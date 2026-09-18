"""
HTTP surface for Post-Mortem: /api/postmortem/*.

Its own router for the same reason the sandbox has one - app.py is already
1300+ lines and this is a whole third mode, not a section of the game. The
shape deliberately mirrors sandbox_api.py: a store keyed by id, ownership
checked in one place, state returned whole on every mutation so the client
never has to reassemble it.

What is different, and why
--------------------------

* **The game is immutable.** There is no endpoint that edits the imported
  moves, and `postmortem_state.PostMortemGame.mainline` is written once at
  import. Branching adds nodes; it can never remove or replace one.
* **AI replies only happen off the game.** `/ai-move` refuses on the mainline
  with a 409. On the real game the next move is not a matter of opinion - it
  is recorded - so having the AI "reply" there would overwrite history with a
  guess. The way forward through the game is `/forward`, which follows the
  game rather than the most recent branch.
* **Analysis is a background scan.** It holds no lock across the whole game;
  each position is one search on the shared engine, awaited on a thread, so a
  live game interleaves rather than queueing behind the scan.

The AI that answers in a branch is `app.decide_ai_move`, injected by
`configure()` exactly as the sandbox's is, so the dependency stays one-way and
a what-if is answered by the same Stockfish-proposes-Gemini-decides path the
rest of the app plays with. A branch answered by a different engine would be
answering a question about a different app.
"""

import asyncio
import logging
import time
from typing import Optional

import chess
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

import postmortem_analysis
import postmortem_state
import learning_events
import move_feedback_log
import turning_point
from gemini_chat_service import GEMINI_POSTMORTEM_CHAT_MODELS, GeminiChatService
from coach_style import CoachStyle
from identity import identity_of
from rate_limit import (
    limit_postmortem_analysis,
    limit_postmortem_chat,
    limit_postmortem_import,
    limit_postmortem_move,
)
from postmortem_state import (
    PgnError,
    SCAN_DONE,
    SCAN_FAILED,
    SCAN_RUNNING,
    SOURCE_HUMAN,
    postmortem_games,
)
from sandbox_state import IllegalSandboxMove
from stockfish_service import stockfish_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/postmortem", tags=["postmortem"])

# The review coach. Its own GeminiChatService instance on its own model chain,
# for the reason every other caller has one: they share a single API key, and
# a chain led by a model somebody else leads with competes for that model's
# quota. It also keeps its own "last model that worked" memory.
postmortem_chat_service = GeminiChatService(models=GEMINI_POSTMORTEM_CHAT_MODELS)

# Injected by app.py at import time - see the module docstring.
_decide_ai_move = None

# What a branch is answered at. Full strength, and not configurable from the
# request: "what would have happened if I had played this instead" is a
# question about best play. A weaker reply would answer a question nobody
# asked, and one the user could not tell apart from the real one.
BRANCH_PROFILE = "master"

# One lock per game, so two AI replies cannot both compute a move for the same
# position and then both apply it. Kept here rather than on the game object so
# postmortem_state stays free of any event-loop dependency.
_game_locks: dict[str, asyncio.Lock] = {}
# In-flight scans, held as strong references because asyncio keeps only weak
# ones and a task nobody holds can be collected mid-flight - which would show
# up as a scan that silently stops at 40%.
_scan_tasks: dict[str, asyncio.Task] = {}


def configure(decide_ai_move) -> None:
    """Hand the router the app's move-decision function."""
    global _decide_ai_move
    _decide_ai_move = decide_ai_move


def _lock_for(game_id: str) -> asyncio.Lock:
    lock = _game_locks.get(game_id)
    if lock is None:
        lock = asyncio.Lock()
        _game_locks[game_id] = lock
    return lock


def _require(game_id: str, http: Request = None):
    """
    This caller's imported game, or 404.

    Ownership checked here rather than per-endpoint so adding a route cannot
    skip it, and a game belonging to somebody else answers exactly as one that
    does not exist - distinguishing them would confirm that a guessed id is
    real. An imported PGN is somebody's own game; it is not a public document
    because its id is unguessable.
    """
    game = postmortem_games.get(game_id)
    if game is None:
        raise HTTPException(
            status_code=404,
            detail="That review is no longer open - import the game again to pick it back up.",
        )
    if http is not None and game.owner is not None and game.owner != identity_of(http):
        logger.warning(f"🔒 Post-mortem {game_id} requested by a different identity - refusing")
        raise HTTPException(
            status_code=404,
            detail="That review is no longer open - import the game again to pick it back up.",
        )
    return game


def _state(game) -> dict:
    """The standard response body: the game's summary plus the move list."""
    payload = game.to_dict()
    payload["moves"] = game.move_rows()
    payload["node"] = game.tree.current.to_dict()
    payload["analysis"] = game.analysis_of(game.tree.current_id)
    return payload


# The same body, for app.py's Play -> Review handoff, which builds a review
# outside this router and must answer with exactly what /import answers.
review_state = _state


# --- import -----------------------------------------------------------------


class ImportRequest(BaseModel):
    # The PGN text itself rather than a file upload: the browser has already
    # read the dropped file to show its name, sending it as JSON keeps the one
    # request shape the rest of this API uses, and it avoids adding a
    # multipart dependency to the image for a few kilobytes of text.
    pgn: str
    source_name: str = "game.pgn"


@router.post("/import", dependencies=[Depends(limit_postmortem_import)])
def import_game(request: ImportRequest, http: Request):
    """
    Turn a PGN into a review.

    Every rejection here is a 400 carrying a sentence written for a chess
    player, because a bad file is a normal thing for a person to do - see
    `postmortem_state.PgnError`. The replay itself is python-chess's, move by
    move, and a PGN that cannot be replayed exactly is refused rather than
    truncated: a half-imported game would put positions on the board that the
    player never actually had.
    """
    identity = identity_of(http)
    returning = learning_events.events.has_prior("pgn_imported", identity)
    started = time.monotonic()
    try:
        game = postmortem_games.create(
            pgn=request.pgn,
            source_name=request.source_name,
            owner=identity,
        )
    except PgnError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except ValueError as exc:
        # MoveTree's own position validation, which is what stands between a
        # hand-written FEN header and a segfaulted engine.
        raise HTTPException(status_code=400, detail=f"That game could not be replayed: {exc}")

    logger.info(
        f"🔍 Post-mortem {game.id} imported: {game.source_name}, "
        f"{len(game.mainline) - 1} plies, result {game.result}"
    )
    import_ms = round((time.monotonic() - started) * 1000)
    learning_events.emit(
        "pgn_imported", identity, game_id=game.id, source_mode="Post-Mortem",
        total_moves=len(game.mainline) - 1, duration_ms=import_ms,
    )
    if returning:
        learning_events.emit(
            "user_returned_with_game", identity, game_id=game.id,
            source_mode="Post-Mortem",
        )
    return _state(game)


@router.get("/game/{game_id}")
def get_game(game_id: str, http: Request):
    return _state(_require(game_id, http))


@router.delete("/game/{game_id}")
def delete_game(game_id: str, http: Request):
    game = _require(game_id, http)
    _cancel_scan(game.id)
    _game_locks.pop(game.id, None)
    postmortem_games.delete(game.id)
    return {"deleted": game.id}


# --- navigation --------------------------------------------------------------


class GotoRequest(BaseModel):
    node_id: str


@router.post("/game/{game_id}/goto")
def goto(game_id: str, request: GotoRequest, http: Request):
    """Jump to any position in the game or in a branch off it."""
    game = _require(game_id, http)
    try:
        game.tree.goto(request.node_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="That move is not part of this review.")
    game.touch()
    return _state(game)


@router.post("/game/{game_id}/back")
def step_back(game_id: str, http: Request):
    game = _require(game_id, http)
    game.tree.back()
    game.touch()
    return _state(game)


@router.post("/game/{game_id}/forward")
def step_forward(game_id: str, http: Request):
    """
    One half-move forward *through the game*.

    Deliberately not `MoveTree.forward()`, which follows the most recently
    created child. That is right for the sandbox, where the newest branch is
    the thing being explored, and wrong here: after branching at move 17,
    stepping forward from move 16 should walk the game the player actually
    played, not divert into their what-if. Inside a branch there is only one
    line to follow, so the tree's own forward is correct there.
    """
    game = _require(game_id, http)
    if game.is_mainline(game.tree.current_id):
        next_id = game.next_mainline_id()
        if next_id is not None:
            game.tree.goto(next_id)
    else:
        game.tree.forward()
    game.touch()
    return _state(game)


@router.post("/game/{game_id}/return")
def return_to_game(game_id: str, http: Request):
    """
    Back to the real game, at the position the branch left from.

    Always available, and it never destroys the branch: the tree keeps it, so
    the same what-if is still there to walk back into. That is the whole
    reason branches are nodes rather than a scratch board.
    """
    game = _require(game_id, http)
    target = game.branch_point() or game.tree.current_id
    game.tree.goto(target)
    game.touch()
    return _state(game)


# --- branching ---------------------------------------------------------------


class BranchRequest(BaseModel):
    move: str


@router.post("/game/{game_id}/branch", dependencies=[Depends(limit_postmortem_move)])
async def branch(game_id: str, request: BranchRequest, http: Request):
    """
    Play a different move from here.

    Legality is decided by `MoveTree.play` against the position itself, so an
    illegal move is a 400 and nothing changes. Playing the move that was
    actually played is not an error and is not a branch - the tree reuses the
    existing child, which walks the game forward instead of duplicating it.

    The move is then analysed at full depth, because this is the one move in
    the review the user chose themselves and the first thing they will ask is
    whether it was any better.
    """
    game = _require(game_id, http)
    identity = identity_of(http)
    board = game.tree.board_at()
    if board.is_game_over():
        raise HTTPException(status_code=409, detail="This position is the end of the line - there is nothing to play.")

    from_id = game.tree.current_id
    try:
        node = game.tree.play(request.move, source=SOURCE_HUMAN)
    except IllegalSandboxMove:
        raise HTTPException(status_code=400, detail="That move is not legal in this position.")
    game.touch()

    # Only if this actually left the game. A move that re-walks the mainline
    # already has its analysis from the scan, and re-running it at probe depth
    # would spend a full-depth search to overwrite a result with itself.
    if not game.is_mainline(node.id) and node.id not in game.analysis:
        try:
            evidence = await asyncio.to_thread(
                postmortem_analysis.analyse_single_ply, board.fen(), node.move
            )
            if evidence is not None:
                evidence["ply"] = game.ply_of(node.id)
                game.record(node.id, evidence)
                move_feedback_log.log_grade(
                    mode="Post-Mortem",
                    fen_before=evidence["fen_before"],
                    move_uci=evidence["uci"],
                    fen_after=evidence.get("fen_after"),
                    san=evidence.get("san"),
                    player_color=evidence.get("color"),
                    quality=evidence.get("quality"),
                    identity=identity,
                    game_id=game.id,
                )
        except Exception as exc:
            # A branch without a grade is still a branch. The board moved,
            # which is what the user asked for; the number is a bonus that the
            # coach and the panel both already handle the absence of.
            logger.warning(f"⚠️ Post-mortem {game_id}: could not analyse the branch move: {exc}")

    payload = _state(game)
    payload["branched_from"] = from_id
    payload["played"] = node.to_dict()
    return payload


@router.post("/game/{game_id}/ai-move", dependencies=[Depends(limit_postmortem_move)])
async def ai_move(game_id: str, http: Request):
    """
    Have the engine answer inside a what-if.

    Refused on the real game with a 409: what happened next there is recorded,
    not decided, and an AI reply would be writing over history. Off the game it
    goes through `app.decide_ai_move` at full strength with `use_learning=False`
    - the same path the app plays with, so the answer to "what would have
    happened" is the app's own best play rather than a second opinion from
    somewhere else, and reviewing a game never touches the player's learning
    history.
    """
    game = _require(game_id, http)
    identity = identity_of(http)
    if _decide_ai_move is None:
        raise HTTPException(status_code=503, detail="Move selection is not configured on this server.")
    if game.is_mainline(game.tree.current_id):
        raise HTTPException(
            status_code=409,
            detail="This is the game as it was played - play a different move first to explore an alternative.",
        )

    started = time.monotonic()
    learning_events.emit(
        "ai_move_generation_started", identity, game_id=game.id,
        source_mode="Post-Mortem", operation="branch_reply",
    )
    async with _lock_for(game_id):
        node = game.tree.current
        board = game.tree.board_at()
        if board.is_game_over():
            raise HTTPException(status_code=409, detail="This line is already over.")
        fen_before = node.fen
        mover = node.turn

        try:
            move, explanation, source, _decision = await _decide_ai_move(
                fen_before,
                mover,
                profile=BRANCH_PROFILE,
                last_move=None,
                use_learning=False,
            )
        except Exception as exc:
            learning_events.emit(
                "ai_move_generation_completed", identity, game_id=game.id,
                source_mode="Post-Mortem", operation="branch_reply",
                duration_ms=round((time.monotonic() - started) * 1000),
                completed=False, error_code=type(exc).__name__,
                error_category="ai_move_generation",
            )
            learning_events.emit(
                "correction_flow_error", identity, game_id=game.id,
                source_mode="Post-Mortem", operation="branch_reply",
                duration_ms=round((time.monotonic() - started) * 1000),
                error_code=type(exc).__name__, error_category="ai_move_generation",
            )
            raise

        # The user may have navigated away while Stockfish and Gemini worked.
        # Applying the move now would attach it to whatever node is current,
        # which is not the position it was chosen for.
        if game.tree.current.fen != fen_before:
            logger.info(f"ℹ️ Post-mortem {game_id}: position changed while thinking - discarding {move}")
            return _state(game)

        try:
            played = game.tree.play(move, source=postmortem_state.SOURCE_AI, explanation=explanation)
        except IllegalSandboxMove as exc:
            raise HTTPException(status_code=500, detail=f"The engine produced an illegal move: {exc}")
        game.touch()

    learning_events.emit(
        "ai_move_generation_completed", identity, game_id=game.id,
        source_mode="Post-Mortem", operation="branch_reply",
        duration_ms=round((time.monotonic() - started) * 1000),
        provider="gemini" if source == "gemini" else "stockfish",
        fallback=source != "gemini", completed=True,
    )

    payload = _state(game)
    payload["selection_source"] = source
    payload["played"] = played.to_dict()
    return payload


# --- analysis ----------------------------------------------------------------


def _cancel_scan(game_id: str) -> None:
    task = _scan_tasks.pop(game_id, None)
    if task is not None and not task.done():
        task.cancel()


async def _run_scan(game) -> None:
    """
    Evaluate the whole game, one position at a time, in the background.

    Runs as a detached task; nothing awaits it. Three properties hold it up:

    * **One position per search, each awaited on its own thread.** The engine
      lock is taken and released per position rather than held for the game,
      so a live player's move interleaves with the scan instead of waiting for
      all of it. A scan makes the app slower; it must not make it unavailable.
    * **Progress is written as it goes**, so the UI has something true to show
      for the minute this takes rather than a spinner and a promise.
    * **Results are addressed by node id.** A late write either finds its own
      move or finds nothing - ids are minted once and never reused - so a scan
      finishing after the user has branched cannot land a grade on the wrong
      move.
    """
    scan = game.scan
    scan.update({"status": SCAN_RUNNING, "analysed": 0, "error": None, "depth": postmortem_analysis.SCAN_DEPTH})
    started = time.monotonic()
    engine_ms = 0.0
    learning_events.emit(
        "engine_analysis_started", game.owner, game_id=game.id,
        source_mode="Post-Mortem", operation="whole_game_scan",
        depth=scan["depth"], total_moves=scan["total"],
    )
    try:
        views = {}
        # Position 0 has no move to grade but is the first point on the eval
        # curve, and it is the "before" of ply 1.
        engine_started = time.monotonic()
        previous = await asyncio.to_thread(postmortem_analysis.evaluate_position, game.start_fen)
        engine_ms += (time.monotonic() - engine_started) * 1000
        views[game.mainline[0]] = previous
        game.start_eval = previous.white_eval

        evidence_list = []
        for ply, node_id in enumerate(game.mainline[1:], start=1):
            node = game.tree.get(node_id)
            engine_started = time.monotonic()
            current = await asyncio.to_thread(postmortem_analysis.evaluate_position, node.fen)
            engine_ms += (time.monotonic() - engine_started) * 1000
            evidence = postmortem_analysis.build_evidence(
                ply, previous, current, chess.Move.from_uci(node.move), node.san
            )
            game.record(node_id, evidence)
            evidence_list.append(evidence)
            move_feedback_log.log_grade(
                mode="Post-Mortem",
                fen_before=evidence["fen_before"],
                move_uci=evidence["uci"],
                fen_after=evidence.get("fen_after"),
                san=evidence.get("san"),
                player_color=evidence.get("color"),
                quality=evidence.get("quality"),
                identity=game.owner,
                game_id=game.id,
            )
            scan["analysed"] = ply
            previous = current

        game.summary = postmortem_analysis.summarise(evidence_list)
        scan["status"] = SCAN_DONE
        learning_events.emit(
            "game_analysis_completed", game.owner, game_id=game.id,
            source_mode="Post-Mortem", duration_ms=round((time.monotonic() - started) * 1000),
            engine_ms=round(engine_ms), depth=scan["depth"],
            analysed_moves=scan["analysed"], total_moves=scan["total"],
            skipped_moves=max(0, scan["total"] - scan["analysed"]),
            completed=True, outcome="completed",
        )
        if game.origin == "play":
            # The Play -> Review loop's own step, so it can be counted apart
            # from reviews of dropped files.
            learning_events.emit(
                "play_game_analysis_completed", game.owner, game_id=game.id,
                source_mode="Play", duration_ms=round((time.monotonic() - started) * 1000),
                analysed_moves=scan["analysed"], total_moves=scan["total"],
                player_color=game.player_color, completed=True, outcome="completed",
            )
        learning_events.emit(
            "engine_analysis_completed", game.owner, game_id=game.id,
            source_mode="Post-Mortem", operation="whole_game_scan",
            duration_ms=round(engine_ms), depth=scan["depth"],
            analysed_moves=scan["analysed"], total_moves=scan["total"],
            completed=True,
        )
        logger.info(f"🔍 Post-mortem {game.id}: scanned {scan['analysed']} plies at depth {scan['depth']}")
    except asyncio.CancelledError:
        # The review was closed. Leave the partial results - they are correct
        # as far as they go - and say plainly that the scan stopped.
        scan["status"] = postmortem_state.SCAN_IDLE
        raise
    except Exception as exc:
        logger.warning(f"⚠️ Post-mortem {game.id}: scan failed at ply {scan['analysed']}: {exc}")
        scan["status"] = SCAN_FAILED
        # Written for the panel, not for a log reader. The partial results are
        # kept: a game analysed to move 20 is still analysed to move 20.
        scan["error"] = "The engine stopped part-way through this game. What was analysed is still shown."
        game.summary = postmortem_analysis.summarise(
            [game.analysis[node_id] for node_id in game.mainline[1:]
             if node_id in game.analysis],
            expected_total=scan["total"],
        )
        learning_events.emit(
            "game_analysis_completed", game.owner, game_id=game.id,
            source_mode="Post-Mortem", duration_ms=round((time.monotonic() - started) * 1000),
            engine_ms=round(engine_ms), depth=scan["depth"],
            analysed_moves=scan["analysed"], total_moves=scan["total"],
            skipped_moves=max(0, scan["total"] - scan["analysed"]),
            completed=False, outcome="failed",
        )
        learning_events.emit(
            "engine_analysis_completed", game.owner, game_id=game.id,
            source_mode="Post-Mortem", operation="whole_game_scan",
            duration_ms=round(engine_ms), depth=scan["depth"],
            analysed_moves=scan["analysed"], total_moves=scan["total"],
            completed=False, error_category="engine_analysis",
        )
        learning_events.emit(
            "correction_flow_error", game.owner, game_id=game.id,
            source_mode="Post-Mortem", operation="game_analysis",
            error_code=type(exc).__name__, error_category="engine_analysis",
        )
    finally:
        _scan_tasks.pop(game.id, None)


@router.post("/game/{game_id}/analyse", dependencies=[Depends(limit_postmortem_analysis)])
async def start_scan(game_id: str, http: Request):
    """
    Start the whole-game scan, or report the one already running.

    Idempotent on purpose: the frontend fires this on import and again if the
    user opens the analysis panel, and a second scan of the same game would be
    a minute of engine time spent computing what is already there.
    """
    game = _require(game_id, http)
    existing = _scan_tasks.get(game_id)
    if existing is not None and not existing.done():
        return {"game_id": game.id, "scan": dict(game.scan)}
    if game.scan["status"] == SCAN_DONE:
        return {"game_id": game.id, "scan": dict(game.scan)}

    learning_events.emit(
        "game_analysis_started", game.owner, game_id=game.id,
        source_mode="Post-Mortem", depth=postmortem_analysis.SCAN_DEPTH,
        total_moves=game.scan["total"],
    )
    task = asyncio.create_task(_run_scan(game))
    _scan_tasks[game_id] = task
    game.touch()
    return {"game_id": game.id, "scan": dict(game.scan)}


@router.get("/game/{game_id}/analysis")
def get_analysis(game_id: str, http: Request):
    """
    Everything the engine has said about this game so far.

    Polled while a scan runs, so it returns the progress alongside the
    results rather than making the client hold two requests in sync.
    """
    game = _require(game_id, http)
    return {
        "game_id": game.id,
        "scan": dict(game.scan),
        "summary": game.summary,
        "moves": game.move_rows(),
        "curve": game.eval_curve(),
    }


@router.get("/game/{game_id}/analysis/{node_id}")
async def get_node_analysis(game_id: str, node_id: str, http: Request):
    """
    The evidence packet for one move, computed at full depth if we do not
    already have it.

    This is the path for a position the user has stopped on - including inside
    a branch, which the scan never touches. It is a single search rather than
    eighty, so it runs at full depth: the number is going to be quoted back to
    them, and this is the one they asked for.
    """
    game = _require(game_id, http)
    try:
        node = game.tree.get(node_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="That move is not part of this review.")
    if node.parent_id is None:
        raise HTTPException(status_code=400, detail="That is the starting position - no move was played to reach it.")

    existing = game.analysis_of(node_id)
    if existing is not None:
        return {"game_id": game.id, "node_id": node_id, "analysis": existing}

    parent = game.tree.get(node.parent_id)
    try:
        evidence = await asyncio.to_thread(
            postmortem_analysis.analyse_single_ply, parent.fen, node.move
        )
    except Exception as exc:
        logger.warning(f"⚠️ Post-mortem {game_id}: analysis of {node_id} failed: {exc}")
        raise HTTPException(status_code=503, detail="The engine could not analyse that position just now.")
    if evidence is None:
        raise HTTPException(status_code=503, detail="The engine could not analyse that position just now.")
    evidence["ply"] = game.ply_of(node_id)
    game.record(node_id, evidence)
    game.touch()
    return {"game_id": game.id, "node_id": node_id, "analysis": evidence}


# --- chat --------------------------------------------------------------------


class ChatRequest(BaseModel):
    message: str
    coach_style: CoachStyle = CoachStyle()


def _score_text(entry: dict) -> str:
    """An engine score as readable text, mates included. See sandbox_api's."""
    mate_in = entry.get("mate_in")
    if mate_in is not None:
        return f"mate in {abs(mate_in)}" + ("" if mate_in > 0 else " against")
    score = entry.get("score")
    return "unknown" if score is None else f"{score / 100:+.2f}"


@router.post("/game/{game_id}/chat", dependencies=[Depends(limit_postmortem_chat)])
async def chat(game_id: str, request: ChatRequest, http: Request):
    """
    Ask the coach about the game.

    The message is answered against a structured evidence packet - the move
    played here, its grade, the engine's preference and its own line, the
    evaluation either side of it, and whether the user is on the game or on a
    what-if. Deliberately NOT the whole PGN handed to a model with "explain
    this": a model given the raw game invents the analysis, and an invented
    centipawn number is exactly the failure this mode would be judged on.

    Its own endpoint and its own transcript for the same reason the sandbox's
    is: /api/chat reads the module-level game and appends to a module-level
    history, so routing a review through it would hand the coach the wrong
    position and mix two conversations.
    """
    game = _require(game_id, http)
    identity = identity_of(http)
    request_started = time.monotonic()
    message = (request.message or "").strip()
    if not message:
        raise HTTPException(status_code=400, detail="Ask the coach something.")

    node = game.tree.current
    board = game.tree.board_at()

    # "Where did I start losing?" - the scan answers, the model explains
    # (turning_point.py). Without a finished scan there is nothing honest to
    # say, so the reply says that and, if the scan never ran, starts it.
    turning = None
    if turning_point.is_question(message):
        if game.scan["status"] != SCAN_DONE:
            if game.scan["status"] == SCAN_RUNNING:
                reply = (f"I need the review analysis first - it is still running "
                         f"({game.scan['analysed']} of {game.scan['total']} moves). Ask again when it finishes.")
            else:
                await start_scan(game.id, http)
                reply = "I need the review analysis first. I have started it - ask again when it finishes."
            game.chat_history.append({"role": "user", "text": message})
            game.chat_history.append({"role": "model", "text": reply})
            game.touch()
            return {"game_id": game.id, "node_id": node.id, "reply": reply, "history": game.chat_history}
        turning = turning_point.select(_scan_evidence(game), game.player_color)

    # The engine's view of the position on screen, so "what should I have
    # played here?" is answered against Stockfish rather than recollection.
    # Refined before it is quoted: get_ranked_moves runs at the shallow
    # ranking depth and its scores are documented as good enough to sort by
    # and not good enough to show.
    alternatives_text = None
    engine_started = time.monotonic()
    learning_events.emit(
        "engine_analysis_started", identity, game_id=game.id,
        source_mode="Post-Mortem", operation="coach_alternatives",
    )
    try:
        ranked = await asyncio.to_thread(stockfish_service.get_ranked_moves, node.fen, 5)
        ranked = await asyncio.to_thread(stockfish_service.refine_candidates, node.fen, ranked)
        parts = []
        for entry in ranked:
            uci = entry.get("move")
            if not uci:
                continue
            try:
                san = board.san(chess.Move.from_uci(uci))
            except (chess.InvalidMoveError, chess.IllegalMoveError, ValueError):
                san = uci
            parts.append(f"{san} ({_score_text(entry)})")
        alternatives_text = ", ".join(parts) or None
        learning_events.emit(
            "engine_analysis_completed", identity, game_id=game.id,
            source_mode="Post-Mortem", operation="coach_alternatives",
            duration_ms=round((time.monotonic() - engine_started) * 1000), completed=True,
        )
    except Exception as exc:
        # A coach that can still talk is better than a 500. The reply loses the
        # engine's ordering, and the persona is told nothing rather than being
        # handed a number to invent around.
        logger.warning(f"⚠️ Post-mortem chat could not rank moves: {exc}")
        learning_events.emit(
            "engine_analysis_completed", identity, game_id=game.id,
            source_mode="Post-Mortem", operation="coach_alternatives",
            duration_ms=round((time.monotonic() - engine_started) * 1000),
            completed=False, error_category="engine_analysis",
        )

    summary_text = None
    if game.summary:
        white = game.summary["white"].get("accuracy")
        black = game.summary["black"].get("accuracy")
        if white is not None or black is not None:
            summary_text = (
                "Decision accuracy (engine-judged moves only): "
                f"White {white if white is not None else '?'}%, "
                f"Black {black if black is not None else '?'}%"
            )

    state = game.to_dict()
    context = {
        "mode": "postmortem",
        "white": game.headers.get("White"),
        "black": game.headers.get("Black"),
        "result": game.result,
        "termination": game.termination.get("detail") or game.termination.get("kind"),
        "fen": node.fen,
        "turn": node.turn,
        "ply": state["ply"],
        "total_plies": state["total_plies"],
        "line_san": game.tree.line_san(),
        # The move that produced the position on screen, with everything the
        # engine established about it. None at the starting position.
        "evidence": game.analysis_of(node.id),
        "alternatives": alternatives_text,
        "on_mainline": state["on_mainline"],
        "branch_ply": state["branch_ply"],
        "branch_line_san": state["branch_line_san"],
        "summary_text": summary_text,
        "coach_style": request.coach_style.model_dump(),
        "turning_point": turning,
    }

    learning_events.emit(
        "llm_request_started", identity, game_id=game.id,
        source_mode="Post-Mortem", operation="review_chat",
    )
    llm_started = time.monotonic()
    success, reply = await postmortem_chat_service.send_message(
        message, game.chat_history, context
    )
    llm_ms = round((time.monotonic() - llm_started) * 1000)
    if not success and turning is not None:
        # The facts are already established; the model only owed the prose.
        logger.warning(f"⚠️ Turning-point explanation unavailable, answering from the scan: {reply}")
        success, reply = True, turning_point.fallback_text(turning)
    if not success:
        # 502: the coach is a downstream service and it failed. The review is
        # fine, and the transcript is left untouched so a retry does not have
        # to reckon with a half-recorded exchange.
        learning_events.emit(
            "correction_flow_error", identity, game_id=game.id,
            source_mode="Post-Mortem", operation="explanation_generation",
            duration_ms=round((time.monotonic() - request_started) * 1000),
            llm_ms=llm_ms, error_category="coach_unavailable",
        )
        learning_events.emit(
            "llm_request_completed", identity, game_id=game.id,
            source_mode="Post-Mortem", operation="review_chat",
            duration_ms=llm_ms, provider="gemini", completed=False,
            error_category="coach_unavailable",
        )
        raise HTTPException(status_code=502, detail=reply)

    learning_events.emit(
        "llm_request_completed", identity, game_id=game.id,
        source_mode="Post-Mortem", operation="review_chat",
        duration_ms=llm_ms, provider="gemini", completed=True,
    )

    game.chat_history.append({"role": "user", "text": message})
    model_turn = {"role": "model", "text": reply}
    if turning is not None:
        model_turn["turning_point"] = turning
    game.chat_history.append(model_turn)
    game.touch()
    learning_events.emit(
        "explanation_rendered", identity, game_id=game.id,
        source_mode="Post-Mortem", operation="coach_reply_ready",
        duration_ms=round((time.monotonic() - request_started) * 1000),
        llm_ms=llm_ms, provider="gemini", completed=True,
    )
    return {
        "game_id": game.id,
        "node_id": node.id,
        "reply": reply,
        "history": game.chat_history,
        "turning_point": turning,
    }


def _scan_evidence(game) -> list[dict]:
    """The mainline's evidence packets with the node ids the browser jumps to."""
    rows = []
    for ply, node_id in enumerate(game.mainline[1:], start=1):
        evidence = game.analysis.get(node_id)
        if evidence:
            rows.append({**evidence, "node_id": node_id, "node_before_id": game.mainline[ply - 1]})
    return rows


@router.delete("/game/{game_id}/chat")
def clear_chat(game_id: str, http: Request):
    """Empty the review's conversation without closing the review."""
    game = _require(game_id, http)
    game.chat_history = []
    return {"game_id": game.id, "history": game.chat_history}


@router.get("/game/{game_id}/chat")
def get_chat(game_id: str, http: Request):
    """The transcript so far, so a remount does not lose the conversation."""
    game = _require(game_id, http)
    return {"game_id": game.id, "history": game.chat_history}
