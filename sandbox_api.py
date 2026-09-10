"""
HTTP surface for Sandbox Learner Mode: /api/sandbox/*.

Kept in its own module and mounted as an APIRouter so app.py grows by two
lines rather than another few hundred. app.py is already 1100+ lines and the
frontend's ChessBoard.tsx is a 1800-line monolith; the sandbox is a whole
second mode and should not be folded into either.

How this avoids a circular import
---------------------------------

The sandbox deliberately reuses `decide_ai_move` from app.py rather than
reimplementing it. Gemini choosing from Stockfish's shortlist is the
product, and a demonstration mode that quietly used a different selection
path would be demonstrating something the app does not actually do.

But app.py imports this module to mount the router, so this module cannot
import app.py back. Instead app.py calls `configure(decide_ai_move)` at
startup and the function is held here. That keeps the dependency one-way
and leaves the sandbox trivially testable with a fake decider.

Isolation guarantees (decision 5: this is intended to go public)
---------------------------------------------------------------

* Every endpoint is addressed by `session_id`; there is no ambient "current
  sandbox". Two browser tabs get two independent trees.
* Nothing here touches `game`, `player_color`, `game_mode`, `ai_difficulty`,
  `current_game_id` or `game_epoch`.
* AI moves are decided with `use_learning=False`, so the cross-game learning
  DB is neither read from nor written to by a sandbox demonstration.
* Stockfish is shared - it is a stateless-per-call service behind its own
  lock - but nothing else is.
"""

import asyncio
import logging
from typing import Optional

import chess
import engine_evidence
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

import sandbox_state
from identity import identity_of
from rate_limit import (
    limit_alternatives,
    limit_eval,
    limit_sandbox_chat,
    limit_sandbox_move,
    limit_scenario,
)
import scenario_service as scenario_module
from gemini_chat_service import GEMINI_SANDBOX_CHAT_MODELS, GeminiChatService
from gemini_narration_service import gemini_narration_service
from scenario_service import ScenarioError, generate_scenario
from sandbox_state import (
    IllegalSandboxMove,
    NARRATION_FAILED,
    NARRATION_PENDING,
    NARRATION_READY,
    sandbox_sessions,
)
from stockfish_service import stockfish_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/sandbox", tags=["sandbox"])

# The coach chat. A separate GeminiChatService instance from the real game's,
# on its own model chain (GEMINI_SANDBOX_CHAT_MODELS), so a busy sandbox and a
# busy game cannot starve each other of one model's quota - and so the
# "try the last model that worked first" memory is per-caller.
sandbox_chat_service = GeminiChatService(models=GEMINI_SANDBOX_CHAT_MODELS)

# Injected by app.py at import time - see the module docstring.
_decide_ai_move = None

# One asyncio lock per session, so two AI-move requests for the same session
# cannot both compute a move for the same position and then both try to
# apply it. Kept here rather than on SandboxSession so sandbox_state stays
# free of any event-loop dependency and can be unit-tested without one.
_session_locks: dict[str, asyncio.Lock] = {}

# In-flight narration tasks per session. Held as strong references because
# asyncio only keeps weak ones - a task nobody holds can be garbage
# collected mid-flight, which shows up as narration that silently never
# arrives. Also lets a deleted session cancel its own pending work instead
# of leaving it to finish into a tree nobody will read.
_narration_tasks: dict[str, set] = {}
# How many engine-ranked alternatives to offer the narrator as contrast.
NARRATION_ALTERNATIVES = 4


def configure(decide_ai_move) -> None:
    """Hand the router the app's move-decision function."""
    global _decide_ai_move
    _decide_ai_move = decide_ai_move


def _lock_for(session_id: str) -> asyncio.Lock:
    lock = _session_locks.get(session_id)
    if lock is None:
        lock = asyncio.Lock()
        _session_locks[session_id] = lock
    return lock


def _require(session_id: str, http: Request = None):
    """
    This caller's session, or 404.

    Ownership is checked here rather than in each endpoint so that adding a
    route cannot accidentally skip it. A session belonging to somebody else
    answers exactly as a session that does not exist does - same status, same
    message - because distinguishing them would confirm to a stranger that an
    id they guessed is real.

    A session with no owner (constructed directly in the pure tests, or
    created before this check existed) is reachable by anyone, which keeps
    those tests meaningful without weakening the deployed path: every session
    the router creates now has an owner.
    """
    session = sandbox_sessions.get(session_id)
    if session is None:
        # 404 rather than 400: an expired session is the normal way this
        # happens (sessions are swept after an idle TTL), and the frontend
        # should treat it as "start a new one", not "you sent bad input".
        raise HTTPException(status_code=404, detail=f"No such sandbox session: {session_id}")
    if http is not None and session.owner is not None:
        if session.owner != identity_of(http):
            logger.warning(f"🔒 Sandbox session {session_id} requested by a different identity - refusing")
            raise HTTPException(status_code=404, detail=f"No such sandbox session: {session_id}")
    return session


async def _run_narration(session, node_id: str, context: dict) -> None:
    """
    Generate narration for one already-played move and attach it.

    Runs as a detached task. Nothing awaits it, so it must never raise into
    the void and must never assume the tree still looks the way it did when
    it started - the user may have rewound, branched, or reset in the
    meantime. `attach_narration` is keyed on the node id and simply returns
    False if that node is gone, which is the whole reason results are
    addressed by id rather than by ply index (see sandbox_state's docstring).
    """
    try:
        # The engine's opinion of the alternatives, for contrast. This uses
        # the cheap stage-1 ranking depth (STOCKFISH_RANK_DEPTH, default 10)
        # rather than full search depth: it is only here to give the
        # narrator something concrete to compare against, and it shares the
        # engine lock with real move selection, so it should stay cheap.
        try:
            ranked = await asyncio.to_thread(
                stockfish_service.get_ranked_moves, context["fen_before"], NARRATION_ALTERNATIVES
            )
            # Label them in SAN. get_ranked_moves speaks UCI, but a coach
            # says "Nf3", not "g1f3", and the narration is read aloud to a
            # student looking at a board.
            board = chess.Board(context["fen_before"])
            for entry in ranked:
                try:
                    entry["san"] = board.san(chess.Move.from_uci(entry["move"]))
                except Exception:
                    pass
            context["alternatives"] = ranked
        except Exception as e:
            # Narration without alternatives is still worth having.
            logger.warning(f"⚠️ Narration alternatives unavailable: {e}")

        success, text = await gemini_narration_service.narrate(context)
        attached = session.tree.attach_narration(
            node_id, text if success else None, NARRATION_READY if success else NARRATION_FAILED
        )
        if not attached:
            logger.info(f"ℹ️ Narration for {node_id} discarded - node no longer in the tree")
        elif not success:
            logger.warning(f"⚠️ Narration failed for {node_id}: {text}")
    except asyncio.CancelledError:
        raise
    except Exception as e:
        # A background task must not take anything down with it.
        logger.warning(f"⚠️ Narration task for {node_id} errored: {e}")
        session.tree.attach_narration(node_id, None, NARRATION_FAILED)


def _start_narration(session, node) -> None:
    """
    Fire narration for `node` without waiting for it.

    This is the parallelism decision made concrete: the endpoint that played
    the move returns as soon as the move exists, with the node marked
    `pending`, and the narration lands on it whenever it arrives. Narration
    for one ply therefore overlaps with move selection for the next, and
    never adds to move latency. Making this serial - awaiting the call here
    - would stack a full Gemini round trip on top of every move.
    """
    if not session.narration_enabled:
        return
    if not gemini_narration_service.available:
        # Leave the status at "none" rather than "failed": nothing broke,
        # narration simply isn't configured, and the frontend should render
        # that as absence rather than as an error.
        return
    parent_id = node.parent_id
    context = {
        "move": node.move,
        "san": node.san,
        "mover": node.mover,
        "source": node.source,
        "fen_before": session.tree.get(parent_id).fen if parent_id else node.fen,
        "fen_after": node.fen,
        "line_san": session.tree.line_san(node.id)[:-1],
        "explanation": node.explanation,
        "title": session.title,
        "is_check": "+" in (node.san or "") or "#" in (node.san or ""),
        "is_game_over": session.tree.board_at(node.id).is_game_over(),
    }
    node.narration_status = NARRATION_PENDING
    task = asyncio.create_task(_run_narration(session, node.id, context))
    tasks = _narration_tasks.setdefault(session.id, set())
    tasks.add(task)
    task.add_done_callback(tasks.discard)


def _cancel_narration(session_id: str) -> None:
    for task in _narration_tasks.pop(session_id, set()):
        task.cancel()


def _state(session) -> dict:
    """The standard response body: session summary plus the whole tree."""
    payload = session.to_dict()
    payload["tree"] = session.tree.to_dict()
    payload["node"] = session.tree.current.to_dict()
    return payload


class CreateSessionRequest(BaseModel):
    start_fen: Optional[str] = None
    difficulty: int = 20
    narration_enabled: bool = True
    title: str = "Sandbox"


class MoveRequest(BaseModel):
    move: str
    source: str = sandbox_state.SOURCE_HUMAN
    # Off by default for the student's own moves. The demonstration is what
    # gets narrated; narrating every move the user tries while exploring
    # would double the load on the shared API key for commentary they did
    # not ask for. Set true for "tell me what you think of my move".
    narrate: bool = False


class GotoRequest(BaseModel):
    node_id: str


class ResetRequest(BaseModel):
    """
    Restart the demonstration. Every field is optional, and every field left
    out keeps whatever the session already has.

    This deliberately does *not* reuse CreateSessionRequest. That model
    exists to build a session out of nothing, so its `difficulty` defaults
    to 20 and its `narration_enabled` to True - which meant a bare
    `POST /reset {}` silently re-pointed a difficulty-6 "easy king and pawn
    endings" session at full-strength play. A reset restarts the line; it
    does not re-create the session.
    """

    start_fen: Optional[str] = None
    difficulty: Optional[int] = None
    narration_enabled: Optional[bool] = None
    # Renaming matters when start_fen is given: a session opened by /scenario
    # carries that scenario's title, and resetting it onto a different
    # position leaves the title describing a board that is no longer there.
    title: Optional[str] = None


class ScenarioRequest(BaseModel):
    prompt: str
    # Both optional overrides. Difficulty is normally inferred from the
    # request itself ("give me something hard"), but an explicit value from
    # a slider should win over the model's guess.
    difficulty: Optional[int] = None
    narration_enabled: bool = True


async def _evaluate_for_scenario(fen: str) -> int:
    """
    Centipawns from White's point of view, for scenario vetting.

    Uses the shallow stage-1 ranking depth rather than full search depth:
    this only has to answer "is one side clearly better here", which does
    not need depth 15, and it runs several times per request.
    """
    ranked = await asyncio.to_thread(stockfish_service.get_ranked_moves, fen, 1)
    if not ranked:
        return 0
    top = ranked[0]
    mate_in = top.get("mate_in")
    if mate_in is not None:
        # A forced mate has score=None, so it has to be mapped onto the
        # centipawn scale explicitly or it reads as a dead-level position -
        # which would reject exactly the decisive scenarios we want.
        score = 100000 if mate_in > 0 else -100000
    else:
        score = top.get("score") or 0
    # get_ranked_moves scores from the side to move's point of view.
    return score if chess.Board(fen).turn else -score


@router.post("/session")
def create_session(request: CreateSessionRequest, http: Request):
    """
    Start an isolated sandbox session, optionally from a custom position.

    `start_fen` is validated by MoveTree via `board.is_valid()`. That check
    matters more here than it looks: move_quality.py documents that
    Stockfish *segfaults* on unreachable positions rather than rejecting
    them, so an invalid FEN reaching the engine takes the shared engine
    process down for the real game too. Reject it at the door.
    """
    try:
        session = sandbox_sessions.create(
            start_fen=request.start_fen,
            difficulty=request.difficulty,
            narration_enabled=request.narration_enabled,
            title=request.title,
            owner=identity_of(http),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    logger.info(f"🧪 Sandbox session {session.id} created ({session.title})")
    return _state(session)


@router.post("/scenario", dependencies=[Depends(limit_scenario)])
async def create_scenario(request: ScenarioRequest, http: Request):
    """
    Natural language in, a session on a validated custom position out.

    Decision 3 was that scenario setup is language only - no buttons - so
    this takes the request verbatim and does the interpreting server-side.

    The position is never a FEN Gemini wrote. Gemini produces structured
    constraints; scenario_service builds the board with python-chess and
    validates it, and only then does it reach a session - the same door
    `POST /session` uses, for the same reason: an invalid position handed
    to the shared Stockfish process segfaults it, and that process is shared
    with the real game.
    """
    if not request.prompt or not request.prompt.strip():
        raise HTTPException(status_code=400, detail="Describe the scenario you want.")

    # A pasted FEN or PGN goes nowhere near Gemini.
    #
    # This is the route the honest-failure message points at ("paste a FEN or
    # a PGN"), and that message is only honest because this exists. It is also
    # simply the better path when it applies: the student already HAS the
    # position, so there is nothing to interpret and nothing to get wrong -
    # python-chess parses it, python-chess validates it, and a bad paste comes
    # back saying which of the two it tried and what was wrong with it.
    pasted = request.prompt.strip()
    if scenario_module.looks_like_fen(pasted) or scenario_module.looks_like_pgn(pasted):
        try:
            board, notes = scenario_module.build_from_pasted(pasted)
        except ScenarioError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        scenario = {
            "fen": board.fen(),
            "title": "Your position",
            # Deliberately flat. Everywhere else this field is written by a
            # model BEFORE the position exists (§16's remaining P3), and it
            # over-claims for exactly that reason. Here the position came from
            # the student, so there is nothing to claim about it and the
            # honest description is what happened.
            "description": notes,
            "notes": notes,
            "side_to_move": "white" if board.turn else "black",
            "difficulty": 12,
            "favor_met": None,
            "mate_in": None,
        }
    else:
        try:
            scenario = await generate_scenario(request.prompt, evaluator=_evaluate_for_scenario)
        except ScenarioError as exc:
            # 400, not 500: almost every failure here is "that request can't be
            # turned into a position" (an opening we don't know, impossible
            # material, a middlegame this cannot construct honestly), which is
            # the user's to act on rather than a server fault.
            raise HTTPException(status_code=400, detail=str(exc))

    try:
        session = sandbox_sessions.create(
            start_fen=scenario["fen"],
            difficulty=request.difficulty or scenario["difficulty"],
            narration_enabled=request.narration_enabled,
            title=scenario["title"],
            owner=identity_of(http),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    logger.info(
        f"🧪 Sandbox scenario '{scenario['title']}' -> session {session.id} "
        f"({scenario['notes']})"
    )
    # The coach chat is told what the student asked to study. A scenario
    # always opens a NEW session, so its chat transcript starts empty - which
    # is exactly the "clear on a new scenario" behaviour, achieved by the
    # session boundary rather than by remembering to wipe anything.
    session.scenario_description = scenario["description"]

    payload = _state(session)
    payload["scenario"] = {
        "title": scenario["title"],
        "description": scenario["description"],
        "notes": scenario["notes"],
        "side_to_move": scenario["side_to_move"],
        # The two facts that were CHECKED against the position, as opposed to
        # the description, which the model wrote before the position existed.
        # favor_met is None when no side was asked to be winning, False when
        # one was and the built position does not deliver it - which the
        # student needs to be told, because the description will be promising
        # a win that is not there.
        "favor_met": scenario.get("favor_met"),
        "mate_in": scenario.get("mate_in"),
        "prompt": request.prompt,
    }
    return payload


@router.get("/session/{session_id}")
def get_session(session_id: str, http: Request):
    return _state(_require(session_id, http))


@router.delete("/session/{session_id}")
def delete_session(session_id: str, http: Request):
    # Ownership first: without this, deletion is the one operation a stranger
    # could still perform on somebody else's session by id alone. _require
    # raises the same 404 a missing session would.
    _require(session_id, http)
    existed = sandbox_sessions.delete(session_id)
    _session_locks.pop(session_id, None)
    _cancel_narration(session_id)
    if not existed:
        raise HTTPException(status_code=404, detail=f"No such sandbox session: {session_id}")
    return {"success": True, "session_id": session_id}


@router.post("/session/{session_id}/reset")
def reset_session(session_id: str, request: ResetRequest, http: Request):
    """
    Drop the tree and start again, optionally at a new position or strength.

    Two defaults here are load-bearing:

    * **The position falls back to this session's own root, not the standard
      opening.** `MoveTree(None)` builds a fresh `chess.Board()`, so passing
      an absent `start_fen` straight through dumped a student who hit
      "restart" on a generated rook endgame back onto move one of a normal
      game - silently discarding the scenario they had asked for.
    * **Difficulty falls back to the session's current value.** See
      ResetRequest for why this is not CreateSessionRequest.

    This is also the only way to change a session's strength, which is why
    it applies `difficulty` at all: it previously accepted the field and
    ignored it, so the frontend's difficulty control had nothing to call.
    """
    session = _require(session_id, http)
    # The old tree is about to be dropped; any narration still in flight is
    # for nodes that will no longer exist.
    _cancel_narration(session_id)
    start_fen = request.start_fen or session.tree.get(session.tree.root_id).fen
    try:
        session.reset(start_fen)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if request.difficulty is not None:
        # Clamped to the same 1-20 range app.py's slider uses - a sandbox
        # difficulty is handed straight to select_candidates_by_difficulty.
        session.difficulty = max(1, min(20, int(request.difficulty)))
    if request.narration_enabled is not None:
        session.narration_enabled = request.narration_enabled
    if request.title is not None:
        title = request.title.strip()
        if title:
            session.title = title[:120]
    return _state(session)


@router.post("/session/{session_id}/move", dependencies=[Depends(limit_sandbox_move)])
async def play_move(session_id: str, request: MoveRequest, http: Request):
    """
    Play a move into the tree - this is the user taking over the board.

    Per decision 2, doing this from a node the AI has already continued from
    does not overwrite anything: it creates a sibling branch and makes it
    current. The AI's original line stays in the tree, reachable by /goto.
    """
    session = _require(session_id, http)
    try:
        node = session.tree.play(request.move, source=request.source)
    except IllegalSandboxMove as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if node.mover:
        session.last_move_by_color[node.mover] = node.move
    if request.narrate:
        _start_narration(session, node)
    session.touch()
    return _state(session)


@router.post("/session/{session_id}/ai-move", dependencies=[Depends(limit_sandbox_move)])
async def ai_move(session_id: str, http: Request):
    """
    Have the AI play one half-move from the current node.

    Routed through app.decide_ai_move with the session's own difficulty and
    reversal state, and with `use_learning=False` so a demonstration never
    reads or biases the player's real learning history.
    """
    session = _require(session_id, http)
    if _decide_ai_move is None:
        raise HTTPException(status_code=503, detail="Sandbox move selection is not configured")

    async with _lock_for(session_id):
        # Re-read inside the lock: a queued request may have been waiting
        # while the position moved on.
        node = session.tree.current
        board = session.tree.board_at()
        if board.is_game_over():
            raise HTTPException(status_code=409, detail="This line is already over")
        fen_before = node.fen
        mover = node.turn

        move, explanation, source = await _decide_ai_move(
            fen_before,
            mover,
            difficulty=session.difficulty,
            last_move=session.last_move_by_color.get(mover),
            use_learning=False,
        )

        # The tree could have been rewound while the engine and Gemini were
        # working (the user hit "back" mid-think). Applying the move then
        # would attach it to whatever node is current now, which is not the
        # position it was chosen for.
        if session.tree.current.fen != fen_before:
            logger.info(f"ℹ️ Sandbox {session_id}: position changed while thinking - discarding {move}")
            return _state(session)

        try:
            new_node = session.tree.play(move, source=sandbox_state.SOURCE_AI, explanation=explanation)
        except IllegalSandboxMove as exc:
            raise HTTPException(status_code=500, detail=f"AI produced an illegal move: {exc}")
        session.last_move_by_color[mover] = move
        # Fired inside the lock so the node cannot be rewound away between
        # creating it and scheduling its narration, but never awaited - the
        # response below does not wait for Gemini.
        _start_narration(session, new_node)
        session.touch()

    payload = _state(session)
    payload["selection_source"] = source
    payload["played"] = new_node.to_dict()
    return payload


@router.post("/session/{session_id}/goto")
def goto(session_id: str, request: GotoRequest, http: Request):
    """Rewind or fast-forward to any node in the tree."""
    session = _require(session_id, http)
    try:
        session.tree.goto(request.node_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    session.touch()
    return _state(session)


@router.post("/session/{session_id}/back")
def step_back(session_id: str, http: Request):
    session = _require(session_id, http)
    session.tree.back()
    session.touch()
    return _state(session)


@router.post("/session/{session_id}/forward")
def step_forward(session_id: str, http: Request):
    session = _require(session_id, http)
    session.tree.forward()
    session.touch()
    return _state(session)


@router.get("/session/{session_id}/narration/{node_id}")
def get_narration(session_id: str, node_id: str, http: Request):
    """
    Poll one node's narration.

    Deliberately small: because narration is fired in parallel and arrives
    after the move, the frontend needs something cheap to poll while the
    board is already showing the move. Refetching the whole tree every
    second to discover one string would grow with the length of the
    demonstration.

    `status` is one of: none (not requested, or narration isn't configured),
    pending (in flight), ready, failed.
    """
    session = _require(session_id, http)
    try:
        node = session.tree.get(node_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return {
        "session_id": session.id,
        "node_id": node.id,
        "status": node.narration_status,
        "narration": node.narration,
    }


@router.get("/session/{session_id}/narration")
def get_all_narration(session_id: str, http: Request):
    """Narration for every node on the current line, oldest first."""
    session = _require(session_id, http)
    return {
        "session_id": session.id,
        "line": [
            {
                "node_id": n.id,
                "san": n.san,
                "mover": n.mover,
                "status": n.narration_status,
                "narration": n.narration,
            }
            for n in session.tree.path_to()
            if n.move is not None
        ],
    }


@router.get("/session/{session_id}/alternatives", dependencies=[Depends(limit_alternatives)])
async def alternatives(session_id: str, http: Request, top_n: int = 5):
    """
    What else could be played here, and what has already been tried.

    This is the data behind "why didn't you play X instead?". `ranked` is
    Stockfish's view of the current position; `explored` is the set of moves
    already played from this node in the tree, each with the AI's own stated
    reason - so the answer can contrast the engine's opinion with what the
    AI actually said at the time.
    """
    session = _require(session_id, http)
    node = session.tree.current
    ranked = await asyncio.to_thread(stockfish_service.get_ranked_moves, node.fen, top_n)
    ranked = await asyncio.to_thread(stockfish_service.refine_candidates, node.fen, ranked)
    return {
        "session_id": session.id,
        "node_id": node.id,
        "fen": node.fen,
        "turn": node.turn,
        "ranked": ranked,
        "explored": [
            {
                "move": c.move,
                "san": c.san,
                "node_id": c.id,
                "source": c.source,
                "explanation": c.explanation,
            }
            for c in session.tree.children_of(node.id)
        ],
    }


# Both of these moved to engine_evidence.py the moment Play's mid-game chat
# needed the same rendering (§P0: the LLM must not invent move quality, and it
# cannot quote the engine without being handed it). They keep their old private
# names here so every call site in this file goes on working, and so there is
# one implementation of "what does the engine say, in words" rather than two
# free to drift.
_score_text = engine_evidence.score_text
_line_text = engine_evidence.line_text


@router.get("/session/{session_id}/eval", dependencies=[Depends(limit_eval)])
async def evaluate(session_id: str, http: Request):
    """
    The position's evaluation, for Learner Mode's eval bar.

    Deliberately its own endpoint rather than a field on every state response.
    This is a full-depth search sharing one engine lock with move selection, so
    computing it on every state read would slow down a demonstration for a
    number that is switched off by default and that most people watching a
    coached line never turn on. It is fetched only while the bar is showing.

    White's absolute perspective, matching the real game's bar: positive is
    good for White regardless of whose turn it is. A bar that flipped meaning
    with the side to move would be unreadable, which is why
    get_position_evaluation fixes the point of view rather than using the
    side-to-move convention the ranked-move scores use.
    """
    session = _require(session_id, http)
    node = session.tree.current
    try:
        result = await asyncio.to_thread(
            stockfish_service.get_position_evaluation, node.fen
        )
    except Exception as exc:
        # A missing bar is a missing bar. It is an optional readout on a
        # position the student can already see, and failing the request would
        # put an error strip over a working board.
        logger.warning(f"⚠️ Sandbox eval failed for {session_id}: {exc}")
        raise HTTPException(status_code=503, detail="The engine could not evaluate this position.")
    return {
        "session_id": session.id,
        # Which node it describes, so a reply that lands after the student has
        # already moved on can be discarded instead of labelling the new
        # position with the old one's number.
        "node_id": node.id,
        "score": result["score"],
        "mate_in": result["mate_in"],
    }


class ClassifyRequest(BaseModel):
    message: str


@router.post("/classify", dependencies=[Depends(limit_sandbox_chat)])
async def classify(request: ClassifyRequest):
    """
    Is this message a question about the board, or a request for a new one?

    Learner Mode has one composer that does both jobs, so something has to
    decide which was meant. That decision is made here rather than in the
    frontend because the only reliable way to tell "give me something easier"
    from "why was that easier for white?" is to read the sentence, and the
    frontend has no model to read it with.

    Deliberately stateless and session-free: it looks at the sentence alone.
    Whether acting on a BUILD is destructive depends on how many moves are on
    the board, and that is the caller's business - this endpoint says what was
    meant, not what should happen next.

    **Failure means ASK.** If Gemini is unreachable, rate-limited or returns
    something unparseable, this returns "ask" with 200 rather than an error.
    Answering a build request as though it were a question is a mildly odd
    reply; treating a question as a build request offers to destroy a line the
    student is studying, and sandbox sessions have no undo. The asymmetry is
    the whole reason this endpoint has no error path.
    """
    message = (request.message or "").strip()
    if not message:
        raise HTTPException(status_code=400, detail="Nothing to classify.")

    success, reply = await sandbox_chat_service.send_message(
        message, [], {"mode": "intent"}
    )
    if not success:
        logger.warning(f"⚠️ Intent classification unavailable, defaulting to ask: {reply}")
        return {"intent": "ask", "classified": False}

    # The instruction asks for one bare word. Models mostly comply and
    # sometimes decorate: "BUILD - they want a new position" and "**BUILD**"
    # are both verdicts, and the second one is common enough that a parser
    # which only stripped a leading asterisk read it as ASK - the safe
    # direction, but wrong, and wrong in a way that quietly made the feature
    # look broken. Keep the letters of the first token and nothing else.
    first = reply.strip().split()[0] if reply.strip() else ""
    token = "".join(ch for ch in first if ch.isalpha()).upper()
    return {"intent": "build" if token == "BUILD" else "ask", "classified": True}


class SandboxChatRequest(BaseModel):
    message: str


@router.post("/session/{session_id}/chat", dependencies=[Depends(limit_sandbox_chat)])
async def chat(session_id: str, request: SandboxChatRequest, http: Request):
    """
    Ask the coach about the position on the board.

    This is Learner Mode's equivalent of the real game's /api/chat, and it is
    deliberately a separate endpoint rather than a mode flag on that one.
    /api/chat reads the module-level `game` and appends to a module-level
    `chat_history`; routing sandbox questions through it would put sandbox
    state into the real game's transcript and hand the coach the wrong
    position - breaking the isolation the whole sandbox is built on.

    The transcript lives on the session, so two people in two sandboxes never
    see each other's conversation, and neither touches the real game's.
    """
    session = _require(session_id, http)
    message = (request.message or "").strip()
    if not message:
        raise HTTPException(status_code=400, detail="Ask the coach something.")

    node = session.tree.current
    board = session.tree.board_at()

    # The engine's ranking is what makes "why not Nf3?" answerable against
    # something real rather than the model's own recollection. It is the same
    # data the removed "Why not?" panel listed.
    #
    # Refined, not raw: get_ranked_moves runs at the shallow RANK_DEPTH and
    # its scores are documented as good enough to sort by and NOT good enough
    # to show - handing those numbers to Gemini would be quoting a figure the
    # engine does not stand behind.
    alternatives_text = None
    best_line_text = None
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
        # The engine's own continuation after its best move, in SAN.
        #
        # Without this the coach was handed a list of first moves and left to
        # calculate the rest itself, which is exactly what it is worst at: on
        # a verified mate in two it named the right first move and then a
        # second move that did not mate. Stockfish had already computed the
        # whole line to produce the score it reported; this is that line.
        if ranked:
            best_line_text = _line_text(board, ranked[0])
    except Exception as exc:
        # A coach that can still talk is better than a 500. The reply just
        # loses the engine's ordering, which the model is told nothing about
        # rather than being given a number it might invent around.
        logger.warning(f"⚠️ Sandbox chat could not rank moves: {exc}")

    explored = session.tree.children_of(node.id)
    context = {
        "mode": "sandbox",
        "fen": node.fen,
        "turn": node.turn,
        "line_san": session.tree.line_san(),
        "difficulty": session.difficulty,
        "scenario_description": session.scenario_description,
        "alternatives": alternatives_text,
        "best_line": best_line_text,
        "last_explanation": node.explanation,
        "last_narration": node.narration,
        "is_game_over": board.is_game_over(),
        "explored_here": [{"san": c.san, "explanation": c.explanation} for c in explored],
    }

    success, reply = await sandbox_chat_service.send_message(
        message, session.chat_history, context
    )
    if not success:
        # 502: the coach is a downstream service and it failed. The session is
        # fine, and the transcript is left untouched so a retry does not have
        # to reckon with a half-recorded exchange.
        raise HTTPException(status_code=502, detail=reply)

    session.chat_history.append({"role": "user", "text": message})
    session.chat_history.append({"role": "model", "text": reply})
    session.touch()
    return {
        "session_id": session.id,
        "node_id": node.id,
        "reply": reply,
        "history": session.chat_history,
    }


@router.get("/session/{session_id}/chat")
def get_chat(session_id: str, http: Request):
    """The transcript so far, so a remount does not lose the conversation."""
    session = _require(session_id, http)
    return {"session_id": session.id, "history": session.chat_history}
