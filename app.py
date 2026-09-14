from pydantic import BaseModel
import asyncio
import logging
import os
import time
from typing import Optional
from concurrent.futures import ThreadPoolExecutor
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from game_logic import ChessGame
from utils import create_success_response, create_error_response
from config import HOST, DEBUG, ERROR_MESSAGES, SUCCESS_MESSAGES
import chess
import chess.pgn
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
import move_feedback_log
import engine_evidence
import gemini_http
import guided_play
from coach_style import CoachStyle
import candidate_selection
from opponent_profiles import LOW_PROFILE_IDS, PROFILE_IDS, all_summaries, display_label, get_profile
import db_writer
import learning_events
from rate_limit import limit_move, limit_chat, limit_regrade
from gemini_narration_service import gemini_narration_service
from scenario_service import scenario_service
import learning_loop_api
import postmortem_api
import postmortem_state
import sandbox_api
import admin_api
import auth_api
import beta_api
import beta_service
from beta_gate import BetaGateMiddleware
from body_limit import BodyLimitMiddleware
from csrf import CsrfOriginMiddleware, origin_of
from security_headers import SecurityHeadersMiddleware
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


@app.on_event("startup")
async def _startup_gemini_connection():
    """
    Open the connection to Gemini before a player is waiting on it.

    Every Gemini call used to build its own httpx client, and so pay a fresh
    TCP connect and TLS handshake - a measured median of 530ms per call, 28%,
    spent on setup rather than on thinking. They share one pooled connection
    now (`gemini_http`), which removes that from every call after the first.

    This is what removes it from the first one too. Best-effort by design: if
    it fails, the next real call opens the connection exactly as it would have
    anyway, so there is nothing to report and nothing to recover.
    """
    await gemini_http.warm(os.environ.get("GEMINI_API_KEY", ""))


@app.on_event("shutdown")
async def _shutdown_gemini_connection():
    """Close the shared Gemini pool with the app, so the loop can finish."""
    await gemini_http.close()


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
    # The last events of the session are still queued behind the request that
    # emitted them (db_writer.py); give them a moment to land before the pool
    # they need is closed.
    db_writer.flush(timeout=5.0)
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
    session = player_sessions.for_identity(identity)
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


async def refresh_eval_async(s, fen: str = None):
    """
    Same as refresh_eval(), but off the event loop.

    Every async caller must use this one: a Stockfish search is blocking,
    and running it inline stalls every other request (status polls, move
    grades) for as long as it takes. Sync callers - FastAPI already runs
    those in a worker thread - can keep using refresh_eval() directly.

    `fen` pins the evaluation to a position. The search now runs behind the
    response that asked for it (see /api/move), so by the time it finishes
    the board may have moved on - a reset, or the AI's reply landing first.
    An evaluation of a position that is no longer on the board is not written
    to the bar; whoever changed the board refreshes it for the new position.
    Returns the evaluation computed, or None if it failed or was discarded.
    """
    if fen is None:
        fen = s.game.get_fen()
    try:
        evaluation = await asyncio.to_thread(stockfish_service.get_position_evaluation, fen)
    except Exception as e:
        logger.warning(f"\u26a0\ufe0f Failed to refresh position evaluation: {e}")
        return None
    if s.game.get_fen() != fen:
        logger.info("\u2139\ufe0f Board changed while evaluating - eval for the old position discarded")
        return evaluation
    s.current_eval = evaluation
    return evaluation


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
        # The whole chain that produced that grade, in one greppable line - see
        # move_feedback_log for what each field answers. It exists because the
        # first report of a wrong grade could not be investigated at all: there
        # was no record of which position had been searched, from whose point
        # of view, or at what depth.
        fen_after = None
        try:
            probe = chess.Board(fen_before)
            probe.push(chess.Move.from_uci(move_uci))
            fen_after = probe.fen()
        except Exception:
            pass
        move_feedback_log.log_grade(
            mode="Play",
            fen_before=fen_before,
            move_uci=move_uci,
            fen_after=fen_after,
            san=entry.get('san'),
            player_color=s.player_color,
            quality=quality,
            eval_before=quality.get('eval_before'),
            eval_after=quality.get('eval_after'),
            identity=s.identity,
            game_id=str(s.current_game_id) if s.current_game_id is not None else None,
        )
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


def quality_at(s, ply_index: int, move_uci: str = None):
    """The grade already attached to a ply, if grading finished before the
    move got logged (book moves grade instantly). None otherwise - the grade
    then arrives later via update_move_quality().

    `move_uci`, when given, has to be the move sitting at that ply. The
    record is written after the response now, so a reset in between could
    put a different game's move at the same index; its grade is not ours."""
    if 0 <= ply_index < len(s.game.game_history):
        entry = s.game.game_history[ply_index]
        if move_uci is not None and entry.get('move') != move_uci:
            return None
        return entry.get('quality')
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
# Strength is an opponent profile (opponent_profiles.py), shared by both
# colors in AI vs AI mode (one setting, not a per-side one). What a profile
# does to the shortlist is candidate_selection.py.
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
# picks (the likeliest move of the profile's pool), there is just no Gemini
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
_UNSET = object()


async def decide_ai_move(
    current_fen: str,
    moving_color: str,
    *,
    profile=None,
    last_move=_UNSET,
    use_learning: bool = True,
    learning=None,
    guided: bool = False,
    coach_style_settings: dict = None,
    rng=None,
):
    """
    Core decision flow:
    1. Stockfish ranks ALL legal moves for the current position, best first
       (shallow).
    2. deprioritize_reversal() sinks any move that would undo
       `moving_color`'s own last move (see last_ai_move_by_color above).
    3. candidate_selection.select_pool() builds the pool a player of the
       chosen profile might consider: within a centipawn ceiling, hanging
       material only as often as that player does, weighted toward the
       checks and captures weak players see and away from the quiet
       defence they miss. Sampled, not sliced - so the same position gives
       a different shortlist next time.
    4. Stockfish re-searches just the pool at full depth (plus the engine's
       best move as a hidden reference, if it was not sampled, so the
       recorded centipawn loss is accurate).
    5. Gemini picks one from that pool, told who it is playing as, and
       explains why in that player's voice.
    6. The pick is validated against the pool.
    7. If Gemini fails, is unavailable, or names a move outside the pool,
       fall back to the highest-weight move WITHIN the pool (the one this
       profile is most likely to play - not the global Stockfish #1, not
       even the pool's strongest) - this keeps behavior profile-consistent
       even when the fallback path is used.
    `moving_color` is "white" or "black" - whichever color is actually
    about to move. Required (not inferred from the board) so callers are
    explicit about who they're deciding for, since in AI vs AI mode both
    colors take this path on alternating turns.
    Returns (move, explanation, source, decision) where source is "gemini"
    or "stockfish_fallback" and `decision` is the record for the event log
    (see _decision below: profile, rank and cost of the move played, who
    chose it and why not Gemini if not). Raises if Stockfish can't produce
    any candidates at all (i.e. game.get_legal_moves_uci() was already empty
    upstream, or Stockfish itself errors).

    The three keyword arguments exist so Sandbox Learner Mode can run this
    exact flow against its own isolated state instead of forking a second
    copy of it. Gemini choosing from Stockfish's shortlist *is* the product,
    so the sandbox has to go through the same code path or it stops
    demonstrating the real thing. All three default to current behaviour,
    so every existing caller is unaffected:

      profile       - the opponent profile to play as (an id or an
                      OpponentProfile; anything else is the default, club).
                      Real-game callers pass the asking player's own
                      setting, since there is no server-wide one.
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
      guided        - Guided Play (guided_play.py). The same one call, with
                      the prompt also asking for a "Watch out" section and
                      handed engine-computed facts about each candidate to
                      ground it. The returned explanation then carries the
                      section on its own line; callers take it apart with
                      guided_play.split_watch_out().
      coach_style_settings
                    - two bounded communication coordinates. They are placed
                      in a phrasing-only prompt block and do not change engine
                      ranking, profile pooling, fallback or candidate checks.
      rng           - the random source the pool is sampled with. Tests
                      seed one; production leaves it None.
    """
    profile = get_profile(profile)
    if last_move is _UNSET:
        last_move = None
    # Off the event loop: even staged, this is hundreds of milliseconds of
    # engine work. Run inline it would freeze the whole server for the
    # duration - no /api/status, no eval bar, and no move grade could be
    # fetched while the AI was thinking, which is what made the player's
    # own badge appear ~10s late while the AI's appeared instantly.
    #
    # Timed in stages, because "the AI takes too long" is four different
    # numbers and only one of them is the model thinking. See the log line at
    # the end of this function; the frontend times the half it can see
    # (moveTiming.ts) and this is the half it cannot.
    _t0 = time.monotonic()

    def _engine_stages():
        """
        Both engine stages, back to back, ahead of everything else queued.

        Held as one priority section (stockfish_service.priority) for two
        reasons. First, the AI's reply is the thing the player is waiting for;
        the grade of the move they just played and the eval bar's refresh are
        not, and before this they reached the engine first simply by being
        submitted first - 300-600ms added to every reply for work that now
        runs while Gemini is thinking, when the engine is idle anyway. Second,
        nothing may slip in between the two stages: stage 2 searches the
        window stage 1 chose, and the engine's hash from stage 1 is part of
        what makes stage 2 fast.
        """
        t_enter = time.monotonic()
        with stockfish_service.priority():
            t_acquired = time.monotonic()
            # Stage 1: order every legal move shallowly. These scores only
            # decide which moves the profile's pool is built from - see candidate_selection.
            ranked = stockfish_service.get_ranked_moves(current_fen, None)
            t_stage1 = time.monotonic()
            ranked = deprioritize_reversal(ranked, last_move)
            pool, pool_info = candidate_selection.select_pool(current_fen, ranked, profile, rng)
            to_refine = candidate_selection.with_reference(
                pool, candidate_selection.annotate(current_fen, ranked)
            )
            # Stage 2: only now, on the two or three moves that survived
            # (and the reference), spend a full-depth search. These are the
            # scores Gemini reasons about and the analysis panel displays,
            # so they have to be the accurate ones.
            refined = stockfish_service.refine_candidates(current_fen, to_refine)
            return refined, pool_info, t_stage1, t_acquired - t_enter

    refined, _pool_info, _t_stage1, _queued = await asyncio.to_thread(_engine_stages)
    _t_stage2 = time.monotonic()
    # Deep-best first within the pool; the reference never reaches Gemini.
    candidates = candidate_selection.strip_reference(refined)

    def _decision(chosen: str, selected_by: str, fallback_reason, gemini_started: float = None) -> dict:
        """
        The record of this decision for the event log (learning_events) and
        the log line: which profile, where the move played sat in the pool
        and in the engine's full list, what it cost against the best, how
        many moves were on offer, and who chose - Gemini, or the fallback
        and why. Safe to log: no FEN, no PGN, no text.
        """
        now = time.monotonic()
        picked = next((c for c in candidates if c["move"] == chosen), None) or {}
        return {
            "profile_id": profile.id,
            "approx_elo": profile.approx_elo,
            "move": chosen,
            "rank_in_pool": next((i for i, c in enumerate(candidates) if c["move"] == chosen), None),
            "rank_overall": picked.get("rank"),
            "cpl": picked.get("cpl"),
            "mate_in": picked.get("mate_in"),
            "n_candidates": len(candidates),
            "n_eligible": _pool_info.get("n_eligible"),
            "n_legal": _pool_info.get("n_legal"),
            "chooser": ("gemini" if chooser is gemini_move_service else "langflow") if chooser else None,
            "selected_by": selected_by,
            "fallback_reason": fallback_reason,
            "rank_depth": stockfish_service.rank_depth,
            "search_depth": stockfish_service.depth,
            "engine_ms": round(1000 * (_t_stage2 - _t0)),
            "llm_ms": round(1000 * (now - gemini_started)) if gemini_started else 0,
            "total_ms": round(1000 * (now - _t0)),
            "model": getattr(chooser, "last_model", None) if selected_by == "gemini" else None,
        }

    def _log_timing(source: str, gemini_started: float = None) -> None:
        """
        Where the wait actually went, in one greppable line.

        "The AI takes too long" is four numbers, and only one of them is the
        model thinking. Splitting them is what showed that the engine stages
        were spending most of their time QUEUED rather than searching - every
        search in the app, including the grade for the move the player just
        made, serialises behind one Stockfish (stockfish_service), so the AI's
        own ranking waits for work that is only decoration.
        """
        now = time.monotonic()
        gemini_ms = (now - gemini_started) * 1000 if gemini_started else 0.0
        logger.info(
            "\u23f1 ai_move "
            f"queued={1000*_queued:.0f}ms "
            f"stage1={1000*(_t_stage1-_t0-_queued):.0f}ms "
            f"stage2={1000*(_t_stage2-_t_stage1):.0f}ms "
            f"gemini={gemini_ms:.0f}ms "
            f"total={1000*(now-_t0):.0f}ms "
            f"source={source} profile={profile.id}"
        )

    if use_learning and learning is not None:
        # A Postgres read (~150ms to Neon from here). Off the loop: inline it
        # stalled every status poll in the app for the duration, on every move.
        candidates = await asyncio.to_thread(learning.reweight_candidates, current_fen, candidates)
    if not candidates:
        raise RuntimeError("Stockfish returned no candidate moves")
    candidate_ucis = [c["move"] for c in candidates]
    # The deterministic pick: the pool move this profile is most likely to
    # play (its sampling weight), not the strongest in the pool - otherwise
    # every fallback quietly plays above the chosen level.
    window_top_move = max(candidates, key=lambda c: c.get("weight", 0))["move"]

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
            "- using the deep-best move of the profile's pool"
        )
        _log_timing("stockfish_fallback")
        return (window_top_move, "Stockfish-calculated move (no Gemini API key configured)",
                "stockfish_fallback", _decision(window_top_move, "fallback", "no_llm"))

    _t_gemini = time.monotonic()
    # Only the Gemini chooser knows the profile and guided prompts; Langflow's
    # flow is the older path and is left exactly as it was.
    context = None
    if chooser is gemini_move_service:
        context = {
            "profile": profile,
            "moving_color": moving_color,
            "own_loose_hint": profile.id in LOW_PROFILE_IDS,
            "facts": guided_play.describe_candidates(current_fen, candidates),
            "coach_style": coach_style_settings,
        }
        if guided:
            context["guided"] = True
    try:
        if context is not None:
            chosen_move, explanation, success = await chooser.choose_move_from_candidates(
                current_fen, candidates, context
            )
        else:
            chosen_move, explanation, success = await chooser.choose_move_from_candidates(
                current_fen, candidates
            )
    except Exception as e:
        logger.warning(f"⚠️ Gemini candidate selection raised an exception, falling back to Stockfish: {e}")
        _log_timing("stockfish_fallback", _t_gemini)
        return (window_top_move, f"Stockfish-calculated move (Gemini error: {e})", "stockfish_fallback",
                _decision(window_top_move, "fallback", "gemini_error", _t_gemini))
    if success and chosen_move in candidate_ucis:
        logger.info(f"✅ Gemini chose {chosen_move} from {len(candidates)} candidates (profile={profile.id})")
        _log_timing("gemini", _t_gemini)
        return chosen_move, explanation, "gemini", _decision(chosen_move, "gemini", None, _t_gemini)
    if success and chosen_move not in candidate_ucis:
        logger.warning(f"⚠️ Gemini picked '{chosen_move}', which is not in the candidate list {candidate_ucis} - falling back")
        reason = "gemini_invalid_move"
    else:
        logger.warning(f"⚠️ Gemini candidate selection failed ({explanation}) - falling back to Stockfish")
        reason = "gemini_invalid_move" if "did not choose a shortlisted move" in str(explanation) else "gemini_failed"
    _log_timing("stockfish_fallback", _t_gemini)
    return (window_top_move, f"Stockfish-calculated move (Gemini fallback: {explanation})", "stockfish_fallback",
            _decision(window_top_move, "fallback", reason, _t_gemini))
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


def _decision_props(decision) -> dict:
    """
    The allowlisted slice of a decide_ai_move() decision record, for the
    event log (learning_events.ALLOWED_PROPERTIES). This is what answers
    "are the lower levels actually weaker, and weaker like a person or like
    a dice roll": the profile, where the move sat in the pool and in the
    engine's list, what it cost, and who chose it. No move text, no position.
    """
    if not decision:
        return {}
    return {
        "opponent_profile": decision.get("profile_id"),
        "approx_elo": decision.get("approx_elo"),
        "rank_in_pool": decision.get("rank_in_pool"),
        "rank_overall": decision.get("rank_overall"),
        "cpl": decision.get("cpl"),
        "n_candidates": decision.get("n_candidates"),
        "n_eligible": decision.get("n_eligible"),
        "selected_by": decision.get("selected_by"),
        "fallback_reason": decision.get("fallback_reason"),
        "rank_depth": decision.get("rank_depth"),
        "search_depth": decision.get("search_depth"),
        "engine_ms": decision.get("engine_ms"),
        "llm_ms": decision.get("llm_ms"),
        "model": decision.get("model"),
    }


def settle_ai_explanation(s, explanation: str, source: str, guided: bool,
                          ply_index: int, san, ai_color: str, started_at: float,
                          decision: dict = None) -> str:
    """
    What a Play AI move's explanation becomes once the move is on the board.

    Splits Guided Play's "Watch out" section off (guided_play.py), files the
    coach turn with the section under its own key, and records the two
    events. Returns the explanation BODY - the part that goes into the move
    list's hover text and the learning record, which have no business
    carrying coaching addressed to the other side.

    Both Play routes that play an AI move call this so they cannot drift.
    The sandbox and Post-Mortem do not: they never ask for the section.
    """
    body, watch_out = guided_play.split_watch_out(explanation)
    if not guided:
        # Belt and braces: a standard reply should never contain the section,
        # but if a model volunteers one it is not shown as coaching.
        body, watch_out = (explanation or "").strip(), None
    s.note_coach_turn(san, body, watch_out=watch_out)
    props = dict(
        game_id=s.current_game_id, source_mode="Play",
        ply_index=ply_index, side_to_move=ai_color, guided=bool(guided), source=source,
        duration_ms=int((time.monotonic() - started_at) * 1000),
        **_decision_props(decision),
    )
    props.setdefault("opponent_profile", s.opponent_profile)
    props.setdefault("approx_elo", get_profile(s.opponent_profile).approx_elo)
    learning_events.emit("ai_move_explanation_generated", s.identity, **props)
    if watch_out:
        learning_events.emit("guided_watchout_generated", s.identity, **props)
    return body


async def make_ai_move_async(s, guided: bool = False, eval_ready: asyncio.Future = None):
    """Make this player's AI move in the background using the
    Stockfish-candidates + Gemini-choice flow (human_vs_ai mode).

    `eval_ready` is /api/move's promise that the evaluation of the position
    the AI is about to move from has been written to `s.current_eval`. That
    refresh no longer happens before the AI is scheduled - it runs while
    Gemini is thinking - so the AI's `eval_before` (the learning record's
    "what was the position worth before this move") is read only once the
    future resolves, which by the time Gemini has answered it always has.

    Two things about the lock. It is WAITED for, not skipped: this task is
    created the instant the player's move is applied, and the previous AI
    move's task can still be inside its own bookkeeping at that moment - a
    fast reply used to find the lock held and silently skip, and the game
    then sat on the player's move until the 90s deadline. The checks inside
    the lock (right turn, same position) are what prevent a double move, not
    the skip. And the lock covers exactly the decision and the move - the
    eval refresh and the learning record happen after it is released, so the
    next move's decision never waits on this move's paperwork.
    """
    lock = s.ai_move_lock()
    if lock.locked():
        logging.info("\u2139\ufe0f Background AI move waiting - the previous AI move is still settling")
    moved = None
    async with lock:
        try:
            if s.game_mode == "ai_vs_ai":
                logging.info("\u26a0\ufe0f Background AI move skipped - AI vs AI mode is active")
                return
            logging.info("\U0001f916 Background AI move starting...")
            current_fen = s.game.get_fen()
            legal_moves = s.game.get_legal_moves_uci()
            current_turn = s.game.get_current_turn()
            ai_color = get_ai_color(s)
            if current_turn != ai_color or not legal_moves:
                logging.info(f"\u26a0\ufe0f Background AI move cancelled - turn: {current_turn}, expected: {ai_color}, moves: {len(legal_moves)}")
                return
            _started = time.monotonic()
            try:
                ai_move, explanation, source, decision = await decide_ai_move(
                    current_fen, ai_color,
                    profile=s.opponent_profile,
                    last_move=s.last_ai_move_by_color.get(ai_color),
                    learning=s.learning,
                    guided=guided,
                    coach_style_settings=s.coach_style,
                )
            except Exception as e:
                logging.error(f"\u274c Background AI move decision failed: {e}")
                return
            if s.game.get_current_turn() != ai_color or s.game.get_fen() != current_fen:
                logging.info("\u2139\ufe0f Board changed while AI was thinking - discarding this move")
                return
            # The eval of the position being moved from. Normally long done -
            # it ran while Gemini was thinking - and if it somehow is not, the
            # move is not held for it: the move is what the player is waiting
            # for, the number is bookkeeping.
            if eval_ready is not None and not eval_ready.done():
                try:
                    await asyncio.wait_for(asyncio.shield(eval_ready), timeout=2.0)
                except (asyncio.TimeoutError, Exception):
                    pass
            eval_before = dict(s.current_eval)
            move_result = s.game.make_langflow_move(ai_move, explanation=guided_play.split_watch_out(explanation)[0])
            if not move_result["success"]:
                logging.error(f"\u274c Background AI move invalid: {ai_move}")
                return
            logging.info(f"\u2705 Background AI move completed ({source}): {ai_move} - {explanation}")
            ply_index = len(s.game.game_history) - 1
            san = s.game.game_history[-1].get('san') if s.game.game_history else None
            explanation = settle_ai_explanation(
                s, explanation, source, guided, ply_index, san, ai_color, _started,
                decision=decision,
            )
            fen_after = s.game.get_fen()
            schedule_move_quality(s, ply_index, current_fen, ai_move)
            s.last_ai_move_by_color[ai_color] = ai_move
            moved = dict(
                game_id=s.current_game_id, ply_index=ply_index, ai_color=ai_color,
                ai_move=ai_move, san=san, fen_before=current_fen, fen_after=fen_after,
                eval_before=eval_before, source=source, explanation=explanation,
            )
        except Exception as e:
            logging.error(f"\u274c Error in background AI move: {e}")
            return
    if moved is None:
        return
    # Off the lock from here: the move is on the board and the poll will carry
    # it. What follows is the eval bar and the record, neither of which the
    # player - or the next AI move - should wait for.
    try:
        await refresh_eval_async(s, moved["fen_after"])
        eval_after = dict(s.current_eval)
        # The learning record is a Postgres write; off the loop so the status
        # polls that are about to carry this move to the board are not held
        # behind a round trip to the database.
        await asyncio.to_thread(
            s.learning.record_move,
            moved["game_id"], moved["ply_index"] + 1, moved["ai_color"], "ai",
            moved["ai_move"], moved["san"], moved["fen_before"], moved["fen_after"],
            moved["eval_before"], eval_after,
            opponent_profile=s.opponent_profile, source=moved["source"], explanation=moved["explanation"],
            quality=quality_at(s, moved["ply_index"], moved["ai_move"]),
        )
        await asyncio.to_thread(finalize_learning_game_if_over, s)
    except Exception as e:
        logging.error(f"\u274c Error recording the background AI move: {e}")


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
            _started = time.monotonic()
            try:
                ai_move, explanation, source, decision = await decide_ai_move(
                    current_fen, current_turn,
                    profile=s.opponent_profile,
                    last_move=s.last_ai_move_by_color.get(current_turn),
                    learning=s.learning,
                    coach_style_settings=s.coach_style,
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
                s.note_coach_turn(san, explanation)
                # The same decision record Play writes, marked as the
                # watching mode, so "is the profile weaker" can be asked of
                # AI vs AI games too.
                learning_events.emit(
                    "ai_move_explanation_generated", s.identity,
                    game_id=s.current_game_id, source_mode="AI vs AI",
                    ply_index=ply_index, side_to_move=current_turn, guided=False, source=source,
                    duration_ms=int((time.monotonic() - _started) * 1000),
                    **_decision_props(decision),
                )
                fen_after = s.game.get_fen()
                schedule_move_quality(s, ply_index, current_fen, ai_move)
                await refresh_eval_async(s, fen_after)
                s.last_ai_move_by_color[current_turn] = ai_move
                eval_after = dict(s.current_eval)
                await asyncio.to_thread(
                    s.learning.record_move,
                    s.current_game_id, ply_index + 1, current_turn, "ai",
                    ai_move, san, current_fen, fen_after, eval_before, eval_after,
                    opponent_profile=s.opponent_profile, source=source, explanation=explanation,
                    quality=quality_at(s, ply_index),
                )
                await asyncio.to_thread(finalize_learning_game_if_over, s)
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

# The origins this deployment serves. Computed here rather than beside
# CORSMiddleware because TWO middlewares need it now - CORS and the CSRF check
# below - and they must be given the same list. Re-reading the environment
# twice is how they end up disagreeing, which presents as an origin CORS
# accepts and CSRF refuses: writes failing on one deployment and nowhere else.
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
# The local origins, which are the fallback when ALLOWED_ORIGINS is unset.
# Harmless in a deployment - a browser does not send a request claiming an
# origin it is not on - and each entry is a server somebody actually runs:
# :3001 is the dev stack (CLAUDE.md, THE THREE BUILDS), :5173 is Vite's
# default dev port, and :4173 is Vite's default `preview` port, which serves
# the real production build and is how the shipping CSP gets exercised in a
# browser before it is deployed (§28).
#
# :4173 was added because it was missing: the CSRF check refused the sandbox's
# POST from the preview server on first contact, which is the middleware being
# right about an origin this list had not been told about.
_default_origins = [
    "http://localhost:3001",
    "http://127.0.0.1:3001",
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:4173",
    "http://127.0.0.1:4173",
]
_env_origins = [o.strip() for o in os.environ.get("ALLOWED_ORIGINS", "").split(",") if o.strip()]

# FRONTEND_URL counts as an allowed origin, always.
#
# This is belt-and-braces against a failure mode the CSRF check introduced, and
# it is worth spelling out because it is genuinely non-obvious:
#
# Until now ALLOWED_ORIGINS was consulted only by CORS, and CORS is never
# consulted in normal use - `vercel.json` rewrites /api server-side, so the
# browser sees one origin and no cross-origin request is ever made. A stale or
# wrong ALLOWED_ORIGINS was therefore INVISIBLE: nothing depended on it.
#
# `csrf.py` changes that. It checks the Origin header on every unsafe request,
# and that header is the deployed frontend's origin. So the moment this ships,
# a stale ALLOWED_ORIGINS stops being harmless and becomes "every POST, PUT and
# DELETE in the application answers 403" - with no CORS error to explain it,
# because CORS was never the thing refusing.
#
# FRONTEND_URL is the same value by definition and is known-good by a different
# route: password-reset emails are built from it and reset was smoke-tested end
# to end against production. Trusting it here means the CSRF check keeps working
# even if ALLOWED_ORIGINS is wrong, and costs nothing - it is our own frontend.
_frontend_origin = origin_of(os.environ.get("FRONTEND_URL", ""))
if _frontend_origin and _frontend_origin not in _env_origins:
    _env_origins = _env_origins + [_frontend_origin]

ALLOWED_ORIGINS = _env_origins or _default_origins

# Cross-site request forgery.
#
# Added after the gate and BEFORE CORS, which - because `add_middleware`
# prepends, so the last one added is the outermost - puts it between them:
#
#     ... -> CORSMiddleware -> CsrfOriginMiddleware -> BetaGateMiddleware -> routes
#
# Inside CORS for the same reason the gate is: a 403 that reaches a
# cross-origin caller stripped of its CORS headers presents in the browser as
# a network error with no status at all, which is unreadable from the console
# and wastes an afternoon. Outside the gate because a forged request should be
# refused as forged whether or not the forger also happens to hold beta
# access - the two refusals mean different things and should not be collapsed.
#
# `test_security.py` §6 asserts this order, because getting it wrong fails
# silently in both directions.
app.add_middleware(CsrfOriginMiddleware, allowed_origins=ALLOWED_ORIGINS)

# CORS, over the same explicit origin list assembled above - see the note
# there for why `allow_origins=["*"]` is a broken setting here rather than a
# permissive one.
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

# The request-body ceiling, and the browser-facing security headers. Added
# LAST, which makes them the OUTERMOST two, and both of them need to be:
#
#   * the body limit has to refuse an oversized upload before anything
#     downstream buffers it, which means before Identity mints a cookie for it
#     and before the router materialises a Pydantic model from it;
#   * the headers have to land on EVERY response, including the ones no inner
#     middleware ever produces - a CORS rejection, the gate's 403, a 413 from
#     the body limit, and an unhandled 500.
#
# Final order, outermost first:
#
#     SecurityHeaders -> BodyLimit -> Identity -> CORS -> Csrf -> BetaGate -> routes
app.add_middleware(BodyLimitMiddleware)
app.add_middleware(SecurityHeadersMiddleware)
app.include_router(auth_api.router)
app.include_router(auth_api.account_router)
app.include_router(beta_api.router)
app.include_router(profile_api.router)
app.include_router(admin_api.router)
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
    # Guided Play (guided_play.py): the reply this move triggers also says
    # what to watch for. Rides on the request rather than living on the
    # session so the preference has one owner - the browser's settings.
    guided: bool = False
    coach_style: CoachStyle = CoachStyle()
class AiMoveRequest(BaseModel):
    guided: bool = False
    coach_style: CoachStyle = CoachStyle()
class ProfileRequest(BaseModel):
    profile: str
class ColorRequest(BaseModel):
    color: str
    coach_style: CoachStyle = CoachStyle()
class CoachStyleRequest(BaseModel):
    coach_style: CoachStyle = CoachStyle()
class ChatRequest(BaseModel):
    message: str
    coach_style: CoachStyle = CoachStyle()
class MoveQualityRequest(BaseModel):
    enabled: bool


# The mid-game chat transcript lives on the player's session
# (PlayerSession.chat_history): a list of {"role": "user"|"model", "text": str},
# oldest first. The coach's explanation of each of its own moves is in it too
# (PlayerSession.note_coach_turn) - the Play panel shows one conversation, not
# a Coach tab beside a Chat tab, and the history replayed to Gemini has to
# match what the player has read. Never persisted - it is scoped to the current game and gets
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

        # --- The engine's evidence, which this endpoint used not to send -----
        #
        # Until this, the mid-game chat was handed a FEN, a SAN history and a
        # difficulty number and nothing else. Asked "was that a good move?" it
        # had no choice but to answer from its own opinion - about a move this
        # app had already graded with Stockfish and drawn a badge for. A beta
        # tester reported being told a move was bad that they believed was
        # best, and this is the path on which that is not merely possible but
        # unavoidable: the model was never given the grade it was being asked
        # about.
        #
        # Learner Mode's coach and Post-Mortem's coach were both already given
        # the engine's ranking; Play was the one that was not. Both halves are
        # sent now - what the engine thinks of the position in front of the
        # player, and what it graded the half-move that produced it - and the
        # instruction (gemini_chat_service._build_system_instruction) forbids
        # asserting a quality that did not come from here.
        last_move_evidence = None
        if s.game.game_history:
            last_entry = s.game.game_history[-1]
            quality = last_entry.get("quality")
            if quality:
                last_move_evidence = {
                    "san": last_entry.get("san"),
                    "by": "the human" if last_entry.get("player") == "human" else "you",
                    "sentence": engine_evidence.grade_sentence(quality, last_entry.get("san")),
                }
        alternatives_text, best_line_text = await engine_evidence.ranked_evidence(
            s.game.get_fen(), 5, label="Play chat"
        )

        game_context = {
            "fen": s.game.get_fen(),
            "move_history_san": [m.get("san") for m in s.game.game_history if m.get("san")],
            "player_color": s.player_color,
            "ai_color": ai_color,
            "opponent_level": display_label(s.opponent_profile),
            "is_game_over": s.game.is_game_over(),
            "last_ai_explanation": last_ai_explanation,
            "alternatives": alternatives_text,
            "best_line": best_line_text,
            "last_move_evidence": last_move_evidence,
            # This player's own history, not the server's. A guest's summary
            # comes from their in-memory learning layer, so the coach can talk
            # about the games they have played in this session without ever
            # reading a stranger's.
            "learning_context": s.learning.get_prompt_context_summary(),
            "coach_style": payload.coach_style.model_dump(),
        }
        success, reply = await gemini_chat_service.send_message(user_message, s.chat_history, game_context)
        if not success:
            return create_error_response("Chat request failed", {"message": reply})
        s.chat_history.append({"role": "user", "text": user_message})
        s.chat_history.append({"role": "model", "text": reply})
        return create_success_response("Chat reply received", {"reply": reply, "history": s.chat_history})
    except Exception as e:
        return create_error_response("Failed to process chat message", details={"error": str(e)})


@app.post("/api/chat/clear")
async def clear_chat(request: Request):
    """Empty the conversation, on purpose, without touching the game.

    The Chat panel's "Clear chat" action. It has to reach the server: the
    transcript is what gets replayed to Gemini on the next question, so a
    clear that only emptied the browser's copy would leave the coach
    remembering everything the player had just watched disappear - and a
    reload would bring it all back."""
    s = session_for(request)
    s.chat_history = []
    return create_success_response("Chat cleared", {"history": s.chat_history})


# Chess Game Endpoints
@app.get("/")
def index():
    """
    The route map - on a laptop, and NOT on a public host.

    This endpoint published a hand-written index of every endpoint in the
    application, unauthenticated, to anybody who asked. It sits at `/` rather
    than under `/api/`, so `beta_gate.py` never saw it: the whole application
    answered 403 without an invitation and then listed its own attack surface
    at the front door.

    That also made hiding the interactive docs decorative. `DOCS_ENABLED`
    exists because "/docs and /openapi.json publish a complete map of every
    endpoint, including the ones that spend the API key on each call" - and
    then this route published an abbreviated version of the same map beside
    it. Two doors, one of them locked.

    So it follows the same flag. On a public host it answers 404, which is
    what an unknown path answers anyway, so it tells a prober nothing. Nothing
    in the app calls it - the browser talks to the Vercel origin, where `/` is
    the SPA - so this costs no functionality; it was only ever a convenience
    for whoever curl'd the backend directly, which is exactly the case
    DOCS_ENABLED is already tuned for.
    """
    if not DOCS_ENABLED:
        raise HTTPException(status_code=404, detail="Not Found")
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


async def _settle_player_move(s, game_id, ply_index: int, mover_color: str, move_uci: str,
                              san, fen_before: str, fen_after: str, eval_before: dict,
                              eval_ready: asyncio.Future):
    """
    Everything a human move owes the record that the player is not waiting for.

    Refreshes the eval bar for the new position (the search queues behind the
    AI's priority stages and runs while Gemini thinks), resolves `eval_ready`
    so the AI's own record can read it, then writes the learning row off the
    event loop. Runs detached from /api/move; see the ordering note there.
    """
    evaluation = None
    try:
        evaluation = await refresh_eval_async(s, fen_after)
    finally:
        if not eval_ready.done():
            eval_ready.set_result(evaluation)
    try:
        await asyncio.to_thread(
            s.learning.record_move,
            game_id, ply_index + 1, mover_color, "human",
            move_uci, san, fen_before, fen_after, eval_before,
            evaluation if evaluation is not None else dict(s.current_eval),
            quality=quality_at(s, ply_index, move_uci),
        )
        await asyncio.to_thread(finalize_learning_game_if_over, s)
    except Exception as e:
        logging.warning(f"\u26a0\ufe0f Could not record the player's move {move_uci}: {e}")


@app.post("/api/move", dependencies=[Depends(limit_move)])
async def make_move(payload: MoveRequest, request: Request):
    """Make player move and get AI response"""
    try:
        s = session_for(request)
        s.coach_style = payload.coach_style.model_dump()
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
        game_id = s.current_game_id
        game_over = s.game.board.is_game_over()
        ai_turn = not game_over and s.game.get_current_turn() == get_ai_color(s)

        # What happens next, in the order the player feels it (CLAUDE.md §36):
        #
        #   1. The AI is scheduled FIRST, before the grade and the eval are
        #      even submitted, and its two engine stages hold the engine as a
        #      priority section. Before this the response waited for the eval
        #      (which itself queued behind the grade) and only then scheduled
        #      the AI - 300-600ms in which the engine was busy with decoration
        #      and Gemini had not yet been asked anything.
        #   2. The grade and the eval refresh run behind it, while Gemini
        #      thinks - the engine is idle then anyway.
        #   3. This response returns without waiting for any of it. The eval
        #      and the grade reach the board through the status poll that is
        #      already watching for the AI's move; the learning record is
        #      written when the eval it needs exists.
        #
        # `eval_ready` is the one hand-off: the AI's own learning record wants
        # the eval of the position it moved from, so it waits on this future
        # (normally long resolved) rather than reading a stale number.
        eval_ready = asyncio.get_running_loop().create_future()
        if ai_turn:
            logging.info(f"🤖 It's {get_ai_color(s)}'s turn - scheduling AI move in background")
            asyncio.create_task(make_ai_move_async(s, guided=payload.guided, eval_ready=eval_ready))
            # Let the AI's task run as far as submitting its engine work, so
            # its priority section is at the gate before the grade's search.
            await asyncio.sleep(0)
        schedule_move_quality(s, ply_index, fen_before, player_move)
        settle = asyncio.create_task(_settle_player_move(
            s, game_id, ply_index, mover_color, player_move, san,
            fen_before, fen_after, eval_before, eval_ready,
        ))
        if game_over:
            # Nothing is racing the eval now, and the end-state layer is the
            # last thing this game shows - give it the exact number.
            await settle
            return create_success_response('Game over!', {
                'game_over': True,
                'status': s.game.get_game_status(),
                'player_move': result,
                'eval': s.current_eval
            })
        if ai_turn:
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
def play_game_pgn(s) -> str:
    """
    The finished Play game as a PGN, written from the server's own board.

    The browser is never asked for the moves: `s.game.board.move_stack` is
    the record of what was actually played, and python-chess writes it. The
    headers are the ones Post-Mortem reads (`KEPT_HEADERS`): the seats are
    named as Play names them - "You" and "Gemini" - so the review's seats and
    result read the same way the game did; `Result` is the board's own verdict
    (`claim_draw=True`, so a threefold or fifty-move ending that Play would
    show as a draw is one here too); `AIStrength` and `Source` ride along for
    anyone reading the file, though the store does not keep them.
    """
    board = s.game.board
    game = chess.pgn.Game.from_board(board)
    # The coach's seat names the level it played at, so the Review that
    # opens from this says "Gemini (Club ~1500)" without a header of its own.
    human, coach = "You", f"Gemini ({display_label(s.opponent_profile)})"
    game.headers["Event"] = "Zugzwang Play"
    game.headers["Site"] = "Zugzwang"
    game.headers["Date"] = time.strftime("%Y.%m.%d")
    game.headers["Round"] = "-"
    game.headers["White"] = human if s.player_color == "white" else coach
    game.headers["Black"] = coach if s.player_color == "white" else human
    game.headers["Result"] = board.result(claim_draw=True)
    status = s.game.get_game_status()
    kind = "checkmate" if status["is_checkmate"] else "stalemate" if status["is_stalemate"] else "draw"
    game.headers["Termination"] = kind
    game.headers["AIStrength"] = display_label(s.opponent_profile)
    game.headers["OpponentProfile"] = s.opponent_profile
    game.headers["Source"] = "Play"
    return str(game)


@app.post("/api/postmortem/from-play", dependencies=[Depends(limit_move)])
async def review_play_game(request: Request):
    """
    "Review this game": the finished Play game becomes a Post-Mortem review.

    One request, no PGN from the browser. The game is read from this
    session's own board (play_game_pgn), handed to the SAME store and replay
    that a dropped file goes through, and its whole-game scan is started
    here rather than by a second request - so the browser lands on a review
    that is already analysing. The review is owned by the calling identity
    exactly as an import is, so every existing /api/postmortem route accepts
    it, and the review id is what the browser remembers; a refresh resumes
    it the way a refresh resumes any review.

    Refuses with 409 while the game is live: the record of a game is not
    finished until the game is, and a review of half a game would grade
    moves the player was still deciding.
    """
    s = session_for(request)
    if s.game_mode == "ai_vs_ai":
        return JSONResponse(status_code=409, content={"detail": "Stop watching first - AI vs AI games are not reviewed here."})
    if not s.game.board.is_game_over(claim_draw=True):
        return JSONResponse(status_code=409, content={"detail": "The game is not over yet. Finish it, then review it."})
    identity = s.identity
    started = time.monotonic()
    pgn = play_game_pgn(s)
    try:
        game = postmortem_state.postmortem_games.create(
            pgn=pgn, source_name="Your game against Gemini", owner=identity,
        )
    except (postmortem_state.PgnError, ValueError) as exc:
        logger.error(f"❌ Play game could not be handed to Review: {exc}")
        learning_events.emit(
            "play_game_review_handoff_failed", identity, source_mode="Play",
            error_category="replay_failed", completed=False,
        )
        return JSONResponse(status_code=500, content={"detail": f"That game could not be replayed: {exc}"})
    game.origin = "play"
    game.player_color = s.player_color
    props = dict(
        game_id=game.id, source_mode="Play", result=game.result,
        termination=game.termination.get("kind"), opponent_profile=s.opponent_profile,
        approx_elo=get_profile(s.opponent_profile).approx_elo,
        player_color=s.player_color, total_moves=len(game.mainline) - 1,
        duration_ms=round((time.monotonic() - started) * 1000),
    )
    learning_events.emit("play_game_analysis_started", identity, **props)
    # The same idempotent scan start the Review UI fires on import.
    await postmortem_api.start_scan(game.id, request)
    # Let the scan task take its first step so the state below says
    # "running" rather than "idle" - the browser reads that word to decide
    # whether to show the analysing state or the empty one.
    await asyncio.sleep(0)
    logger.info(
        f"🔁 Play game handed to Review as {game.id}: {len(game.mainline) - 1} plies, "
        f"{game.result}, player {s.player_color}"
    )
    state = postmortem_api.review_state(game)
    return state


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
        s.coach_style = payload.coach_style.model_dump()
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
            "opponent_profile": s.opponent_profile,
            "approx_elo": get_profile(s.opponent_profile).approx_elo,
        })
    except Exception as e:
        return create_error_response("Failed to set color", details={"error": str(e)})
@app.post("/api/ai-vs-ai/start")
async def ai_vs_ai_start(request: Request, payload: Optional[CoachStyleRequest] = None):
    """Start a fresh AI vs AI game and begin auto-play."""
    try:
        s = session_for(request)
        if payload is not None:
            s.coach_style = payload.coach_style.model_dump()
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
            'opponent_profile': s.opponent_profile,
            'approx_elo': get_profile(s.opponent_profile).approx_elo,
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
def _profile_payload(s) -> dict:
    p = get_profile(s.opponent_profile)
    return {
        "profile": p.id,
        "label": p.label,
        "approx_elo": p.approx_elo,
        "blurb": p.blurb,
        "profiles": all_summaries(),
    }


@app.get("/api/difficulty")
def get_difficulty(request: Request):
    """
    This player's opponent profile, and the seven there are.

    The path kept its old name so nothing else had to move; the 1-20 integer
    it used to carry is gone (opponent_profiles.py).
    """
    try:
        s = session_for(request)
        return create_success_response("Opponent profile retrieved", _profile_payload(s))
    except Exception as e:
        return create_error_response("Failed to get opponent profile", details={"error": str(e)})


@app.post("/api/difficulty")
def set_difficulty(payload: ProfileRequest, request: Request):
    """Set this player's opponent profile by id ("beginner" ... "master")."""
    try:
        s = session_for(request)
        if payload.profile not in PROFILE_IDS:
            return create_error_response("Invalid opponent profile", details={
                "message": f"profile must be one of: {', '.join(PROFILE_IDS)}",
                "allowed": list(PROFILE_IDS),
                "received": payload.profile,
            })
        s.opponent_profile = payload.profile
        logger.info(f"🎚️ Opponent profile set to {display_label(s.opponent_profile)}")
        return create_success_response("Opponent profile updated", _profile_payload(s))
    except Exception as e:
        return create_error_response("Failed to set opponent profile", details={"error": str(e)})
@app.post("/api/ai-move", dependencies=[Depends(limit_move)])
async def make_ai_move(request: Request, payload: Optional[AiMoveRequest] = None):
    """Manually trigger AI move using the Stockfish-candidates + Gemini-choice flow (human_vs_ai mode)."""
    s = session_for(request)
    guided = bool(payload and payload.guided)
    if payload is not None:
        s.coach_style = payload.coach_style.model_dump()
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
            # Stockfish's ranked list, the profile's pool, then Gemini's choice from it
            _started = time.monotonic()
            try:
                ai_move, explanation, source, decision = await decide_ai_move(
                    current_fen, ai_color,
                    profile=s.opponent_profile,
                    last_move=s.last_ai_move_by_color.get(ai_color),
                    learning=s.learning,
                    guided=guided,
                    coach_style_settings=s.coach_style,
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
            move_result = s.game.make_langflow_move(ai_move, explanation=guided_play.split_watch_out(explanation)[0])
            if move_result["success"]:
                logger.info(f"✅ Manual AI move completed ({source}): {ai_move} - {explanation}")
                ply_index = len(s.game.game_history) - 1
                san = s.game.game_history[-1].get('san') if s.game.game_history else None
                explanation = settle_ai_explanation(
                    s, explanation, source, guided, ply_index, san, ai_color, _started,
                    decision=decision,
                )
                fen_after = s.game.get_fen()
                schedule_move_quality(s, ply_index, current_fen, ai_move)
                await refresh_eval_async(s, fen_after)
                s.last_ai_move_by_color[ai_color] = ai_move
                await asyncio.to_thread(
                    s.learning.record_move,
                    s.current_game_id, ply_index + 1, ai_color, "ai",
                    ai_move, san, current_fen, fen_after, eval_before, dict(s.current_eval),
                    opponent_profile=s.opponent_profile, source=source, explanation=explanation,
                    quality=quality_at(s, ply_index),
                )
                await asyncio.to_thread(finalize_learning_game_if_over, s)
                return create_success_response(f"AI played {ai_move}", {
                    "ai_move": ai_move,
                    "reasoning": explanation,
                    "source": source,
                    "opponent_profile": s.opponent_profile,
                    "approx_elo": get_profile(s.opponent_profile).approx_elo,
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
