from pydantic import BaseModel
import os
import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from game_logic import ChessGame
from utils import create_success_response, create_error_response
from config import HOST, DEBUG, ERROR_MESSAGES, SUCCESS_MESSAGES
import chess
from langflow_service import ChessLangflowManager
from stockfish_service import stockfish_service
from langflow_config import langflow_config
from learning_service import learning_service
from gemini_chat_service import gemini_chat_service
from gemini_move_service import gemini_move_service
from move_quality import classify_move, summarize_accuracy
from rate_limit import limit_move, limit_chat, limit_regrade
app = FastAPI(title="Chess AI Platform", version="1.0.0", description="Modern chess game with AI opponent")
# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
# Initialize game components
game = ChessGame()
# Cached evaluation of the current position, from White's perspective, for
# the frontend eval bar. Recomputed once per applied move (not on every
# /api/status poll - that would mean a Stockfish call every second while
# the AI is "thinking", which is both wasteful and slow).
current_eval = {"score": None, "mate_in": None}
def refresh_eval():
    """Recompute current_eval from the game's current position."""
    global current_eval
    try:
        current_eval = stockfish_service.get_position_evaluation(game.get_fen())
    except Exception as e:
        logger.warning(f"⚠️ Failed to refresh position evaluation: {e}")
async def refresh_eval_async():
    """
    Same as refresh_eval(), but off the event loop.

    Every async caller must use this one: a Stockfish search is blocking,
    and running it inline stalls every other request (status polls, move
    grades) for as long as it takes. Sync callers - FastAPI already runs
    those in a worker thread - can keep using refresh_eval() directly.
    """
    global current_eval
    try:
        current_eval = await asyncio.to_thread(stockfish_service.get_position_evaluation, game.get_fen())
    except Exception as e:
        logger.warning(f"⚠️ Failed to refresh position evaluation: {e}")
refresh_eval()
# Which color the human is playing; the AI always plays the other color in
# human_vs_ai mode. Defaults to White (the original behavior). Changed via
# /api/set-color, which also starts a fresh game.
player_color = "white"
# "human_vs_ai" (normal play, one side is the human) or "ai_vs_ai" (both
# sides are AI-controlled, auto-playing against each other). Controlled by
# /api/set-color (always forces human_vs_ai) and the /api/ai-vs-ai/*
# endpoints.
game_mode = "human_vs_ai"
# Only meaningful while game_mode == "ai_vs_ai": whether the auto-play loop
# is actively scheduling moves (True) or paused (False, waiting for a
# manual /api/ai-vs-ai/step or /api/ai-vs-ai/resume).
ai_vs_ai_running = False
# Seconds to wait between AI vs AI moves, so the game is actually watchable
# instead of flashing through instantly.
AI_VS_AI_MOVE_DELAY = 1.5
def get_ai_color() -> str:
    """The color the AI is currently playing, in human_vs_ai mode."""
    return "black" if player_color == "white" else "white"
# UCI of each color's own last AI-played move, keyed by color ("white" /
# "black") - so we can avoid immediately undoing it. In heavily winning
# positions many different candidate moves legitimately tie on evaluation
# (e.g. "opponent is mated in 1 no matter what"), and without this the AI
# can get stuck shuffling a piece back and forth forever between two
# tied-eval moves - a real repetition-draw risk despite being completely
# winning. Tracked per-color (not a single flat value) so AI vs AI mode -
# where both colors are AI-controlled at once - doesn't confuse "my last
# move" with "my opponent's last move" from the ply in between.
last_ai_move_by_color = {"white": None, "black": None}
# Cross-game "learning" state (see learning_service.py) - which persisted
# game row current moves are being logged against, and whether that row's
# already been finalized (so repeated is_game_over() checks after the game
# ends don't try to finalize the same game more than once).
current_game_id = learning_service.start_game(player_color)
game_finalized = False
# Chess.com-style move grading (see move_quality.py). Toggleable because
# grading costs a Stockfish search per half-move on top of the one that
# already refreshes the eval bar - turning it off has to actually stop that
# work, not just hide the badges, which is why this lives on the server
# rather than purely in the frontend.
move_quality_enabled = True
# Bumped on every new game. A grading task started for the previous game
# can still be mid-search when the board is reset, and would otherwise
# write its badge onto whatever move now happens to occupy that ply index.
game_epoch = 0


# Grading runs on its own small thread pool rather than as an asyncio task.
# A task would only start when the event loop next got a turn, which puts
# the player's own grade directly behind the AI's move search - the exact
# reason player badges used to lag ~10s behind the AI's near-instant ones.
# Submitting to a real pool starts the work immediately, in parallel, no
# matter what the loop is doing. Two workers is enough for the worst case
# (both colors graded back to back in AI vs AI).
_grading_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="move-grade")


def schedule_move_quality(ply_index: int, fen_before: str, move_uci: str):
    """
    Grade the half-move just played, in the background.

    Deliberately fire-and-forget: the grade is decoration, so it must never
    delay the move response or the AI's reply. The badge appears a moment
    later via the frontend's normal /api/status polling, the same way AI
    moves already do.
    """
    if not move_quality_enabled:
        return
    _grading_pool.submit(_classify_and_store, ply_index, fen_before, move_uci, game_epoch)


def _classify_and_store(ply_index: int, fen_before: str, move_uci: str, epoch: int):
    """Runs on a _grading_pool worker thread, not the event loop."""
    try:
        quality = classify_move(fen_before, move_uci)
        if quality is None:
            return
        # The board can be reset, or moves can be played, while the search
        # above is running - only attach the grade if this is still the
        # same game and the same move is still sitting at that ply.
        if epoch != game_epoch or not (0 <= ply_index < len(game.game_history)):
            return
        entry = game.game_history[ply_index]
        if entry.get('move') != move_uci:
            return
        entry['quality'] = quality
        # Persisted so grades survive a restart and stay available for
        # review. record_move() numbers plies from 1, hence the +1.
        learning_service.update_move_quality(current_game_id, ply_index + 1, quality)
        logger.info(f"🏅 Move {ply_index + 1} ({move_uci}) graded: {quality['name']}")
    except Exception as e:
        logger.warning(f"⚠️ Move grading task failed for {move_uci}: {e}")


# Progress of a full-game re-grade, surfaced through /api/status so the
# frontend can show how far along it is. `total` of 0 means idle.
regrade_progress = {"running": False, "done": 0, "total": 0}


def _regrade_whole_game(epoch: int):
    """
    Grade every ungraded move in the current game, oldest first.

    Runs on a grading-pool worker. Moves played while grading was switched
    off (or before a restart) otherwise stay blank forever - this is the way
    to fill them in without replaying the game.
    """
    global regrade_progress
    try:
        board = chess.Board()
        # Snapshot the moves up front: the list can grow underneath us if
        # the game continues while the re-grade is running.
        planned = [(i, entry.get('move')) for i, entry in enumerate(list(game.game_history))]
        pending = [(i, uci) for i, uci in planned if uci]
        regrade_progress = {"running": True, "done": 0, "total": len(pending)}
        for ply_index, move_uci in pending:
            if epoch != game_epoch:
                logger.info("ℹ️ Re-grade abandoned - a new game started")
                break
            fen_before = board.fen()
            try:
                move = chess.Move.from_uci(move_uci)
                if move not in board.legal_moves:
                    break
                board.push(move)
            except ValueError:
                break
            if ply_index < len(game.game_history) and not game.game_history[ply_index].get('quality'):
                quality = classify_move(fen_before, move_uci)
                if quality and epoch == game_epoch and ply_index < len(game.game_history):
                    entry = game.game_history[ply_index]
                    if entry.get('move') == move_uci:
                        entry['quality'] = quality
                        learning_service.update_move_quality(current_game_id, ply_index + 1, quality)
            regrade_progress["done"] += 1
        logger.info(f"🏅 Re-grade finished ({regrade_progress['done']}/{regrade_progress['total']})")
    except Exception as e:
        logger.warning(f"⚠️ Re-grade failed: {e}")
    finally:
        regrade_progress = {"running": False, "done": 0, "total": 0}


def build_accuracy_summary() -> dict:
    """
    Per-color accuracy and grade counts for the current game, plus who is
    playing which side so the frontend can label them.
    """
    qualities = [entry.get('quality') for entry in game.game_history]
    white = summarize_accuracy(qualities[0::2])
    black = summarize_accuracy(qualities[1::2])
    return {
        "white": white,
        "black": black,
        "player_color": player_color,
        "regrade": dict(regrade_progress),
    }


def quality_at(ply_index: int):
    """The grade already attached to a ply, if grading finished before the
    move got logged (book moves grade instantly). None otherwise - the grade
    then arrives later via update_move_quality()."""
    if 0 <= ply_index < len(game.game_history):
        return game.game_history[ply_index].get('quality')
    return None


def start_new_learning_game():
    """Call whenever the board is reset/restarted, so subsequent moves log
    against a fresh row instead of appending to the just-finished game."""
    global current_game_id, game_finalized, chat_history, game_epoch
    current_game_id = learning_service.start_game(player_color)
    game_finalized = False
    chat_history = []
    # Invalidate any in-flight move grading from the game just ended.
    game_epoch += 1


def determine_game_result_and_termination(g: ChessGame):
    """Only meaningful once g.is_game_over() is True. Returns
    (result, termination) - result is 'white_win' | 'black_win' | 'draw',
    termination is 'checkmate' | 'stalemate' | 'insufficient_material' |
    '75_move_rule' | 'fivefold_repetition' | 'other'."""
    board = g.board
    if board.is_checkmate():
        winner = "black" if board.turn else "white"
        return f"{winner}_win", "checkmate"
    if board.is_stalemate():
        return "draw", "stalemate"
    if board.is_insufficient_material():
        return "draw", "insufficient_material"
    if board.is_seventyfive_moves():
        return "draw", "75_move_rule"
    if board.is_fivefold_repetition():
        return "draw", "fivefold_repetition"
    return "draw", "other"


def finalize_learning_game_if_over():
    """Idempotent - safe to call after every move. Only actually records a
    result the first time the current game is found to be over."""
    global game_finalized
    if game_finalized or current_game_id is None or not game.board.is_game_over():
        return
    result, termination = determine_game_result_and_termination(game)
    learning_service.finalize_game(current_game_id, result, termination)
    game_finalized = True


def is_reverse_move(move: str, other_move: str) -> bool:
    """True if `move` exactly undoes `other_move` (same squares, reversed)."""
    return (
        len(move) >= 4 and len(other_move) >= 4
        and move[0:2] == other_move[2:4] and move[2:4] == other_move[0:2]
    )
def deprioritize_reversal(ranked_moves: list, last_move) -> list:
    """
    Stable-partition ranked_moves so any move that exactly reverses
    last_move sinks to the bottom - it stays available as a fallback if it's
    truly the only good option, but loses out to any equally-ranked
    alternative that actually makes progress.
    """
    if not last_move:
        return ranked_moves
    non_reversing = [m for m in ranked_moves if not is_reverse_move(m["move"], last_move)]
    reversing = [m for m in ranked_moves if is_reverse_move(m["move"], last_move)]
    return non_reversing + reversing
# Difficulty: 1 (weakest) - DIFFICULTY_MAX (strongest / current
# top-of-the-list behavior). Controls which window of the full
# Stockfish-ranked move list gets sent to Gemini as candidates - see
# select_candidates_by_difficulty() below. Shared by both colors in AI vs AI
# mode (one slider, not a per-side setting).
#
# Window narrowed from 5 to 3 candidates: a tighter quality band per
# difficulty level, so a given setting feels more consistent move to move
# instead of occasionally handing Gemini a wide swing in candidate strength.
# Slider range widened from 10 to 20 steps for finer control - though how
# much of that is actually distinguishable depends on how many legal moves
# exist in the current position (can be well under 20 in an endgame).
DIFFICULTY_MAX = 20
ai_difficulty = DIFFICULTY_MAX
DIFFICULTY_WINDOW_SIZE = 3
# Guards against overlapping AI-move requests - e.g. the background task
# scheduled by /api/move racing a manual /api/ai-move click, both computing
# a move for the same turn. Whichever finishes second used to get rejected
# by game.make_langflow_move() (board already advanced) and reported as an
# "Invalid AI move" even though the move it picked was perfectly legal at
# the moment it was chosen. Serializing on this lock (plus the staleness
# check below) prevents that. Also shared by the AI vs AI loop, so a manual
# action can never race an auto-play move.
ai_move_lock = asyncio.Lock()
# Which backend picks the AI's move from Stockfish's shortlist.
#
# "gemini" (default) calls Gemini's REST API directly - see
# gemini_move_service.py for why the Langflow hop was removed from the
# hosted deployment. "langflow" restores the original path, which is what
# docker-compose.yml sets locally so the Chess flow stays editable in
# Langflow's UI. Both expose the same choose_move_from_candidates().
MOVE_SELECTOR = os.getenv("MOVE_SELECTOR", "gemini").lower()

langflow_manager = None
if MOVE_SELECTOR == "langflow":
    try:
        langflow_manager = ChessLangflowManager(langflow_config)
        logger.info("✅ Langflow manager initialized successfully")
    except Exception as e:
        logger.error(f"❌ Failed to initialize Langflow manager: {e}")
        langflow_manager = None
    move_selector = langflow_manager
else:
    move_selector = gemini_move_service
    logger.info("✅ Move selection calling Gemini directly (Langflow bypassed)")
def select_candidates_by_difficulty(ranked_moves: list, difficulty: int, window_size: int = DIFFICULTY_WINDOW_SIZE) -> list:
    """
    Slide a `window_size`-move window along the full best-to-worst ranked
    move list based on difficulty (1-DIFFICULTY_MAX, currently 1-20).
    difficulty=10 -> window sits at the very top of the list (the best
    moves - today's default/strongest behavior).
    difficulty=1  -> window sits at the very bottom of the list
    (deliberately weak, but still 100% legal, moves).
    Values in between slide the window linearly across the list.
    If there are fewer legal moves than window_size, the whole list is
    returned (nothing to slide across).
    """
    if not ranked_moves:
        return []
    total = len(ranked_moves)
    if total <= window_size:
        return ranked_moves
    # Clamp defensively in case of bad input.
    difficulty = max(1, min(DIFFICULTY_MAX, difficulty))
    max_start = total - window_size
    # difficulty=10 -> fraction 0.0 (top) ; difficulty=1 -> fraction 1.0 (bottom)
    fraction = (DIFFICULTY_MAX - difficulty) / (DIFFICULTY_MAX - 1)
    start = round(fraction * max_start)
    start = max(0, min(start, max_start))
    return ranked_moves[start:start + window_size]
async def decide_ai_move(current_fen: str, moving_color: str):
    """
    Core decision flow:
    1. Stockfish ranks ALL legal moves for the current position, best first.
    2. deprioritize_reversal() sinks any move that would undo
       `moving_color`'s own last move (see last_ai_move_by_color above).
    3. select_candidates_by_difficulty() slices out a window of that list
       based on the current ai_difficulty (DIFFICULTY_MAX = best moves, 1 = weak moves).
    4. Gemini (via Langflow) picks one from that windowed shortlist and
       explains why.
    5. The pick is validated against the candidate list.
    6. If Gemini fails, is unavailable, or hallucinates a move outside the
       shortlist, fall back to the best move WITHIN the current difficulty
       window (not the global Stockfish #1) - this keeps behavior
       difficulty-consistent even when the fallback path is used.
    `moving_color` is "white" or "black" - whichever color is actually
    about to move. Required (not inferred from the board) so callers are
    explicit about who they're deciding for, since in AI vs AI mode both
    colors take this path on alternating turns.
    Returns (move, explanation, source) where source is "gemini" or
    "stockfish_fallback". Raises if Stockfish can't produce any candidates
    at all (i.e. game.get_legal_moves_uci() was already empty upstream, or
    Stockfish itself errors).
    """
    # Off the event loop: this ranks EVERY legal move at depth 15 and takes
    # seconds. Run inline it would freeze the whole server for the duration
    # - no /api/status, no eval bar, and no move grade could be fetched
    # while the AI was thinking, which is what made the player's own badge
    # appear ~10s late while the AI's appeared instantly.
    ranked_moves = await asyncio.to_thread(stockfish_service.get_ranked_moves, current_fen, None)
    ranked_moves = deprioritize_reversal(ranked_moves, last_ai_move_by_color.get(moving_color))
    candidates = select_candidates_by_difficulty(ranked_moves, ai_difficulty, window_size=DIFFICULTY_WINDOW_SIZE)
    candidates = learning_service.reweight_candidates(current_fen, candidates)
    if not candidates:
        raise RuntimeError("Stockfish returned no candidate moves")
    candidate_ucis = [c["move"] for c in candidates]
    window_top_move = candidates[0]["move"]
    if not move_selector:
        logger.info("ℹ️ Move selector unavailable - using top of current difficulty window")
        return window_top_move, "Stockfish-calculated move (move selector unavailable)", "stockfish_fallback"
    try:
        chosen_move, explanation, success = await move_selector.choose_move_from_candidates(
            current_fen, candidates
        )
    except Exception as e:
        logger.warning(f"⚠️ Gemini candidate selection raised an exception, falling back to Stockfish: {e}")
        return window_top_move, f"Stockfish-calculated move (Gemini error: {e})", "stockfish_fallback"
    if success and chosen_move in candidate_ucis:
        logger.info(f"✅ Gemini chose {chosen_move} from {len(candidates)} candidates (difficulty={ai_difficulty})")
        return chosen_move, explanation, "gemini"
    if success and chosen_move not in candidate_ucis:
        logger.warning(f"⚠️ Gemini picked '{chosen_move}', which is not in the candidate list {candidate_ucis} - falling back")
    else:
        logger.warning(f"⚠️ Gemini candidate selection failed ({explanation}) - falling back to Stockfish")
    return window_top_move, f"Stockfish-calculated move (Gemini fallback: {explanation})", "stockfish_fallback"
async def make_ai_move_async():
    """Make AI move in background using the Stockfish-candidates + Gemini-choice flow (human_vs_ai mode)."""
    if ai_move_lock.locked():
        logging.info("⚠️ Background AI move skipped - an AI move is already in progress")
        return
    async with ai_move_lock:
        try:
            if game_mode == "ai_vs_ai":
                logging.info("⚠️ Background AI move skipped - AI vs AI mode is active")
                return
            logging.info("🤖 Background AI move starting...")
            eval_before = dict(current_eval)
            current_fen = game.get_fen()
            legal_moves = game.get_legal_moves_uci()
            current_turn = game.get_current_turn()
            ai_color = get_ai_color()
            if current_turn != ai_color or not legal_moves:
                logging.info(f"⚠️ Background AI move cancelled - turn: {current_turn}, expected: {ai_color}, moves: {len(legal_moves)}")
                return
            try:
                ai_move, explanation, source = await decide_ai_move(current_fen, ai_color)
            except Exception as e:
                logging.error(f"❌ Background AI move decision failed: {e}")
                return
            if game.get_current_turn() != ai_color or game.get_fen() != current_fen:
                logging.info("ℹ️ Board changed while AI was thinking - discarding this move")
                return
            move_result = game.make_langflow_move(ai_move, explanation=explanation)
            if move_result["success"]:
                logging.info(f"✅ Background AI move completed ({source}): {ai_move} - {explanation}")
                ply_index = len(game.game_history) - 1
                san = game.game_history[-1].get('san') if game.game_history else None
                fen_after = game.get_fen()
                schedule_move_quality(ply_index, current_fen, ai_move)
                await refresh_eval_async()
                last_ai_move_by_color[ai_color] = ai_move
            else:
                logging.error(f"❌ Background AI move invalid: {ai_move}")
            if move_result["success"]:
                eval_after = dict(current_eval)
                learning_service.record_move(
                    current_game_id, ply_index + 1, ai_color, "ai",
                    ai_move, san, current_fen, fen_after, eval_before, eval_after,
                    difficulty=ai_difficulty, source=source, explanation=explanation,
                    quality=quality_at(ply_index)
                )
                finalize_learning_game_if_over()
        except Exception as e:
            logging.error(f"❌ Error in background AI move: {e}")
async def make_ai_vs_ai_move_async(chain: bool = True):
    """
    One half-move of AI vs AI auto-play. If `chain` is True and we're still
    running after the move, schedules itself again after a short delay -
    a self-perpetuating loop rather than a fixed-count one, so pausing
    (ai_vs_ai_running = False) or exiting (game_mode changed away from
    "ai_vs_ai") takes effect the moment the *next* move would have been
    scheduled, without needing to cancel an in-flight asyncio task.
    `chain=False` is used by /api/ai-vs-ai/step to play exactly one move
    while paused, without kicking off further auto-play.
    """
    if ai_move_lock.locked():
        logging.info("⚠️ AI vs AI move skipped - a move is already in progress")
        return
    moved = False
    async with ai_move_lock:
        try:
            if game_mode != "ai_vs_ai":
                return
            if chain and not ai_vs_ai_running:
                # Auto-play was paused between being scheduled and running - bail quietly.
                return
            current_fen = game.get_fen()
            legal_moves = game.get_legal_moves_uci()
            current_turn = game.get_current_turn()
            eval_before = dict(current_eval)
            if not legal_moves or game.is_game_over():
                logging.info("🏁 AI vs AI game over - stopping auto-play")
                return
            try:
                ai_move, explanation, source = await decide_ai_move(current_fen, current_turn)
            except Exception as e:
                logging.error(f"❌ AI vs AI move decision failed: {e}")
                return
            if game.get_fen() != current_fen:
                logging.info("ℹ️ Board changed unexpectedly during AI vs AI move - discarding")
                return
            move_result = game.make_langflow_move(ai_move, explanation=explanation)
            if move_result["success"]:
                logging.info(f"✅ AI vs AI move completed ({source}, {current_turn}): {ai_move} - {explanation}")
                ply_index = len(game.game_history) - 1
                san = game.game_history[-1].get('san') if game.game_history else None
                fen_after = game.get_fen()
                schedule_move_quality(ply_index, current_fen, ai_move)
                await refresh_eval_async()
                last_ai_move_by_color[current_turn] = ai_move
                eval_after = dict(current_eval)
                learning_service.record_move(
                    current_game_id, ply_index + 1, current_turn, "ai",
                    ai_move, san, current_fen, fen_after, eval_before, eval_after,
                    difficulty=ai_difficulty, source=source, explanation=explanation,
                    quality=quality_at(ply_index)
                )
                finalize_learning_game_if_over()
                moved = True
            else:
                logging.error(f"❌ AI vs AI move invalid: {ai_move}")
                return
        except Exception as e:
            logging.error(f"❌ Error in AI vs AI move: {e}")
            return
    if moved and chain and game_mode == "ai_vs_ai" and ai_vs_ai_running and not game.is_game_over():
        await asyncio.sleep(AI_VS_AI_MOVE_DELAY)
        asyncio.create_task(make_ai_vs_ai_move_async())
# Add CORS middleware
# nginx serves the frontend and proxies /api/* from the same origin, so the
# browser never actually needs CORS here. allow_origins=["*"] was a local-dev
# convenience that, on a public URL, invites any other site to drive this
# server's Gemini-backed endpoints on a visitor's behalf. (It was also invalid
# as written - browsers reject a "*" origin combined with credentials.)
# ALLOWED_ORIGINS is a comma-separated override for anything cross-origin.
_allowed_origins = [
    o.strip() for o in os.getenv(
        "ALLOWED_ORIGINS",
        "http://localhost:3000,http://localhost:5173"
    ).split(",") if o.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
# Pydantic models
class MoveRequest(BaseModel):
    move: str
class DifficultyRequest(BaseModel):
    difficulty: int
class ColorRequest(BaseModel):
    color: str
class ChatRequest(BaseModel):
    message: str
class MoveQualityRequest(BaseModel):
    enabled: bool


# In-memory mid-game chat transcript: list of {"role": "user"|"model", "text": str},
# oldest first. Not persisted (unlike learning_service's SQLite log) - it's
# scoped to the current game only, and gets cleared alongside it in
# start_new_learning_game(). Fine to lose on a server restart.
chat_history = []


@app.post("/api/chat", dependencies=[Depends(limit_chat)])
async def chat_with_ai(request: ChatRequest):
    """Mid-game chat: ask the AI about the current position, its reasoning,
    what it expects you to play, etc. Calls Gemini directly (see
    gemini_chat_service.py) - the Langflow "Chess" flow is untouched by
    this endpoint."""
    try:
        user_message = request.message
        if not user_message or not user_message.strip():
            return create_error_response("Message required", {"message": "message must not be empty"})
        ai_color = get_ai_color()
        last_ai_explanation = None
        if game.game_history:
            last_ai_explanation = game.game_history[-1].get("explanation")
        game_context = {
            "fen": game.get_fen(),
            "move_history_san": [m.get("san") for m in game.game_history if m.get("san")],
            "player_color": player_color,
            "ai_color": ai_color,
            "difficulty": ai_difficulty,
            "is_game_over": game.is_game_over(),
            "last_ai_explanation": last_ai_explanation,
            "learning_context": learning_service.get_prompt_context_summary(),
        }
        success, reply = await gemini_chat_service.send_message(user_message, chat_history, game_context)
        if not success:
            return create_error_response("Chat request failed", {"message": reply})
        chat_history.append({"role": "user", "text": user_message})
        chat_history.append({"role": "model", "text": reply})
        return create_success_response("Chat reply received", {"reply": reply, "history": chat_history})
    except Exception as e:
        return create_error_response("Failed to process chat message", details={"error": str(e)})


# Chess Game Endpoints
@app.get("/")
def index():
    """API info"""
    return {
        "message": "Chess AI Platform Backend",
        "version": "1.0.0",
        "description": "Modern chess game with AI opponent",
        "endpoints": {
            "game_status": "/api/status",
            "make_move": "/api/move",
            "ai_move": "/api/ai-move",
            "reset_game": "/api/reset",
            "set_color": "/api/set-color",
            "ai_vs_ai_start": "/api/ai-vs-ai/start",
            "ai_vs_ai_pause": "/api/ai-vs-ai/pause",
            "ai_vs_ai_resume": "/api/ai-vs-ai/resume",
            "ai_vs_ai_step": "/api/ai-vs-ai/step",
            "ai_vs_ai_exit": "/api/ai-vs-ai/exit",
            "langflow_init": "/api/langflow/initialize",
            "get_difficulty": "/api/difficulty",
            "set_difficulty": "/api/difficulty",
            "learning_summary": "/api/learning/summary",
            "chat": "/api/chat",
            "get_move_quality": "/api/move-quality",
            "set_move_quality": "/api/move-quality",
            "regrade_game": "/api/move-quality/regrade"
        }
    }
@app.post("/api/move", dependencies=[Depends(limit_move)])
async def make_move(request: MoveRequest):
    """Make player move and get AI response"""
    try:
        if game_mode == "ai_vs_ai":
            return create_error_response("AI vs AI mode is active", {
                "message": "Exit AI vs AI mode before making a manual move"
            })
        player_move = request.move
        logging.info(f"🔄 Received move request: {player_move}")
        if not player_move:
            raise HTTPException(status_code=400, detail=ERROR_MESSAGES['move_required'])
        if game.get_current_turn() != player_color:
            return create_error_response("Not your turn", {
                "current_turn": game.get_current_turn(),
                "player_color": player_color
            })
        mover_color = game.get_current_turn()
        fen_before = game.get_fen()
        eval_before = dict(current_eval)
        result = game.make_player_move(player_move)
        logging.info(f"📥 Player move result: {result}")
        if not result['success']:
            return result
        # Snapshot the ply number, SAN and resulting position NOW, before any
        # await. Since the eval refresh stopped blocking the event loop, the
        # AI's background move can land while we're suspended on it - reading
        # len(game.game_history) afterwards then yields the AI's ply, not
        # ours, which corrupted the move log (duplicate/skipped ply numbers)
        # and made the grade update target the wrong row.
        ply_index = len(game.game_history) - 1
        san = game.game_history[-1].get('san') if game.game_history else None
        fen_after = game.get_fen()
        # Kicked off before the eval refresh, not after: grading only needs
        # the position and the move, both of which are already known here,
        # so starting it first lets it run alongside the eval search instead
        # of queueing behind it - a second off how fast the badge shows up.
        schedule_move_quality(ply_index, fen_before, player_move)
        await refresh_eval_async()
        learning_service.record_move(
            current_game_id, ply_index + 1, mover_color, "human",
            player_move, san, fen_before, fen_after, eval_before, dict(current_eval),
            quality=quality_at(ply_index)
        )
        finalize_learning_game_if_over()
        if game.board.is_game_over():
            return create_success_response('Game over!', {
                'game_over': True,
                'status': game.get_game_status(),
                'player_move': result,
                'eval': current_eval
            })
        if game.get_current_turn() == get_ai_color():
            logging.info(f"🤖 It's {get_ai_color()}'s turn - scheduling AI move in background")
            asyncio.create_task(make_ai_move_async())
            model_result = {
                'success': True,
                'message': 'AI move scheduled in background',
                'ai_scheduled': True
            }
        else:
            logging.info(f"⚪ It's {player_color}'s turn - no AI move needed")
            model_result = {
                'success': False,
                'message': f"{player_color.capitalize()}'s turn - player moves next",
                'skip_ai': True
            }
        response_data = {
            'player_move': result,
            'model_move': model_result,
            'status': game.get_game_status(),
            'eval': current_eval
        }
        return create_success_response('Move processed', response_data)
    except Exception as e:
        return create_error_response('Failed to process move', details={'error': str(e)})
@app.get("/api/reset")
def reset_game():
    """Reset the current game. Keeps the current player_color (so hitting
    Reset while playing as Black stays as Black); always drops back to
    human_vs_ai mode if AI vs AI was active, since the Reset Game button
    isn't shown during AI vs AI anyway."""
    global last_ai_move_by_color, game_mode, ai_vs_ai_running
    try:
        game.reset_game()
        refresh_eval()
        last_ai_move_by_color = {"white": None, "black": None}
        game_mode = "human_vs_ai"
        ai_vs_ai_running = False
        start_new_learning_game()
        return create_success_response(SUCCESS_MESSAGES['game_reset'], {
            'eval': current_eval,
            'player_color': player_color,
            'game_mode': game_mode
        })
    except Exception as e:
        return create_error_response('Failed to reset game', details={'error': str(e)})
@app.post("/api/set-color")
async def set_color(request: ColorRequest):
    """Start a fresh game with the human playing the requested color. If the
    human picks Black, White (the AI) moves first - scheduled the same way
    a normal AI turn is scheduled after any human move."""
    global player_color, last_ai_move_by_color, game_mode, ai_vs_ai_running
    try:
        color = request.color.lower()
        if color not in ("white", "black"):
            return create_error_response("Invalid color", {
                "message": "color must be 'white' or 'black'",
                "received": request.color
            })
        player_color = color
        game_mode = "human_vs_ai"
        ai_vs_ai_running = False
        game.reset_game()
        await refresh_eval_async()
        last_ai_move_by_color = {"white": None, "black": None}
        start_new_learning_game()
        ai_scheduled = False
        if game.get_current_turn() == get_ai_color():
            logger.info(f"🎨 Player set to {player_color} - AI ({get_ai_color()}) moves first")
            asyncio.create_task(make_ai_move_async())
            ai_scheduled = True
        return create_success_response(f"Playing as {player_color}", {
            "player_color": player_color,
            "game_mode": game_mode,
            "ai_scheduled": ai_scheduled,
            "status": game.get_game_status(),
            "history": game.game_history,
            "eval": current_eval,
            "difficulty": ai_difficulty
        })
    except Exception as e:
        return create_error_response("Failed to set color", details={"error": str(e)})
@app.post("/api/ai-vs-ai/start")
async def ai_vs_ai_start():
    """Start a fresh AI vs AI game and begin auto-play."""
    global game_mode, ai_vs_ai_running, last_ai_move_by_color
    try:
        game.reset_game()
        await refresh_eval_async()
        last_ai_move_by_color = {"white": None, "black": None}
        start_new_learning_game()
        game_mode = "ai_vs_ai"
        ai_vs_ai_running = True
        asyncio.create_task(make_ai_vs_ai_move_async())
        return create_success_response("AI vs AI started", {
            "game_mode": game_mode,
            "ai_vs_ai_running": ai_vs_ai_running,
            "status": game.get_game_status(),
            "history": game.game_history,
            "eval": current_eval
        })
    except Exception as e:
        return create_error_response("Failed to start AI vs AI", details={"error": str(e)})
@app.post("/api/ai-vs-ai/pause")
def ai_vs_ai_pause():
    """Pause AI vs AI auto-play. The in-flight move (if any) still finishes;
    it's the *next* move that gets skipped."""
    global ai_vs_ai_running
    ai_vs_ai_running = False
    return create_success_response("Paused", {"ai_vs_ai_running": ai_vs_ai_running})
@app.post("/api/ai-vs-ai/resume")
async def ai_vs_ai_resume():
    """Resume AI vs AI auto-play after a pause."""
    global ai_vs_ai_running
    try:
        if game_mode != "ai_vs_ai":
            return create_error_response("Not in AI vs AI mode", {"game_mode": game_mode})
        if game.is_game_over():
            return create_error_response("Game is already over", {})
        ai_vs_ai_running = True
        asyncio.create_task(make_ai_vs_ai_move_async())
        return create_success_response("Resumed", {"ai_vs_ai_running": ai_vs_ai_running})
    except Exception as e:
        return create_error_response("Failed to resume AI vs AI", details={"error": str(e)})
@app.post("/api/ai-vs-ai/step")
async def ai_vs_ai_step():
    """Play exactly one AI-vs-AI move without chaining to the next one -
    used while paused, to step through a game move by move."""
    try:
        if game_mode != "ai_vs_ai":
            return create_error_response("Not in AI vs AI mode", {"game_mode": game_mode})
        if ai_vs_ai_running:
            return create_error_response("Already auto-playing", {"message": "Pause first to step manually"})
        if game.is_game_over():
            return create_error_response("Game is already over", {})
        await make_ai_vs_ai_move_async(chain=False)
        return create_success_response("Step complete", {
            "status": game.get_game_status(),
            "history": game.game_history,
            "eval": current_eval
        })
    except Exception as e:
        return create_error_response("Failed to step", details={"error": str(e)})
@app.post("/api/ai-vs-ai/exit")
def ai_vs_ai_exit():
    """Leave AI vs AI mode and return to normal human-vs-AI play (keeps the
    current player_color from before AI vs AI was started)."""
    global game_mode, ai_vs_ai_running
    game_mode = "human_vs_ai"
    ai_vs_ai_running = False
    return create_success_response("Exited AI vs AI mode", {
        "game_mode": game_mode,
        "player_color": player_color
    })
@app.get("/api/status")
def get_status():
    """Get current game status"""
    try:
        return create_success_response('Status retrieved', {
            'status': game.get_game_status(),
            # Full history (not just the last few moves) - the frontend move
            # history panel renders the whole game, not a recent window.
            'history': game.game_history,
            'difficulty': ai_difficulty,
            'eval': current_eval,
            'player_color': player_color,
            'game_mode': game_mode,
            'ai_vs_ai_running': ai_vs_ai_running,
            # So a page refresh doesn't wipe the visible chat transcript -
            # chat_history is already kept server-side (see /api/chat),
            # cleared alongside every new game. Loaded back into the chat
            # box on mount by ChessBoard.tsx's initializeGame().
            'chat_history': chat_history,
            # Whether move grading is running. The badges themselves ride
            # along on each history entry's 'quality' key, filled in
            # asynchronously (see schedule_move_quality) - so a move can
            # appear here for a poll or two before its grade does.
            'move_quality_enabled': move_quality_enabled,
            # Per-color accuracy, grade counts and re-grade progress for the
            # Review panel. Derived from the same history above, so it can
            # never disagree with the badges on screen.
            'accuracy': build_accuracy_summary()
        })
    except Exception as e:
        return create_error_response('Failed to get status', details={'error': str(e)})


@app.post("/api/move-quality/regrade", dependencies=[Depends(limit_regrade)])
def regrade_game():
    """
    Grade every move in the current game that doesn't have a grade yet.

    Needed because grades are only produced as moves are played: anything
    played while the toggle was off, or before the server last restarted,
    would otherwise stay permanently blank.
    """
    try:
        if regrade_progress["running"]:
            return create_error_response("Re-grade already running", {
                "message": "A full-game re-grade is already in progress",
                "progress": dict(regrade_progress)
            })
        ungraded = sum(1 for e in game.game_history if not e.get('quality'))
        if ungraded == 0:
            return create_success_response("Nothing to re-grade", {
                "queued": 0,
                "message": "Every move in this game is already graded"
            })
        _grading_pool.submit(_regrade_whole_game, game_epoch)
        logger.info(f"🏅 Re-grade queued for {ungraded} ungraded move(s)")
        return create_success_response("Re-grade started", {"queued": ungraded})
    except Exception as e:
        return create_error_response("Failed to start re-grade", details={"error": str(e)})


@app.get("/api/move-quality")
def get_move_quality_enabled():
    """Whether chess.com-style move grading is currently switched on."""
    try:
        return create_success_response("Move quality setting retrieved", {
            "enabled": move_quality_enabled
        })
    except Exception as e:
        return create_error_response("Failed to get move quality setting", details={"error": str(e)})


@app.post("/api/move-quality")
def set_move_quality_enabled(request: MoveQualityRequest):
    """
    Turn move grading on or off.

    Switching it off stops the extra Stockfish search per half-move; grades
    already attached to earlier moves are left in place, so flipping it back
    on doesn't lose them (though moves played while it was off stay
    ungraded - they'd need a re-analysis of the whole game to fill in).
    """
    global move_quality_enabled
    try:
        move_quality_enabled = bool(request.enabled)
        logger.info(f"🏅 Move quality grading {'enabled' if move_quality_enabled else 'disabled'}")
        return create_success_response("Move quality setting updated", {
            "enabled": move_quality_enabled
        })
    except Exception as e:
        return create_error_response("Failed to set move quality setting", details={"error": str(e)})
@app.get("/api/learning/summary")
def get_learning_summary():
    """Live opponent-profile + AI self-history summary for the frontend's
    Learning panel (see learning_service.py)."""
    try:
        return create_success_response("Learning summary retrieved", learning_service.get_learning_summary())
    except Exception as e:
        return create_error_response("Failed to get learning summary", details={"error": str(e)})


@app.get("/api/langflow/initialize")
async def initialize_langflow():
    """Initialize Langflow connection"""
    try:
        return create_success_response("Langflow initialized successfully", {
            "status": "initialized",
            "flows_created": True
        })
    except Exception as e:
        return create_error_response("Error initializing Langflow", {
            "error": str(e)
        })
@app.get("/api/difficulty")
def get_difficulty():
    """Get the current AI difficulty (1-20)"""
    try:
        return create_success_response("Difficulty retrieved", {
            "difficulty": ai_difficulty
        })
    except Exception as e:
        return create_error_response("Failed to get difficulty", details={"error": str(e)})
@app.post("/api/difficulty")
def set_difficulty(request: DifficultyRequest):
    """Set the AI difficulty (1-20). 20 = strongest, 1 = weakest."""
    global ai_difficulty
    try:
        new_difficulty = request.difficulty
        if not isinstance(new_difficulty, int) or new_difficulty < 1 or new_difficulty > DIFFICULTY_MAX:
            return create_error_response("Invalid difficulty", {
                "message": f"difficulty must be an integer between 1 and {DIFFICULTY_MAX}",
                "received": new_difficulty
            })
        ai_difficulty = new_difficulty
        logger.info(f"🎚️ AI difficulty set to {ai_difficulty}")
        return create_success_response("Difficulty updated", {
            "difficulty": ai_difficulty
        })
    except Exception as e:
        return create_error_response("Failed to set difficulty", details={"error": str(e)})
@app.post("/api/ai-move", dependencies=[Depends(limit_move)])
async def make_ai_move():
    """Manually trigger AI move using the Stockfish-candidates + Gemini-choice flow (human_vs_ai mode)."""
    if game_mode == "ai_vs_ai":
        return create_error_response("AI vs AI mode is active", {
            "message": "Use the AI vs AI controls (pause/step/resume) instead"
        })
    if ai_move_lock.locked():
        return create_error_response("AI is already thinking", {
            "message": "An AI move is already in progress - please wait a moment and try again"
        })
    async with ai_move_lock:
        try:
            logger.info("🤖 Manual AI move requested")
            eval_before = dict(current_eval)
            current_fen = game.get_fen()
            legal_moves = game.get_legal_moves_uci()
            current_turn = game.get_current_turn()
            ai_color = get_ai_color()
            logger.info(f"🎯 AI will play for {current_turn}")
            if current_turn != ai_color:
                return create_error_response("Not AI's turn", {
                    "current_turn": current_turn,
                    "message": f"AI plays as {ai_color}"
                })
            if not legal_moves:
                return create_error_response("No legal moves available", {
                    "fen": current_fen,
                    "game_over": game.is_game_over()
                })
            # Get Stockfish's full ranked list, window it by difficulty, then let Gemini choose from it
            try:
                ai_move, explanation, source = await decide_ai_move(current_fen, ai_color)
            except Exception as e:
                logger.error(f"❌ AI move decision failed: {e}")
                return create_error_response("AI move generation failed", {
                    "error": str(e),
                    "fen": current_fen,
                    "legal_moves": legal_moves
                })
            if game.get_current_turn() != ai_color or game.get_fen() != current_fen:
                logger.info("ℹ️ Board changed while AI was thinking - move no longer applies")
                return create_success_response("Board already advanced", {
                    "message": "The position changed while the AI was thinking (an auto-move likely already completed) - no action needed",
                    "game_state": {
                        "fen": game.get_fen(),
                        "turn": game.get_current_turn(),
                        "is_game_over": game.is_game_over(),
                        "is_check": game.is_in_check(),
                        "is_checkmate": game.is_checkmate(),
                        "is_stalemate": game.is_stalemate(),
                        "legal_moves": game.get_legal_moves_uci()
                    }
                })
            move_result = game.make_langflow_move(ai_move, explanation=explanation)
            if move_result["success"]:
                logger.info(f"✅ Manual AI move completed ({source}): {ai_move} - {explanation}")
                ply_index = len(game.game_history) - 1
                san = game.game_history[-1].get('san') if game.game_history else None
                fen_after = game.get_fen()
                schedule_move_quality(ply_index, current_fen, ai_move)
                await refresh_eval_async()
                last_ai_move_by_color[ai_color] = ai_move
                learning_service.record_move(
                    current_game_id, ply_index + 1, ai_color, "ai",
                    ai_move, san, current_fen, fen_after, eval_before, dict(current_eval),
                    difficulty=ai_difficulty, source=source, explanation=explanation,
                    quality=quality_at(ply_index)
                )
                finalize_learning_game_if_over()
                return create_success_response(f"AI played {ai_move}", {
                    "ai_move": ai_move,
                    "reasoning": explanation,
                    "source": source,
                    "difficulty": ai_difficulty,
                    "eval": current_eval,
                    "history": game.game_history,
                    "game_state": {
                        "fen": game.get_fen(),
                        "turn": game.get_current_turn(),
                        "is_game_over": game.is_game_over(),
                        "is_check": game.is_in_check(),
                        "is_checkmate": game.is_checkmate(),
                        "is_stalemate": game.is_stalemate(),
                        "legal_moves": game.get_legal_moves_uci()
                    }
                })
            else:
                return create_error_response(f"Invalid AI move: {ai_move}", {
                    "ai_move": ai_move,
                    "error": move_result.get("message", "Unknown error")
                })
        except Exception as e:
            logger.error(f"❌ Error in manual AI move: {e}")
            return create_error_response("Error making AI move", {
                "error": str(e)
            })
if __name__ == "__main__":
    LOCAL_PORT = 8080
    print("🚀 Starting Chess AI Platform...")
    print(f"🌐 Server available at: http://localhost:{LOCAL_PORT}")
    print(f"📚 API docs available at: http://localhost:{LOCAL_PORT}/docs")
    print(f"🔗 Connecting to Langflow at: {langflow_config.url}")
    print(f"♟️ Chess AI Platform v1.0.0")
    import uvicorn
    uvicorn.run("app:app", host=HOST, port=LOCAL_PORT, reload=DEBUG)
