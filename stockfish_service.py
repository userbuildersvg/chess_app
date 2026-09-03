"""
Stockfish integration for chess move generation.

Performance note - why this file looks the way it does
------------------------------------------------------
Move selection used to cost ~7.5s per half-move on a fast machine (far
worse on a throttled container), for two measured reasons:

1. `get_ranked_moves()` analysed **every legal move at full depth** - in
   a middlegame, ~35 depth-15 searches. Almost all of that was thrown
   away: the full ranking exists only to *position the difficulty
   window*, and just the 3 moves inside that window are ever evaluated,
   shown, or played.

2. Every entry point opened a **fresh Stockfish process** (measured at
   ~530ms each) and discarded it, so the transposition table never
   survived a call. The old flow paid that three times per half-move -
   ranking, grading and the eval bar - about 1.6s of pure process churn
   before any thinking happened.

Both are fixed, and the fix is staged rather than clever:

* **Stage 1 (`get_ranked_moves`)** orders *all* legal moves with one
  shallow MultiPV search (`RANK_DEPTH`, ~0.2s). Ordering is all this
  stage is for, so depth spent here is depth wasted.

* **Stage 2 (`refine_candidates`)** re-analyses *only* the handful of
  moves the difficulty window actually selected, at full `SEARCH_DEPTH`,
  in a single search restricted with `root_moves`. Those are the scores
  handed to Gemini and rendered in the UI, so they stay accurate.

* The engine process is **opened once and reused**, guarded by a lock.
  Serialising is deliberate: on a fraction-of-a-CPU instance, concurrent
  Stockfish processes thrash one core and split the RAM budget, whereas
  one warm process gets the whole slice and keeps its hash between
  searches.

Measured on a 34-move middlegame: 7.5s -> ~0.8s, with full depth-15
accuracy preserved for every move that matters.

A note for anyone tempted to "just use MultiPV everywhere": ranking all
N moves with MultiPV=N is *not* faster than N separate searches (it was
benchmarked at 0.8-1.3x), because asking for N principal variations
suppresses most of the alpha-beta pruning that makes a search fast.
MultiPV only pays off at small N - which is exactly what stage 2 uses it
for. The win here comes from not searching deeply at all in stage 1.

Everything is tunable from the environment so the deployed instance can
be dialled down without a code change - see the constants below.
"""
import os
import threading

import chess
import chess.engine
import logging

logger = logging.getLogger(__name__)


def _env_int(name: str, default: int) -> int:
    """Read an int from the environment, falling back on anything unusable."""
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        logger.warning(f"⚠️ {name} is not an integer - using {default}")
        return default


# Overridable so a local dev box (or a non-Debian image) doesn't need the
# Docker layout. The default remains the path the Dockerfile installs to.
STOCKFISH_PATH = os.environ.get("STOCKFISH_PATH", "/usr/games/stockfish")

# Full search depth - used wherever the *number* matters: the candidate
# moves handed to Gemini, move grading, and the eval bar.
#
# Kept at 15 so playing strength and move grades are unchanged by the
# optimisation work. move_quality.CLASSIFY_DEPTH deliberately tracks this
# value: grading a move at a shallower depth than the AI *selected* it at
# produces visible incoherence (the AI plays its own top pick and then
# grades it an inaccuracy). Change this and both move together.
SEARCH_DEPTH = _env_int("STOCKFISH_DEPTH", 15)

# Stage-1 depth: ordering every legal move so the difficulty window can be
# sliced out of the list. Deliberately shallow - the scores this produces
# are never shown or handed to Gemini, they only decide which moves are
# "good ones" and which are "bad ones", and a shallow search already sorts
# that reliably. Raising it buys ordering fidelity at roughly 4x the cost
# per 2 ply; 10 keeps a middlegame ranking at ~0.5s instead of ~7s.
RANK_DEPTH = _env_int("STOCKFISH_RANK_DEPTH", 10)

# Engine resources. Defaults suit a small shared-CPU container; raise both
# on a real box. Hash is in MB and is the other big lever - too small and
# the engine re-searches positions it already solved.
ENGINE_THREADS = _env_int("STOCKFISH_THREADS", 1)
ENGINE_HASH_MB = _env_int("STOCKFISH_HASH_MB", 16)


class StockfishService:
    """
    Wraps a single long-lived Stockfish process.

    Thread safety: `chess.engine.SimpleEngine` is *not* safe to use from
    several threads at once, and this service is called from a few
    (`asyncio.to_thread` for move selection and eval, plus the grading
    pool in app.py). Every engine interaction therefore happens under
    `_lock`. See the module docstring for why serialising is the right
    call here rather than a process pool.
    """

    def __init__(self, path: str = STOCKFISH_PATH, depth: int = SEARCH_DEPTH):
        self.path = path
        self.depth = depth
        self._engine = None
        self._lock = threading.RLock()

    # ----- engine lifecycle -------------------------------------------------

    def _engine_unlocked(self):
        """Return the live engine, starting it if this is the first call.

        Caller must already hold `_lock`.
        """
        if self._engine is None:
            logger.info(f"♟️ Starting Stockfish ({self.path})")
            self._engine = chess.engine.SimpleEngine.popen_uci(self.path)
            try:
                self._engine.configure({
                    "Threads": ENGINE_THREADS,
                    "Hash": ENGINE_HASH_MB,
                })
            except Exception as e:
                # Not every build exposes both options; a default-configured
                # engine is still perfectly usable, so this is not fatal.
                logger.warning(f"⚠️ Could not configure Stockfish options: {e}")
        return self._engine

    def _reset_engine_unlocked(self):
        """Drop a dead or wedged engine so the next call starts a fresh one."""
        if self._engine is not None:
            try:
                self._engine.quit()
            except Exception:
                pass
            self._engine = None

    def _run(self, fn):
        """
        Run `fn(engine)` against the shared engine, once, retrying on a
        dead process.

        Stockfish can die under us - most commonly it is killed by the
        container's OOM reaper on a memory-tight host, and historically it
        segfaulted outright on a position that cannot occur in a real game
        (the side not to move left in check). Because the process is now
        long-lived rather than per-call, one such death would otherwise
        poison every later request, so a terminated engine is replaced and
        the call retried exactly once.
        """
        with self._lock:
            try:
                return fn(self._engine_unlocked())
            except chess.engine.EngineTerminatedError as e:
                logger.warning(f"⚠️ Stockfish terminated ({e}) - restarting and retrying once")
                self._reset_engine_unlocked()
                return fn(self._engine_unlocked())

    def close(self):
        """Shut the engine down (used on app shutdown)."""
        with self._lock:
            self._reset_engine_unlocked()

    # ----- analysis ---------------------------------------------------------

    def get_best_move(self, fen: str) -> str:
        """
        Given a FEN position, return the single best move in UCI format.
        """
        board = chess.Board(fen)

        result = self._run(lambda engine: engine.play(
            board, chess.engine.Limit(depth=self.depth)
        ))
        move = result.move

        if move is None:
            raise ValueError("Stockfish returned no move (game may be over)")

        logger.info(f"♟️ Stockfish selected move: {move.uci()}")
        return move.uci()

    @staticmethod
    def _sort_key(entry):
        # Four tiers so mates sort against centipawn scores sensibly:
        # winning mates above every cp score, losing mates below every cp
        # score, a faster mate ahead of a slower one in both directions,
        # and unevaluated fallbacks last. (Sorted with reverse=True.)
        if entry["mate_in"] is not None:
            if entry["mate_in"] > 0:
                return (3, -entry["mate_in"])
            return (1, entry["mate_in"])
        if entry["score"] is None:
            return (0, 0)
        return (2, entry["score"])

    def _analyse_moves(self, board, moves, depth):
        """
        Rank `moves` (a list of chess.Move) from one MultiPV search of
        `board`, restricted to those moves via UCI `searchmoves`.

        Returns entries shaped {"move": uci, "score": cp, "mate_in": n},
        best first. Scores are centipawns from the perspective of the side
        to move (positive = good for them); a forced mate sets "mate_in"
        instead and leaves "score" None.
        """
        infos = self._run(lambda engine: engine.analyse(
            board,
            chess.engine.Limit(depth=depth),
            multipv=len(moves),
            root_moves=moves,
        ))
        if isinstance(infos, dict):  # multipv=1 comes back unwrapped
            infos = [infos]

        ranked, seen = [], set()
        for info in infos:
            pv = info.get("pv") or []
            if not pv or pv[0] in seen:
                continue
            move = pv[0]
            seen.add(move)
            # POV of the side to move, i.e. whoever would play this move.
            # The original implementation pushed the move and took
            # `pov(not board.turn)` - the same perspective.
            score = info["score"].pov(board.turn)
            mate_in = score.mate()
            ranked.append({
                "move": move.uci(),
                "score": score.score() if mate_in is None else None,
                "mate_in": mate_in,
            })

        # Any move the engine didn't report a line for still has to appear:
        # difficulty windowing slices the list by position, so a move
        # missing from it is a move the weakest settings could never play.
        # They go last, behind everything actually evaluated.
        missing = [m for m in moves if m not in seen]
        if missing:
            logger.warning(
                f"⚠️ Stockfish returned {len(seen)}/{len(moves)} lines - "
                f"appending {len(missing)} unranked move(s) at the bottom"
            )
            for move in missing:
                ranked.append({"move": move.uci(), "score": None, "mate_in": None})

        ranked.sort(key=self._sort_key, reverse=True)
        return ranked

    def get_ranked_moves(self, fen: str, top_n: int = 5, depth: int = None) -> list[dict]:
        """
        Stage 1. Order legal moves best-first. `top_n=None` ranks every
        legal move, which is what move selection asks for - low difficulty
        settings deliberately pick from the *bottom* of the list.

        Runs at the shallow `RANK_DEPTH` by default. The scores it returns
        are good enough to sort by and NOT good enough to show: pass the
        chosen window through `refine_candidates()` before handing scores
        to Gemini or the UI.
        """
        board = chess.Board(fen)

        legal_moves = list(board.legal_moves)
        if not legal_moves:
            logger.info("♟️ No legal moves to rank (game is over)")
            return []

        ranked = self._analyse_moves(board, legal_moves, depth or RANK_DEPTH)

        logger.info(
            f"♟️ Ranked {len(ranked)} moves at depth {depth or RANK_DEPTH}, "
            f"top: {ranked[0] if ranked else 'none'}"
        )
        return ranked if top_n is None else ranked[:top_n]

    def refine_candidates(self, fen: str, candidates: list[dict], depth: int = None) -> list[dict]:
        """
        Stage 2. Re-analyse just the shortlisted moves at full depth and
        return them re-ordered with accurate scores.

        `candidates` is a slice of `get_ranked_moves()` output. Only these
        few moves are ever handed to Gemini, rendered in the analysis
        panel, or played - so they are the only ones worth a deep search,
        and there are few enough of them that one MultiPV search over just
        them is cheap.

        Illegal or unparseable entries are dropped. If nothing survives,
        the input is returned untouched so the caller still has something
        to play.
        """
        if not candidates:
            return candidates

        board = chess.Board(fen)
        moves = []
        for c in candidates:
            try:
                move = chess.Move.from_uci(c["move"])
            except (ValueError, KeyError, TypeError):
                continue
            if move in board.legal_moves:
                moves.append(move)

        if not moves:
            logger.warning("⚠️ No legal candidates left to refine - keeping shallow scores")
            return candidates

        refined = self._analyse_moves(board, moves, depth or self.depth)
        logger.info(
            f"♟️ Refined {len(refined)} candidate(s) at depth {depth or self.depth}, "
            f"top: {refined[0] if refined else 'none'}"
        )
        return refined

    def get_position_evaluation(self, fen: str) -> dict:
        """
        Evaluate the given position directly (no move applied) from White's
        absolute perspective - the standard convention for an eval bar
        (positive = White is better, negative = Black is better), regardless
        of whose turn it actually is.

        Returns {"score": cp, "mate_in": mate_in}. "score" is centipawns
        (None if a forced mate was found instead). "mate_in" is the number
        of moves to mate (positive = White mates, negative = Black mates),
        None if no forced mate was found.
        """
        board = chess.Board(fen)
        info = self._run(lambda engine: engine.analyse(
            board, chess.engine.Limit(depth=self.depth)
        ))
        score = info["score"].pov(chess.WHITE)
        mate_in = score.mate()
        cp = score.score() if mate_in is None else None
        return {"score": cp, "mate_in": mate_in}

    def analyse(self, board: chess.Board, limit, multipv: int = None):
        """
        Escape hatch for callers that need a raw analysis against the same
        shared engine - move_quality.py grades against this rather than
        opening a second Stockfish of its own.
        """
        if multipv is None:
            return self._run(lambda engine: engine.analyse(board, limit))
        return self._run(lambda engine: engine.analyse(board, limit, multipv=multipv))


# Global instance
stockfish_service = StockfishService()
