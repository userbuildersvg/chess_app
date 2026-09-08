from pydantic import BaseModel
import asyncio
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from game_logic import ChessGame
from utils import create_success_response, create_error_response
from config import HOST, DEBUG, ERROR_MESSAGES, SUCCESS_MESSAGES
import chess
from langflow_service import ChessLangflowManager
from stockfish_service import stockfish_service
from langflow_config import langflow_config
import db
import email_service
import profile_api
import profile_service
import profile_worker
from learning_service import LearningService
from gemini_chat_service import gemini_chat_service
from gemini_move_service import gemini_move_service
from move_quality import classify_move, summarize_accuracy
from rate_limit import limit_move, limit_chat, limit_regrade
from gemini_narration_service import gemini_narration_service
from scenario_service import scenario_service
import learning_loop_api
import postmortem_api
import sandbox_api
import auth_api
import beta_api
import beta_service
from beta_gate import BetaGateMiddleware
from auth_service import accounts_enabled, auth_service
from identity import IdentityMiddleware, identity_of, is_guest, is_production
from player_state import player_sessions
# Interactive API docs: on locally, off on a public host.
#
# /docs and /openapi.json do not leak the Gemini key - nothing does, that was
# checked - but they do publish a complete map of every endpoint and its
# schema to anyone who asks, including the ones that spend the key on each
# call. That is a convenience worth having on a laptop and an invitation on a
# public URL, so it follows the deployment rather than being a constant.
#
# ENABLE_DOCS overrides in either direction, for the case where they are
# genuinely wanted on a deployed instance.
_docs_env = os.environ.get("ENABLE_DOCS")
DOCS_ENABLED = (_docs_env.lower() == "true") if _docs_env is not None else not is_production()

app = FastAPI(
    title="Chess AI Platform",
    version="1.0.0",
    description="Modern chess game with AI opponent",
    docs_url="/docs" if DOCS_ENABLED else None,
    redoc_url="/redoc" if DOCS_ENABLED else None,
    # Without this the schema stays readable at /openapi.json even with the
    # docs page gone, which would make hiding the page decorative.
    openapi_url="/openapi.json" if DOCS_ENABLED else None,
)
# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# httpx logs every request at INFO as "HTTP Request: POST <full url> ...", and
# the Gemini REST URL carries the API key as a ?key= query parameter. At INFO
# that writes the real key in plaintext into the backend log - locally into
# /tmp/backend.log, and on Render into the hosted log stream, where it is
# retained and readable by anyone with dashboard access. Nothing in our own
# code prints the key; this is purely httpx's default request logging. Raising
# its level to WARNING keeps genuine transport failures visible while dropping
# the per-request line that leaks the credential.
logging.getLogger("httpx").setLevel(logging.WARNING)


# How often the guest-retention sweep runs. Once a day: the thing being
# deleted is 30 days old, so the difference between sweeping hourly and daily
# is nothing anyone can observe, and a daily pass costs one query.
RETENTION_SWEEP_INTERVAL = float(os.environ.get("RETENTION_SWEEP_INTERVAL_SECONDS", 24 * 60 * 60))


async def _retention_loop():
    """
    Delete unclaimed guest history, once a day, for as long as we are up.

    An asyncio task rather than a scheduler, a worker or a cron container.
    What has to happen is one DELETE with a WHERE clause, and the cost of
    missing a day is that data lives one day longer - so the reliability bar
    is "runs eventually", which a loop in the process that owns the database
    clears without adding a moving part. If this ever needs to be exactly
    once across many instances, that is the moment to move it out, and not
    before: there is one instance.

    Never raises. A failing sweep must not take the server with it; the log
    line is the alert, and the next pass tries again.
    """
    while True:
        try:
            removed = await asyncio.to_thread(
                LearningService.purge_unclaimed_guest_games)
            if removed:
                logger.info(f"🧹 Retention sweep removed {removed} unclaimed guest game(s)")
            # Expired reset tokens ride the same sweep rather than getting a
            # loop of their own: both are "delete rows nobody can use any
            # more", both are cheap, and one timer is one thing to reason
            # about.
            stale = await asyncio.to_thread(auth_service.purge_expired_resets)
            if stale:
                logger.info(f"🧹 Retention sweep removed {stale} expired reset token(s)")
        except Exception as e:
            logger.warning(f"⚠️ Retention sweep failed (will retry): {e}")
        await asyncio.sleep(RETENTION_SWEEP_INTERVAL)


@app.on_event("startup")
async def _startup_email():
    """
    Say plainly whether password-reset email works.

    The reset endpoint is deliberately silent about mail failures - it has to
    be, or it becomes a way to test which addresses are registered. That makes
    a broken mail configuration invisible from outside, so it gets said here
    instead, once, at the only moment somebody is watching.
    """
    state = email_service.status()
    if state["enabled"]:
        logger.info(f"✉️ Password reset email: Mailjet, sending as {email_service.from_email()}")
    elif state["reason"] == "disabled_by_config":
        logger.warning(
            "✉️ Password reset email is OFF (EMAIL_ENABLED=false). Accounts work; "
            "a forgotten password has no recovery path until this is turned on."
        )
    else:
        logger.warning(
            "✉️ Password reset email is NOT CONFIGURED (missing: %s). Accounts work; "
            "a forgotten password has no recovery path, and /api/auth/forgot-password "
            "says so rather than pretending. See DEPLOY.md."
            % ", ".join(state.get("missing", []))
        )


@app.on_event("startup")
async def _startup_database():
    """
    Bring the schema up to date, and say loudly if there is no database.

    Migrating on boot rather than from a deploy hook: there is one process and
    one database, the migrations are idempotent, and a deploy step that can be
    forgotten is a deploy step that will be. `db.migrate()` is a no-op on an
    up-to-date database.

    A missing or unreachable database is logged as an ERROR and the app still
    starts. That is deliberate rather than lazy: without a database this
    server can still play chess - every learning write is already written to
    degrade to "records nothing" - and refusing to boot would turn a storage
    outage into a total outage. What must not happen is starting silently, so
    the failure is loud, and /api/health reports it.
    """
    # Before anything else, and regardless of DATABASE_URL: are the migration
    # files even in this build? A production image once shipped without them
    # (Dockerfile.backend copied *.py and nothing else), and the failure was
    # invisible - `schema_migrations` was created, no migration ran, the log
    # said "Schema up to date", and the app served a database with no tables.
    # A build that cannot describe its own schema is not one to boot, so this
    # raises rather than degrading.
    db.assert_migrations_present()

    if not db.configured():
        logger.error(
            "❌ DATABASE_URL is not set. Accounts and cross-game learning cannot "
            "be stored; play still works. Set it to the Neon pooled connection string."
        )
        return
    try:
        await asyncio.to_thread(db.migrate)
    except db.MigrationsMissing:
        # Cannot happen after the assertion above, but a swallowed one here
        # would restore exactly the silence this fix exists to remove.
        raise
    except Exception as e:
        logger.error(f"❌ Database is configured but unreachable or un-migratable: {e}")
        return
    asyncio.create_task(_retention_loop())

    # The Improvement Profile's background scan (profile_worker.py). Started
    # here rather than lazily on the first import, so a queue left behind by a
    # previous process starts draining the moment this one is up.
    #
    # requeue_stuck() first, and it is the line that makes the free instance's
    # idle spin-down a non-event: a row still marked `analysing` belongs to a
    # process that no longer exists, because there is exactly one worker and it
    # has not started yet. Without this those rows would sit unclaimed forever
    # and the person waiting on them would see "analysing" and no progress.
    try:
        await asyncio.to_thread(profile_service.requeue_stuck)
    except Exception as e:
        logger.warning(f"⚠️ Could not requeue interrupted profile analyses: {e}")
    asyncio.create_task(profile_worker.loop())


@app.on_event("shutdown")
def _shutdown_engine():
    """Release the two long-lived resources when the server stops.

    The engine is long-lived rather than opened per call, so it has to be
    closed explicitly - otherwise a reload leaves an orphaned Stockfish
    holding its hash allocation, which matters on a memory-capped host.

    The Postgres pool is the same shape of problem: uvicorn's reloader
    restarts the process without exiting it, so waiting for db.py's atexit
    hook would leave a reload's worth of connections held against Neon's
    budget on every code change.
    """
    stockfish_service.close()
    db.close_pool()
# ---------------------------------------------------------------------------
# WHOSE GAME IS THIS?
#
# Everything below used to be module-level: one board, one colour, one eval,
# one difficulty, for the whole server. That was fine for one person on one
# laptop and wrong for anything else - two browser tabs already shared a game -
# and it is the reason accounts could not simply be bolted on. Signing in would
# have given every user their own name and the same chess game.
#
# The state now lives on a PlayerSession (player_state.py), addressed by the
# caller's identity (identity.py). Endpoints ask for `session_for(request)` and
# work on that. Nothing in this file parses an identity string; it is opaque
# here exactly as it is there, which is what lets sign-in be added by changing
# which string arrives rather than by rewriting these endpoints again.
# ---------------------------------------------------------------------------

AI_VS_AI_MOVE_DELAY = 1.5


def learning_for(identity: str):
    """
    The cross-game learning handle this identity uses.

    One line now, and it used to be the single place guest mode differed:
    a guest was handed a complete learning service whose database was in
    memory and died with them (`guest_learning.py`), so that nothing they
    played was saved anywhere.

    That changed when guest history became claimable. A guest now writes to
    the same store as everyone else, under their own `guest:…` owner, so
    that signing up can hand those games to the new account
    (`LearningService.claim_guest_games`). Isolation is unchanged - it is
    enforced by the owner column on every query rather than by a separate
    database - but "a guest saves nothing" is no longer true, and the
    Account panel says so.

    Two guardrails came with that, both in learning_service.py:
    `purge_unclaimed_guest_games()` bounds how long anonymous rows live, and
    `reweight_candidates()` reads account-owned games only, so an anonymous
    visitor cannot steer what the AI plays against everyone else.
    """
    return LearningService(identity)


def session_for(request: Request):
    """
    This caller's game, created on first sight.

    Also completes the two fields PlayerSession deliberately leaves for this
    module to fill, because they need services it must not import: the
    learning handle, and the learning row the game in progress is logged
    against.
    """
    identity = identity_of(request)
    session = player_sessions.for_identity(identity, difficulty=DIFFICULTY_MAX)
    if session.learning is None:
        session.learning = learning_for(identity)
    if session.current_game_id is None:
        session.current_game_id = session.learning.start_game(session.player_color)
    return session


def refresh_eval(s):
    """Recompute this player's cached evaluation from their current position."""
    try:
        s.current_eval = stockfish_service.get_position_evaluation(s.game.get_fen())
    except Exception as e:
        logger.warning(f"\u26a0\ufe0f Failed to refresh position evaluation: {e}")


async def refresh_eval_async(s):
    """
    Same as refresh_eval(), but off the event loop.

    Every async caller must use this one: a Stockfish search is blocking,
    and running it inline stalls every other request (status polls, move
    grades) for as long as it takes. Sync callers - FastAPI already runs
    those in a worker thread - can keep using refresh_eval() directly.
    """
    try:
        s.current_eval = await asyncio.to_thread(stockfish_service.get_position_evaluation, s.game.get_fen())
    except Exception as e:
        logger.warning(f"\u26a0\ufe0f Failed to refresh position evaluation: {e}")


def get_ai_color(s) -> str:
    """The color the AI is currently playing for this player, in human_vs_ai mode."""
    return "black" if s.player_color == "white" else "white"


# Grading runs on its own small thread pool rather than as an asyncio task.
# A task would only start when the event loop next got a turn, which puts
# the player's own grade directly behind the AI's move search - the exact
# reason player badges used to lag ~10s behind the AI's near-instant ones.
# Submitting to a real pool starts the work immediately, in parallel, no
# matter what the loop is doing. Two workers is enough for the worst case
# (both colors graded back to back in AI vs AI).
_grading_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="move-grade")


def schedule_move_quality(s, ply_index: int, fen_before: str, move_uci: str):
    """
    Grade the half-move just played, in the background.

    Deliberately fire-and-forget: the grade is decoration, so it must never
    delay the move response or the AI's reply. The badge appears a moment
    later via the frontend's normal /api/status polling, the same way AI
    moves already do.
    """
    if not s.move_quality_enabled:
        return
    _grading_pool.submit(_classify_and_store, s, ply_index, fen_before, move_uci, s.game_epoch)


def _classify_and_store(s, ply_index: int, fen_before: str, move_uci: str, epoch: int):
    """Runs on a _grading_pool worker thread, not the event loop."""
    try:
        quality = classify_move(fen_before, move_uci)
        if quality is None:
            return
        # The board can be reset, or moves can be played, while the search
        # above is running - only attach the grade if this is still the
        # same game and the same move is still sitting at that ply.
        if epoch != s.game_epoch or not (0 <= ply_index < len(s.game.game_history)):
            return
        entry = s.game.game_history[ply_index]
        if entry.get('move') != move_uci:
            return
        entry['quality'] = quality
        # Recorded against this player's own learning handle - the shared
        # database for an account, an in-memory one for a guest.
        # record_move() numbers plies from 1, hence the +1.
        s.learning.update_move_quality(s.current_game_id, ply_index + 1, quality)
        logger.info(f"\U0001f3c5 Move {ply_index + 1} ({move_uci}) graded: {quality['name']}")
    except Exception as e:
        logger.warning(f"\u26a0\ufe0f Move grading task failed for {move_uci}: {e}")


def _regrade_whole_game(s, epoch: int):
    """
    Grade every ungraded move in this player's game, oldest first.

    Runs on a grading-pool worker. Moves played while grading was switched
    off (or before a restart) otherwise stay blank forever - this is the way
    to fill them in without replaying the game.
    """
    try:
        board = chess.Board()
        # Snapshot the moves up front: the list can grow underneath us if
        # the game continues while the re-grade is running.
        planned = [(i, entry.get('move')) for i, entry in enumerate(list(s.game.game_history))]
        pending = [(i, uci) for i, uci in planned if uci]
        s.regrade_progress = {"running": True, "done": 0, "total": len(pending)}
        for ply_index, move_uci in pending:
            if epoch != s.game_epoch:
                logger.info("\u2139\ufe0f Re-grade abandoned - a new game started")
                break
            fen_before = board.fen()
            try:
                move = chess.Move.from_uci(move_uci)
                if move not in board.legal_moves:
                    break
                board.push(move)
            except ValueError:
                break
            if ply_index < len(s.game.game_history) and not s.game.game_history[ply_index].get('quality'):
                quality = classify_move(fen_before, move_uci)
                if quality and epoch == s.game_epoch and ply_index < len(s.game.game_history):
                    entry = s.game.game_history[ply_index]
                    if entry.get('move') == move_uci:
                        entry['quality'] = quality
                        s.learning.update_move_quality(s.current_game_id, ply_index + 1, quality)
            s.regrade_progress["done"] += 1
        logger.info(f"\U0001f3c5 Re-grade finished ({s.regrade_progress['done']}/{s.regrade_progress['total']})")
    except Exception as e:
        logger.warning(f"\u26a0\ufe0f Re-grade failed: {e}")
    finally:
        s.regrade_progress = {"running": False, "done": 0, "total": 0}


def build_accuracy_summary(s) -> dict:
    """
    Per-color accuracy and grade counts for this player's current game, plus
    who is playing which side so the frontend can label them.
    """
    qualities = [entry.get('quality') for entry in s.game.game_history]
    white = summarize_accuracy(qualities[0::2])
    black = summarize_accuracy(qualities[1::2])
    return {
        "white": white,
        "black": black,
        "player_color": s.player_color,
        "regrade": dict(s.regrade_progress),
    }


def quality_at(s, ply_index: int):
    """The grade already attached to a ply, if grading finished before the
    move got logged (book moves grade instantly). None otherwise - the grade
    then arrives later via update_move_quality()."""
    if 0 <= ply_index < len(s.game.game_history):
        return s.game.game_history[ply_index].get('quality')
    return None


def start_new_learning_game(s):
    """Call whenever this player's board is reset/restarted, so subsequent
    moves log against a fresh row instead of appending to the just-finished
    game."""
    s.current_game_id = s.learning.start_game(s.player_color)
    s.game_finalized = False
    s.chat_history = []
    # Invalidate any in-flight move grading from the game just ended.
    s.game_epoch += 1


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


def finalize_learning_game_if_over(s):
    """Idempotent - safe to call after every move. Only actually records a
    result the first time this player's game is found to be over."""
    if s.game_finalized or s.current_game_id is None or not s.game.board.is_game_over():
        return
    result, termination = determine_game_result_and_termination(s.game)
    s.learning.finalize_game(s.current_game_id, result, termination)
    s.game_finalized = True


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
DIFFICULTY_WINDOW_SIZE = 3
# Overlapping AI-move requests are guarded per player, by
# PlayerSession.ai_move_lock() - e.g. the background task scheduled by
# /api/move racing a manual /api/ai-move click, both computing a move for the
# same turn. Whichever finished second used to get rejected by
# game.make_langflow_move() (board already advanced) and reported as an
# "Invalid AI move" even though the move it picked was perfectly legal at the
# moment it was chosen. Serializing on that lock (plus the staleness check
# below) prevents that, and it is also shared by that player's AI vs AI loop,
# so a manual action can never race an auto-play move.
#
# Per player rather than per process, which the single global got wrong the
# moment there was more than one board: one person thinking blocked everyone
# else's move, and "AI is already thinking" could be a stranger's AI.
# Initialize Langflow manager with error handling
#
# DISABLE_LANGFLOW=true runs the app engine-only: Stockfish still ranks and
# picks (the top of the current difficulty window), there is just no Gemini
# move choice or explanation. Worth having as a real switch rather than a
# misconfiguration, because without it there is no way to start the app
# without a reachable Langflow - the manager's constructor always succeeds,
# so an unreachable URL isn't caught here, and every single AI move then
# spends the flow-lookup retry budget (30 attempts, 5s apart = 150s) before
# falling back. That reads as "the AI is broken", not "Langflow is missing".
if os.environ.get("DISABLE_LANGFLOW", "false").lower() == "true":
    langflow_manager = None
    logger.info("ℹ️ DISABLE_LANGFLOW set - not using the Langflow move path")
else:
    try:
        langflow_manager = ChessLangflowManager(langflow_config)
        logger.info("✅ Langflow manager initialized successfully")
    except Exception as e:
        logger.error(f"❌ Failed to initialize Langflow manager: {e}")
        langflow_manager = None

# Say plainly, once, at startup who is actually choosing moves. Without
# this the only way to discover the LLM had dropped out of the loop was to
# notice a move explanation that read "Stockfish-calculated move" - which
# is exactly how it went unnoticed before.
if gemini_move_service.available:
    logger.info(
        f"🤖 Move selection: Gemini direct "
        f"(models: {', '.join(gemini_move_service.models[:3])}...)"
    )
elif langflow_manager:
    logger.info(f"🤖 Move selection: Gemini via Langflow at {langflow_config.url}")
else:
    logger.warning(
        "⚠️ Move selection: Stockfish only - no GEMINI_API_KEY set and no Langflow. "
        "The AI will still play legally, but no LLM is choosing or explaining moves."
    )
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
_UNSET = object()


async def decide_ai_move(
    current_fen: str,
    moving_color: str,
    *,
    difficulty: int = None,
    last_move=_UNSET,
    use_learning: bool = True,
    learning=None,
):
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

    The three keyword arguments exist so Sandbox Learner Mode can run this
    exact flow against its own isolated state instead of forking a second
    copy of it. Gemini choosing from Stockfish's shortlist *is* the product,
    so the sandbox has to go through the same code path or it stops
    demonstrating the real thing. All three default to current behaviour,
    so every existing caller is unaffected:

      difficulty    - the strength to play at. Defaults to full strength;
                      real-game callers pass the asking player's own slider
                      value, since there is no longer a server-wide one.
      last_move     - the mover's own previous move, to deprioritize
                      reversals. Passed explicitly (not inferred) because
                      None is itself a meaningful value, hence the _UNSET
                      sentinel rather than a None default.
      use_learning  - whether to reweight candidates using cross-game
                      history. Sandbox passes False: a demonstration is not
                      a real game and must not read from or bias the
                      player's learning history.
      learning      - WHOSE history to reweight against, when use_learning
                      is on. The shared database for a signed-in account, a
                      throwaway in-memory one for a guest - see
                      learning_for(). Passed in rather than reached for,
                      because this function must not be the thing that
                      decides whose data it is touching.
    """
    if difficulty is None:
        difficulty = DIFFICULTY_MAX
    if last_move is _UNSET:
        last_move = None
    # Off the event loop: even staged, this is hundreds of milliseconds of
    # engine work. Run inline it would freeze the whole server for the
    # duration - no /api/status, no eval bar, and no move grade could be
    # fetched while the AI was thinking, which is what made the player's
    # own badge appear ~10s late while the AI's appeared instantly.
    #
    # Stage 1: order every legal move shallowly. These scores only decide
    # where the difficulty window lands - see stockfish_service.
    ranked_moves = await asyncio.to_thread(stockfish_service.get_ranked_moves, current_fen, None)
    ranked_moves = deprioritize_reversal(ranked_moves, last_move)
    candidates = select_candidates_by_difficulty(ranked_moves, difficulty, window_size=DIFFICULTY_WINDOW_SIZE)
    # Stage 2: only now, on the two or three moves that survived, spend a
    # full-depth search. These are the scores Gemini reasons about and the
    # analysis panel displays, so they have to be the accurate ones.
    candidates = await asyncio.to_thread(
        stockfish_service.refine_candidates, current_fen, candidates
    )
    if use_learning and learning is not None:
        candidates = learning.reweight_candidates(current_fen, candidates)
    if not candidates:
        raise RuntimeError("Stockfish returned no candidate moves")
    candidate_ucis = [c["move"] for c in candidates]
    window_top_move = candidates[0]["move"]

    # Who actually picks the move. Direct Gemini first: it needs nothing
    # running but this process, so it keeps the LLM in the loop in every
    # environment where a key is set. Langflow is only used if it's
    # explicitly configured AND there's no direct key - it's the older path
    # and it needs a whole second service alive to work.
    chooser = None
    if gemini_move_service.available:
        chooser = gemini_move_service
    elif langflow_manager:
        chooser = langflow_manager

    if chooser is None:
        logger.warning(
            "⚠️ No LLM move selector available (set GEMINI_API_KEY, or configure Langflow) "
            "- using top of current difficulty window"
        )
        return window_top_move, "Stockfish-calculated move (no Gemini API key configured)", "stockfish_fallback"

    try:
        chosen_move, explanation, success = await chooser.choose_move_from_candidates(
            current_fen, candidates
        )
    except Exception as e:
        logger.warning(f"⚠️ Gemini candidate selection raised an exception, falling back to Stockfish: {e}")
        return window_top_move, f"Stockfish-calculated move (Gemini error: {e})", "stockfish_fallback"
    if success and chosen_move in candidate_ucis:
        logger.info(f"✅ Gemini chose {chosen_move} from {len(candidates)} candidates (difficulty={difficulty})")
        return chosen_move, explanation, "gemini"
    if success and chosen_move not in candidate_ucis:
        logger.warning(f"⚠️ Gemini picked '{chosen_move}', which is not in the candidate list {candidate_ucis} - falling back")
    else:
        logger.warning(f"⚠️ Gemini candidate selection failed ({explanation}) - falling back to Stockfish")
    return window_top_move, f"Stockfish-calculated move (Gemini fallback: {explanation})", "stockfish_fallback"
# Sandbox Learner Mode (see sandbox_state.py / sandbox_api.py). Mounted
# here, after decide_ai_move exists, because the sandbox reuses that exact
# function rather than forking a second move-selection path. Injected rather
# than imported the other way round so the dependency stays one-way.
sandbox_api.configure(decide_ai_move)
app.include_router(sandbox_api.router)

# Post-Mortem (see postmortem_state.py / postmortem_analysis.py /
# postmortem_api.py). Mounted the same way and for the same reason: a what-if
# branch is answered by this exact function, so exploring "what if I had played
# this instead" demonstrates the app's own play rather than a second engine
# path that happens to live in the review.
postmortem_api.configure(decide_ai_move)
app.include_router(postmortem_api.router)

# The learning loop (learning_loop.py / diagnosis_service.py / retest_bank.py /
# learning_loop_api.py). Mounted after Post-Mortem because it is built on it:
# every diagnosis starts from a decision in an imported game, and trying the
# better move IS a Post-Mortem branch rather than a second implementation of
# one. It is `/api/learning-loop`, not `/api/learning` - the latter is the
# cross-game learning panel below, a different feature that happens to share a
# word.
app.include_router(learning_loop_api.router)

# Same reasoning as the move-selection line above: say plainly at startup
# whether the sandbox's narration voice is actually live. Narration failing
# is otherwise invisible - the moves still play, there is just silence where
# the coaching was, which is exactly the kind of quiet degradation that let
# Gemini drop out of the move loop unnoticed.
if gemini_narration_service.available:
    logger.info(
        f"🗣️ Sandbox narration: Gemini "
        f"(models: {', '.join(gemini_narration_service.models[:2])}...)"
    )
else:
    logger.warning(
        "⚠️ Sandbox narration: unavailable (no GEMINI_API_KEY). "
        "Sandbox demonstrations will play moves but say nothing."
    )
if scenario_service.available:
    logger.info(
        f"🎬 Sandbox scenarios: Gemini "
        f"(models: {', '.join(scenario_service.models[:2])}...)"
    )
else:
    logger.warning(
        "⚠️ Sandbox scenarios: unavailable (no GEMINI_API_KEY). "
        "Sandbox sessions can still start from the standard position or an explicit FEN."
    )


async def make_ai_move_async(s):
    """Make this player's AI move in the background using the
    Stockfish-candidates + Gemini-choice flow (human_vs_ai mode)."""
    lock = s.ai_move_lock()
    if lock.locked():
        logging.info("\u26a0\ufe0f Background AI move skipped - an AI move is already in progress")
        return
    async with lock:
        try:
            if s.game_mode == "ai_vs_ai":
                logging.info("\u26a0\ufe0f Background AI move skipped - AI vs AI mode is active")
                return
            logging.info("\U0001f916 Background AI move starting...")
            eval_before = dict(s.current_eval)
            current_fen = s.game.get_fen()
            legal_moves = s.game.get_legal_moves_uci()
            current_turn = s.game.get_current_turn()
            ai_color = get_ai_color(s)
            if current_turn != ai_color or not legal_moves:
                logging.info(f"\u26a0\ufe0f Background AI move cancelled - turn: {current_turn}, expected: {ai_color}, moves: {len(legal_moves)}")
                return
            try:
                ai_move, explanation, source = await decide_ai_move(
                    current_fen, ai_color,
                    difficulty=s.ai_difficulty,
                    last_move=s.last_ai_move_by_color.get(ai_color),
                    learning=s.learning,
                )
            except Exception as e:
                logging.error(f"\u274c Background AI move decision failed: {e}")
                return
            if s.game.get_current_turn() != ai_color or s.game.get_fen() != current_fen:
                logging.info("\u2139\ufe0f Board changed while AI was thinking - discarding this move")
                return
            move_result = s.game.make_langflow_move(ai_move, explanation=explanation)
            if move_result["success"]:
                logging.info(f"\u2705 Background AI move completed ({source}): {ai_move} - {explanation}")
                ply_index = len(s.game.game_history) - 1
                san = s.game.game_history[-1].get('san') if s.game.game_history else None
                fen_after = s.game.get_fen()
                schedule_move_quality(s, ply_index, current_fen, ai_move)
                await refresh_eval_async(s)
                s.last_ai_move_by_color[ai_color] = ai_move
            else:
                logging.error(f"\u274c Background AI move invalid: {ai_move}")
            if move_result["success"]:
                eval_after = dict(s.current_eval)
                s.learning.record_move(
                    s.current_game_id, ply_index + 1, ai_color, "ai",
                    ai_move, san, current_fen, fen_after, eval_before, eval_after,
                    difficulty=s.ai_difficulty, source=source, explanation=explanation,
                    quality=quality_at(s, ply_index)
                )
                finalize_learning_game_if_over(s)
        except Exception as e:
            logging.error(f"\u274c Error in background AI move: {e}")


async def make_ai_vs_ai_move_async(s, chain: bool = True):
    """
    One half-move of this player's AI vs AI auto-play. If `chain` is True and
    we're still running after the move, schedules itself again after a short
    delay - a self-perpetuating loop rather than a fixed-count one, so pausing
    (ai_vs_ai_running = False) or exiting (game_mode changed away from
    "ai_vs_ai") takes effect the moment the *next* move would have been
    scheduled, without needing to cancel an in-flight asyncio task.
    `chain=False` is used by /api/ai-vs-ai/step to play exactly one move
    while paused, without kicking off further auto-play.
    """
    lock = s.ai_move_lock()
    if lock.locked():
        logging.info("\u26a0\ufe0f AI vs AI move skipped - a move is already in progress")
        return
    moved = False
    async with lock:
        try:
            if s.game_mode != "ai_vs_ai":
                return
            if chain and not s.ai_vs_ai_running:
                # Auto-play was paused between being scheduled and running - bail quietly.
                return
            current_fen = s.game.get_fen()
            legal_moves = s.game.get_legal_moves_uci()
            current_turn = s.game.get_current_turn()
            eval_before = dict(s.current_eval)
            if not legal_moves or s.game.is_game_over():
                logging.info("\U0001f3c1 AI vs AI game over - stopping auto-play")
                return
            try:
                ai_move, explanation, source = await decide_ai_move(
                    current_fen, current_turn,
                    difficulty=s.ai_difficulty,
                    last_move=s.last_ai_move_by_color.get(current_turn),
                    learning=s.learning,
                )
            except Exception as e:
                logging.error(f"\u274c AI vs AI move decision failed: {e}")
                return
            if s.game.get_fen() != current_fen:
                logging.info("\u2139\ufe0f Board changed unexpectedly during AI vs AI move - discarding")
                return
            move_result = s.game.make_langflow_move(ai_move, explanation=explanation)
            if move_result["success"]:
                logging.info(f"\u2705 AI vs AI move completed ({source}, {current_turn}): {ai_move} - {explanation}")
                ply_index = len(s.game.game_history) - 1
                san = s.game.game_history[-1].get('san') if s.game.game_history else None
                fen_after = s.game.get_fen()
                schedule_move_quality(s, ply_index, current_fen, ai_move)
                await refresh_eval_async(s)
                s.last_ai_move_by_color[current_turn] = ai_move
                eval_after = dict(s.current_eval)
                s.learning.record_move(
                    s.current_game_id, ply_index + 1, current_turn, "ai",
                    ai_move, san, current_fen, fen_after, eval_before, eval_after,
                    difficulty=s.ai_difficulty, source=source, explanation=explanation,
                    quality=quality_at(s, ply_index)
                )
                finalize_learning_game_if_over(s)
                moved = True
            else:
                logging.error(f"\u274c AI vs AI move invalid: {ai_move}")
                return
        except Exception as e:
            logging.error(f"\u274c Error in AI vs AI move: {e}")
            return
    if moved and chain and s.game_mode == "ai_vs_ai" and s.ai_vs_ai_running and not s.game.is_game_over():
        await asyncio.sleep(AI_VS_AI_MOVE_DELAY)
        asyncio.create_task(make_ai_vs_ai_move_async(s))


# The closed beta gate.
#
# Added FIRST of the three, and that is load-bearing rather than tidy: Starlette
# runs the LAST-added middleware outermost, so adding this one before CORS and
# Identity produces
#
#     IdentityMiddleware -> CORSMiddleware -> BetaGateMiddleware -> routes
#
# which is the only order that works. Identity has to have run, or there is no
# `request.state.identity` to authorize; CORS has to be outside, or the gate's
# 403 reaches a cross-origin caller stripped of its CORS headers and shows up
# in a browser as a network error with no status at all.
#
# Everything under /api is refused by default and `beta_gate.OPEN_PATHS` is the
# short list of exceptions - so a route added later is protected on the day it
# is written, without its author having to remember anything.
app.add_middleware(BetaGateMiddleware)

# CORS.
#
# `allow_origins=["*"]` together with `allow_credentials=True` is not a
# permissive setting - it is a broken one. The spec forbids the combination,
# so browsers reject a credentialed cross-origin request whose response echoes
# `*`, and they do it silently: no error in our log, just a request that never
# carries the identity cookie. Every visitor then looks brand new on every
# call and their board resets constantly. It went unnoticed until now only
# because nothing depended on a cookie.
#
# So origins are explicit. ALLOWED_ORIGINS is a comma-separated list for the
# deployed frontend (the Vercel URL); the localhost entries cover the dev
# server, and are harmless in production because a browser will not send a
# request claiming an origin it is not on.
_default_origins = [
    "http://localhost:3001",
    "http://127.0.0.1:3001",
    "http://localhost:5173",
]
_env_origins = [o.strip() for o in os.environ.get("ALLOWED_ORIGINS", "").split(",") if o.strip()]
ALLOWED_ORIGINS = _env_origins or _default_origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
if _env_origins:
    logger.info(f"\U0001f310 CORS origins: {', '.join(ALLOWED_ORIGINS)}")
else:
    logger.info(
        "\U0001f310 CORS origins: localhost only (set ALLOWED_ORIGINS to the deployed "
        "frontend URL before serving it from anywhere else)"
    )

# Identity, and the accounts that are built but switched off.
#
# The resolver is only handed over when accounts are ENABLED. With the flag
# off there is nothing that can turn a cookie into an account, so every
# caller is a guest by construction rather than by a check somebody could
# forget - the disabled state is enforced by what is wired, not only by what
# the routes say.
app.add_middleware(
    IdentityMiddleware,
    resolve_account=auth_service.resolve_session if accounts_enabled() else None,
)
app.include_router(auth_api.router)
app.include_router(auth_api.account_router)
app.include_router(beta_api.router)
app.include_router(profile_api.router)
if accounts_enabled():
    logger.warning(
        "\U0001f513 ACCOUNTS ARE ENABLED - sign-up and sign-in are live on /api/auth/*"
    )
    # With accounts on, two variables stop being optional, and neither failure
    # announces itself at the point it matters: with no DATABASE_URL every
    # signup fails on a server that booted cleanly, and with no stable
    # SESSION_COOKIE_SECRET every restart invalidates every guest cookie, so
    # visitors silently become new guests and history waiting to be claimed
    # becomes unclaimable. Both are stated here rather than discovered later.
    if not db.configured():
        logger.error(
            "\u274c ACCOUNTS_ENABLED=true but DATABASE_URL is not set. Accounts have "
            "nowhere to be stored; every signup will fail. Set DATABASE_URL."
        )
    if not os.environ.get("SESSION_COOKIE_SECRET"):
        logger.error(
            "\u274c ACCOUNTS_ENABLED=true but SESSION_COOKIE_SECRET is not set. Guest "
            "cookies are signed with a random per-process key, so every restart "
            "makes every visitor a new guest and orphans any history waiting to "
            "be claimed. Set it, once, and leave it alone."
        )
else:
    logger.info(
        "\U0001f512 Accounts: built but switched off (ACCOUNTS_ENABLED is not true). "
        "Everyone plays as a guest; /api/auth/signup and /api/auth/login answer 503."
    )

# Whether the front door is closed, said in words at the one moment somebody
# is watching. The gate is fail-closed by default (`beta_service.beta_required`),
# so the dangerous state is not "it refused everyone" - that is loud and gets
# reported in minutes - it is "it quietly let everyone in" after somebody set
# BETA_ACCESS_REQUIRED=false to debug something and left it. That state says so
# here, at WARNING, every boot.
if beta_service.beta_required():
    logger.warning(
        "\U0001f512 CLOSED BETA: guarded /api routes are refused without a "
        "redeemed access code. Manage codes with tools/beta_codes.py."
    )
    if not db.configured():
        logger.error(
            "\u274c BETA_ACCESS_REQUIRED is on but DATABASE_URL is not set. The "
            "grants live in Postgres, so NOBODY can be authorized - the whole app "
            "will answer 403. Set DATABASE_URL."
        )
    if not (os.environ.get("BETA_CODE_PEPPER") or os.environ.get("SESSION_COOKIE_SECRET")):
        logger.error(
            "\u274c BETA_ACCESS_REQUIRED is on but neither BETA_CODE_PEPPER nor "
            "SESSION_COOKIE_SECRET is set. Codes cannot be hashed, so every "
            "redemption will fail. Set one, once, and never change it."
        )
else:
    logger.warning(
        "\U0001f513 THE APP IS PUBLIC: BETA_ACCESS_REQUIRED=false, so the closed "
        "beta gate is switched off and every route is open to anyone."
    )

# Say which it is, so "why is /docs 404 on Render" is answered by the log
# rather than by reading this file.
if DOCS_ENABLED:
    logger.info("\U0001f4d6 API docs: /docs and /openapi.json are served")
else:
    logger.info(
        "\U0001f4d5 API docs: /docs, /redoc and /openapi.json are OFF "
        "(production; set ENABLE_DOCS=true to serve them anyway)"
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


# The mid-game chat transcript lives on the player's session
# (PlayerSession.chat_history): a list of {"role": "user"|"model", "text": str},
# oldest first. Never persisted - it is scoped to the current game and gets
# cleared alongside it in start_new_learning_game(). Fine to lose on a restart,
# and it was one of the more obviously wrong globals: a shared transcript meant
# one player could read another's conversation with the coach.


@app.post("/api/chat", dependencies=[Depends(limit_chat)])
async def chat_with_ai(payload: ChatRequest, request: Request):
    """Mid-game chat: ask the AI about the current position, its reasoning,
    what it expects you to play, etc. Calls Gemini directly (see
    gemini_chat_service.py) - the Langflow "Chess" flow is untouched by
    this endpoint."""
    try:
        s = session_for(request)
        user_message = payload.message
        if not user_message or not user_message.strip():
            return create_error_response("Message required", {"message": "message must not be empty"})
        ai_color = get_ai_color(s)
        last_ai_explanation = None
        if s.game.game_history:
            last_ai_explanation = s.game.game_history[-1].get("explanation")
        game_context = {
            "fen": s.game.get_fen(),
            "move_history_san": [m.get("san") for m in s.game.game_history if m.get("san")],
            "player_color": s.player_color,
            "ai_color": ai_color,
            "difficulty": s.ai_difficulty,
            "is_game_over": s.game.is_game_over(),
            "last_ai_explanation": last_ai_explanation,
            # This player's own history, not the server's. A guest's summary
            # comes from their in-memory learning layer, so the coach can talk
            # about the games they have played in this session without ever
            # reading a stranger's.
            "learning_context": s.learning.get_prompt_context_summary(),
        }
        success, reply = await gemini_chat_service.send_message(user_message, s.chat_history, game_context)
        if not success:
            return create_error_response("Chat request failed", {"message": reply})
        s.chat_history.append({"role": "user", "text": user_message})
        s.chat_history.append({"role": "model", "text": reply})
        return create_success_response("Chat reply received", {"reply": reply, "history": s.chat_history})
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
            "health": "/api/health",
            "game_status": "/api/status",
            "make_move": "/api/move",
            "ai_move": "/api/ai-move",
            "reset_game": "POST /api/reset",
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
            "regrade_game": "/api/move-quality/regrade",
            "account_config": "/api/auth/config",
            "whoami": "/api/auth/me",
            "signup": "/api/auth/signup",
            "login": "/api/auth/login",
            "logout": "/api/auth/logout",
            "import_games": "POST /api/profile/games",
            "improvement_profile": "/api/profile"
        }
    }
# The commit this process was built from.
#
# Read from the platform's own variable first, so this needs no configuration
# to work where it matters: Render sets RENDER_GIT_COMMIT itself, and BUILD_SHA
# is the manual override for anywhere else. "dev" on a laptop.
#
# It exists because the frontend and the backend deploy separately and skew
# silently (section 2 of CLAUDE.md). The one time that happened the symptom a
# user saw was "Not Found - try another file" on a perfectly good PGN. A build
# id here and one in the frontend footer turn that into a comparison anyone can
# make, rather than a question only a maintainer with a terminal can answer.
BUILD_SHA = (
    os.environ.get("BUILD_SHA")
    or os.environ.get("RENDER_GIT_COMMIT")
    or "dev"
)[:12]


@app.get("/api/health")
def health():
    """
    Liveness, and deliberately the one API route that creates nothing.

    Health checks are anonymous and frequent - Docker every 30s, Render on its
    own schedule - and they arrive with no cookie, so every single one looks
    like a brand-new visitor. Pointed at `/api/status` (as all three were)
    each check minted a PlayerSession and, with accounts off, an in-memory
    learning database to go with it: roughly 2,880 a day, churning straight
    through the store's 500-session cap and evicting real players' games to
    make room for monitoring.

    So this endpoint never calls `session_for()`. It answers whether the
    process is up and whether the engine is there, which is all a health check
    is entitled to know.
    """
    # One cheap round trip. A database that is configured but unreachable is
    # the failure this is here to surface - it looks like nothing at all from
    # the outside, because play carries on working and only persistence
    # silently stops.
    database = "not_configured"
    if db.configured():
        try:
            with db.connection() as conn:
                conn.execute("SELECT 1")
            database = "ok"
        except Exception as e:
            logger.warning(f"⚠️ Health check could not reach the database: {e}")
            database = "unreachable"

    return {
        # Still "ok" when the database or email is down: the process is alive
        # and can serve chess, and telling Render otherwise would make it
        # restart a healthy container over a problem restarting cannot fix.
        "status": "ok",
        "stockfish": stockfish_service is not None,
        "accounts_enabled": accounts_enabled(),
        "database": database,
        # Which build this is, so "did my deploy land?" is one request rather
        # than a guess. Set by the platform at build time; "dev" locally.
        # Deliberately just the commit: this endpoint is unauthenticated, and
        # a branch name or build host tells a stranger about the deployment
        # without telling the operator anything the SHA does not.
        "version": BUILD_SHA,
        # Whether the app is behind the closed-beta gate. Safe to publish and
        # worth publishing: a visitor learns it from the landing page anyway,
        # and it is the one way an operator can confirm from outside that a
        # deploy did not silently ship with the door open.
        "beta_required": beta_service.beta_required(),
        # Whether password-reset email can be delivered. Just the reason code,
        # never the missing variable names - this endpoint is unauthenticated,
        # and a list of which secrets are absent is a map for somebody.
        "email": email_service.status()["reason"],
    }


@app.post("/api/move", dependencies=[Depends(limit_move)])
async def make_move(payload: MoveRequest, request: Request):
    """Make player move and get AI response"""
    try:
        s = session_for(request)
        if s.game_mode == "ai_vs_ai":
            return create_error_response("AI vs AI mode is active", {
                "message": "Exit AI vs AI mode before making a manual move"
            })
        player_move = payload.move
        logging.info(f"🔄 Received move request: {player_move}")
        if not player_move:
            raise HTTPException(status_code=400, detail=ERROR_MESSAGES['move_required'])
        if s.game.get_current_turn() != s.player_color:
            return create_error_response("Not your turn", {
                "current_turn": s.game.get_current_turn(),
                "player_color": s.player_color
            })
        mover_color = s.game.get_current_turn()
        fen_before = s.game.get_fen()
        eval_before = dict(s.current_eval)
        result = s.game.make_player_move(player_move)
        logging.info(f"📥 Player move result: {result}")
        if not result['success']:
            return result
        # Snapshot the ply number, SAN and resulting position NOW, before any
        # await. Since the eval refresh stopped blocking the event loop, the
        # AI's background move can land while we're suspended on it - reading
        # len(game.game_history) afterwards then yields the AI's ply, not
        # ours, which corrupted the move log (duplicate/skipped ply numbers)
        # and made the grade update target the wrong row.
        ply_index = len(s.game.game_history) - 1
        san = s.game.game_history[-1].get('san') if s.game.game_history else None
        fen_after = s.game.get_fen()
        # Kicked off before the eval refresh, not after: grading only needs
        # the position and the move, both of which are already known here,
        # so starting it first lets it run alongside the eval search instead
        # of queueing behind it - a second off how fast the badge shows up.
        schedule_move_quality(s, ply_index, fen_before, player_move)
        await refresh_eval_async(s)
        s.learning.record_move(
            s.current_game_id, ply_index + 1, mover_color, "human",
            player_move, san, fen_before, fen_after, eval_before, dict(s.current_eval),
            quality=quality_at(s, ply_index)
        )
        finalize_learning_game_if_over(s)
        if s.game.board.is_game_over():
            return create_success_response('Game over!', {
                'game_over': True,
                'status': s.game.get_game_status(),
                'player_move': result,
                'eval': s.current_eval
            })
        if s.game.get_current_turn() == get_ai_color(s):
            logging.info(f"🤖 It's {get_ai_color(s)}'s turn - scheduling AI move in background")
            asyncio.create_task(make_ai_move_async(s))
            model_result = {
                'success': True,
                'message': 'AI move scheduled in background',
                'ai_scheduled': True
            }
        else:
            logging.info(f"⚪ It's {s.player_color}'s turn - no AI move needed")
            model_result = {
                'success': False,
                'message': f"{s.player_color.capitalize()}'s turn - player moves next",
                'skip_ai': True
            }
        response_data = {
            'player_move': result,
            'model_move': model_result,
            'status': s.game.get_game_status(),
            'eval': s.current_eval
        }
        return create_success_response('Move processed', response_data)
    except Exception as e:
        return create_error_response('Failed to process move', details={'error': str(e)})
@app.post("/api/reset")
def reset_game(request: Request):
    """Reset the caller's game. Keeps their current player_color (so hitting
    Reset while playing as Black stays as Black); always drops back to
    human_vs_ai mode if AI vs AI was active, since the Reset Game button
    isn't shown during AI vs AI anyway.

    POST, not GET. This threw away a game in progress on a GET, which makes
    it reachable by anything that can make a browser follow a URL - an
    `<img src>` on another site, a link, a prefetch - carrying the victim's
    own identity cookie. GET has to be safe; every other state-changing
    endpoint in this app is already a POST, and this one was the exception.
    Kept at the same path so only the verb changes for callers."""
    try:
        s = session_for(request)
        s.game.reset_game()
        refresh_eval(s)
        s.last_ai_move_by_color = {"white": None, "black": None}
        s.game_mode = "human_vs_ai"
        s.ai_vs_ai_running = False
        start_new_learning_game(s)
        return create_success_response(SUCCESS_MESSAGES['game_reset'], {
            'eval': s.current_eval,
            'player_color': s.player_color,
            'game_mode': s.game_mode
        })
    except Exception as e:
        return create_error_response('Failed to reset game', details={'error': str(e)})
@app.post("/api/set-color")
async def set_color(payload: ColorRequest, request: Request):
    """Start a fresh game with the human playing the requested color. If the
    human picks Black, White (the AI) moves first - scheduled the same way
    a normal AI turn is scheduled after any human move."""
    try:
        s = session_for(request)
        color = payload.color.lower()
        if color not in ("white", "black"):
            return create_error_response("Invalid color", {
                "message": "color must be 'white' or 'black'",
                "received": payload.color
            })
        s.player_color = color
        s.game_mode = "human_vs_ai"
        s.ai_vs_ai_running = False
        s.game.reset_game()
        await refresh_eval_async(s)
        s.last_ai_move_by_color = {"white": None, "black": None}
        start_new_learning_game(s)
        ai_scheduled = False
        if s.game.get_current_turn() == get_ai_color(s):
            logger.info(f"🎨 Player set to {s.player_color} - AI ({get_ai_color(s)}) moves first")
            asyncio.create_task(make_ai_move_async(s))
            ai_scheduled = True
        return create_success_response(f"Playing as {s.player_color}", {
            "player_color": s.player_color,
            "game_mode": s.game_mode,
            "ai_scheduled": ai_scheduled,
            "status": s.game.get_game_status(),
            "history": s.game.game_history,
            "eval": s.current_eval,
            "difficulty": s.ai_difficulty
        })
    except Exception as e:
        return create_error_response("Failed to set color", details={"error": str(e)})
@app.post("/api/ai-vs-ai/start")
async def ai_vs_ai_start(request: Request):
    """Start a fresh AI vs AI game and begin auto-play."""
    try:
        s = session_for(request)
        s.game.reset_game()
        await refresh_eval_async(s)
        s.last_ai_move_by_color = {"white": None, "black": None}
        start_new_learning_game(s)
        s.game_mode = "ai_vs_ai"
        s.ai_vs_ai_running = True
        asyncio.create_task(make_ai_vs_ai_move_async(s))
        return create_success_response("AI vs AI started", {
            "game_mode": s.game_mode,
            "ai_vs_ai_running": s.ai_vs_ai_running,
            "status": s.game.get_game_status(),
            "history": s.game.game_history,
            "eval": s.current_eval
        })
    except Exception as e:
        return create_error_response("Failed to start AI vs AI", details={"error": str(e)})
@app.post("/api/ai-vs-ai/pause")
def ai_vs_ai_pause(request: Request):
    """Pause AI vs AI auto-play. The in-flight move (if any) still finishes;
    it's the *next* move that gets skipped."""
    s = session_for(request)
    s.ai_vs_ai_running = False
    return create_success_response("Paused", {"ai_vs_ai_running": s.ai_vs_ai_running})
@app.post("/api/ai-vs-ai/resume")
async def ai_vs_ai_resume(request: Request):
    """Resume AI vs AI auto-play after a pause."""
    try:
        s = session_for(request)
        if s.game_mode != "ai_vs_ai":
            return create_error_response("Not in AI vs AI mode", {"game_mode": s.game_mode})
        if s.game.is_game_over():
            return create_error_response("Game is already over", {})
        s.ai_vs_ai_running = True
        asyncio.create_task(make_ai_vs_ai_move_async(s))
        return create_success_response("Resumed", {"ai_vs_ai_running": s.ai_vs_ai_running})
    except Exception as e:
        return create_error_response("Failed to resume AI vs AI", details={"error": str(e)})
@app.post("/api/ai-vs-ai/step")
async def ai_vs_ai_step(request: Request):
    """Play exactly one AI-vs-AI move without chaining to the next one -
    used while paused, to step through a game move by move."""
    try:
        s = session_for(request)
        if s.game_mode != "ai_vs_ai":
            return create_error_response("Not in AI vs AI mode", {"game_mode": s.game_mode})
        if s.ai_vs_ai_running:
            return create_error_response("Already auto-playing", {"message": "Pause first to step manually"})
        if s.game.is_game_over():
            return create_error_response("Game is already over", {})
        await make_ai_vs_ai_move_async(s, chain=False)
        return create_success_response("Step complete", {
            "status": s.game.get_game_status(),
            "history": s.game.game_history,
            "eval": s.current_eval
        })
    except Exception as e:
        return create_error_response("Failed to step", details={"error": str(e)})
@app.post("/api/ai-vs-ai/exit")
def ai_vs_ai_exit(request: Request):
    """Leave AI vs AI mode and return to normal human-vs-AI play (keeps the
    current player_color from before AI vs AI was started)."""
    s = session_for(request)
    s.game_mode = "human_vs_ai"
    s.ai_vs_ai_running = False
    return create_success_response("Exited AI vs AI mode", {
        "game_mode": s.game_mode,
        "player_color": s.player_color
    })
@app.get("/api/status")
def get_status(request: Request):
    """Get current game status"""
    try:
        s = session_for(request)
        return create_success_response('Status retrieved', {
            'status': s.game.get_game_status(),
            # Full history (not just the last few moves) - the frontend move
            # history panel renders the whole game, not a recent window.
            'history': s.game.game_history,
            'difficulty': s.ai_difficulty,
            'eval': s.current_eval,
            'player_color': s.player_color,
            'game_mode': s.game_mode,
            'ai_vs_ai_running': s.ai_vs_ai_running,
            # So a page refresh doesn't wipe the visible chat transcript -
            # chat_history is already kept server-side (see /api/chat),
            # cleared alongside every new game. Loaded back into the chat
            # box on mount by ChessBoard.tsx's initializeGame().
            'chat_history': s.chat_history,
            # Whether move grading is running. The badges themselves ride
            # along on each history entry's 'quality' key, filled in
            # asynchronously (see schedule_move_quality) - so a move can
            # appear here for a poll or two before its grade does.
            'move_quality_enabled': s.move_quality_enabled,
            # Per-color accuracy, grade counts and re-grade progress for the
            # Review panel. Derived from the same history above, so it can
            # never disagree with the badges on screen.
            'accuracy': build_accuracy_summary(s),
            # Whether this player is anonymous. The header renders "Guest"
            # from this, and it is also the honest answer to "is any of this
            # being saved?" - for a guest, nothing is.
            'guest': is_guest(s.identity)
        })
    except Exception as e:
        return create_error_response('Failed to get status', details={'error': str(e)})


@app.post("/api/move-quality/regrade", dependencies=[Depends(limit_regrade)])
def regrade_game(request: Request):
    """
    Grade every move in this player's current game that doesn't have a grade
    yet.

    Needed because grades are only produced as moves are played: anything
    played while the toggle was off, or before the server last restarted,
    would otherwise stay permanently blank.
    """
    try:
        s = session_for(request)
        if s.regrade_progress["running"]:
            return create_error_response("Re-grade already running", {
                "message": "A full-game re-grade is already in progress",
                "progress": dict(s.regrade_progress)
            })
        ungraded = sum(1 for e in s.game.game_history if not e.get('quality'))
        if ungraded == 0:
            return create_success_response("Nothing to re-grade", {
                "queued": 0,
                "message": "Every move in this game is already graded"
            })
        _grading_pool.submit(_regrade_whole_game, s, s.game_epoch)
        logger.info(f"🏅 Re-grade queued for {ungraded} ungraded move(s)")
        return create_success_response("Re-grade started", {"queued": ungraded})
    except Exception as e:
        return create_error_response("Failed to start re-grade", details={"error": str(e)})


@app.get("/api/move-quality")
def get_move_quality_enabled(request: Request):
    """Whether chess.com-style move grading is currently switched on."""
    try:
        s = session_for(request)
        return create_success_response("Move quality setting retrieved", {
            "enabled": s.move_quality_enabled
        })
    except Exception as e:
        return create_error_response("Failed to get move quality setting", details={"error": str(e)})


@app.post("/api/move-quality")
def set_move_quality_enabled(payload: MoveQualityRequest, request: Request):
    """
    Turn move grading on or off.

    Switching it off stops the extra Stockfish search per half-move; grades
    already attached to earlier moves are left in place, so flipping it back
    on doesn't lose them (though moves played while it was off stay
    ungraded - they'd need a re-analysis of the whole game to fill in).
    """
    try:
        s = session_for(request)
        s.move_quality_enabled = bool(payload.enabled)
        logger.info(f"🏅 Move quality grading {'enabled' if s.move_quality_enabled else 'disabled'}")
        return create_success_response("Move quality setting updated", {
            "enabled": s.move_quality_enabled
        })
    except Exception as e:
        return create_error_response("Failed to set move quality setting", details={"error": str(e)})
@app.get("/api/learning/summary")
def get_learning_summary(request: Request):
    """Live opponent-profile + AI self-history summary for the frontend's
    Learning panel (see learning_service.py).

    Answers from the CALLER's learning handle, which is the whole of guest
    mode as far as this endpoint is concerned: a guest sees the games they
    have played in this session and nothing else, because their learning
    layer is in memory and contains only their own play."""
    try:
        s = session_for(request)
        summary = s.learning.get_learning_summary()
        # Said plainly rather than inferred from empty numbers, so the panel
        # can explain why the history is short instead of looking broken.
        summary["guest"] = is_guest(s.identity)
        # Guests are persisted now, like everyone else - what a guest lacks
        # is a way back to this history from another browser, which is what
        # signing up gives them. The panel says that rather than implying
        # nothing is being recorded.
        summary["persisted"] = True
        summary["claimable"] = is_guest(s.identity)
        return create_success_response("Learning summary retrieved", summary)
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
def get_difficulty(request: Request):
    """Get this player's current AI difficulty (1-20)"""
    try:
        s = session_for(request)
        return create_success_response("Difficulty retrieved", {
            "difficulty": s.ai_difficulty
        })
    except Exception as e:
        return create_error_response("Failed to get difficulty", details={"error": str(e)})
@app.post("/api/difficulty")
def set_difficulty(payload: DifficultyRequest, request: Request):
    """Set this player's AI difficulty (1-20). 20 = strongest, 1 = weakest."""
    try:
        s = session_for(request)
        new_difficulty = payload.difficulty
        if not isinstance(new_difficulty, int) or new_difficulty < 1 or new_difficulty > DIFFICULTY_MAX:
            return create_error_response("Invalid difficulty", {
                "message": f"difficulty must be an integer between 1 and {DIFFICULTY_MAX}",
                "received": new_difficulty
            })
        s.ai_difficulty = new_difficulty
        logger.info(f"🎚️ AI difficulty set to {s.ai_difficulty}")
        return create_success_response("Difficulty updated", {
            "difficulty": s.ai_difficulty
        })
    except Exception as e:
        return create_error_response("Failed to set difficulty", details={"error": str(e)})
@app.post("/api/ai-move", dependencies=[Depends(limit_move)])
async def make_ai_move(request: Request):
    """Manually trigger AI move using the Stockfish-candidates + Gemini-choice flow (human_vs_ai mode)."""
    s = session_for(request)
    if s.game_mode == "ai_vs_ai":
        return create_error_response("AI vs AI mode is active", {
            "message": "Use the AI vs AI controls (pause/step/resume) instead"
        })
    lock = s.ai_move_lock()
    if lock.locked():
        return create_error_response("AI is already thinking", {
            "message": "An AI move is already in progress - please wait a moment and try again"
        })
    async with lock:
        try:
            logger.info("🤖 Manual AI move requested")
            eval_before = dict(s.current_eval)
            current_fen = s.game.get_fen()
            legal_moves = s.game.get_legal_moves_uci()
            current_turn = s.game.get_current_turn()
            ai_color = get_ai_color(s)
            logger.info(f"🎯 AI will play for {current_turn}")
            if current_turn != ai_color:
                return create_error_response("Not AI's turn", {
                    "current_turn": current_turn,
                    "message": f"AI plays as {ai_color}"
                })
            if not legal_moves:
                return create_error_response("No legal moves available", {
                    "fen": current_fen,
                    "game_over": s.game.is_game_over()
                })
            # Get Stockfish's full ranked list, window it by difficulty, then let Gemini choose from it
            try:
                ai_move, explanation, source = await decide_ai_move(
                    current_fen, ai_color,
                    difficulty=s.ai_difficulty,
                    last_move=s.last_ai_move_by_color.get(ai_color),
                    learning=s.learning,
                )
            except Exception as e:
                logger.error(f"❌ AI move decision failed: {e}")
                return create_error_response("AI move generation failed", {
                    "error": str(e),
                    "fen": current_fen,
                    "legal_moves": legal_moves
                })
            if s.game.get_current_turn() != ai_color or s.game.get_fen() != current_fen:
                logger.info("ℹ️ Board changed while AI was thinking - move no longer applies")
                return create_success_response("Board already advanced", {
                    "message": "The position changed while the AI was thinking (an auto-move likely already completed) - no action needed",
                    "game_state": {
                        "fen": s.game.get_fen(),
                        "turn": s.game.get_current_turn(),
                        "is_game_over": s.game.is_game_over(),
                        "is_check": s.game.is_in_check(),
                        "is_checkmate": s.game.is_checkmate(),
                        "is_stalemate": s.game.is_stalemate(),
                        "legal_moves": s.game.get_legal_moves_uci()
                    }
                })
            move_result = s.game.make_langflow_move(ai_move, explanation=explanation)
            if move_result["success"]:
                logger.info(f"✅ Manual AI move completed ({source}): {ai_move} - {explanation}")
                ply_index = len(s.game.game_history) - 1
                san = s.game.game_history[-1].get('san') if s.game.game_history else None
                fen_after = s.game.get_fen()
                schedule_move_quality(s, ply_index, current_fen, ai_move)
                await refresh_eval_async(s)
                s.last_ai_move_by_color[ai_color] = ai_move
                s.learning.record_move(
                    s.current_game_id, ply_index + 1, ai_color, "ai",
                    ai_move, san, current_fen, fen_after, eval_before, dict(s.current_eval),
                    difficulty=s.ai_difficulty, source=source, explanation=explanation,
                    quality=quality_at(s, ply_index)
                )
                finalize_learning_game_if_over(s)
                return create_success_response(f"AI played {ai_move}", {
                    "ai_move": ai_move,
                    "reasoning": explanation,
                    "source": source,
                    "difficulty": s.ai_difficulty,
                    "eval": s.current_eval,
                    "history": s.game.game_history,
                    "game_state": {
                        "fen": s.game.get_fen(),
                        "turn": s.game.get_current_turn(),
                        "is_game_over": s.game.is_game_over(),
                        "is_check": s.game.is_in_check(),
                        "is_checkmate": s.game.is_checkmate(),
                        "is_stalemate": s.game.is_stalemate(),
                        "legal_moves": s.game.get_legal_moves_uci()
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
