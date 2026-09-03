"""
learning_service.py

Persistent, cross-game "learning" layer for the chess platform.

Neither Stockfish nor Gemini can be retrained live, so "learning" here means
a small SQLite-backed feedback loop instead: every move (human or AI) gets
logged with enough context (position, eval swing, who chose it, how the
game it belonged to eventually turned out) that later games can look back
at it. Two things build on that raw log:

  - An opponent profile, computed on demand straight from the games/moves
    tables (never maintained as a separate running total, so it can never
    silently drift out of sync with the raw data) - how the human tends to
    open, how often they blunder, which color they favor, win/loss/draw
    record.
  - A narrow, honest form of self-improvement: when the AI faces an EXACT
    position (by FEN) it has chosen a Gemini-picked move from before, and
    that game's result is known, future visits to that same position nudge
    the candidate ordering toward whichever move actually won more often.
    This only ever re-orders WITHIN Stockfish's difficulty-windowed
    candidates - it never overrides Stockfish's own evaluation or pulls in
    a move outside the window - and it has zero opinion about a
    position/move pair it's never recorded before, which is the common
    case early on. This is deliberately modest: a real position rarely
    recurs byte-for-byte across unrelated games, so don't expect this to
    "kick in" meaningfully until there's a real history built up.

Storage lives at data/learning.db, inside the directory that's already
bind-mounted whole into the backend container (see docker-compose.yml's
`./:/app` mount under the chess-ai-platform service) - so it persists
across container restarts and rebuilds automatically, with no extra Docker
volume needed. Add `data/` to .gitignore so personal game history never
gets committed or shipped in a zip.
"""
import sqlite3
import os
import json
import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "learning.db")

# How many plies (half-moves) of the opening to fingerprint for opponent
# habit-tracking. Short enough to bucket real repeated habits, long enough
# to mean something beyond "everyone plays e4 or d4".
OPENING_SIGNATURE_PLIES = 6

# An eval swing (centipawns, sign-adjusted to the mover's own perspective)
# beyond this magnitude, on the mover's own move, counts as a blunder.
BLUNDER_THRESHOLD_CP = 150


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class LearningService:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        os.makedirs(os.path.dirname(self.db_path) or ".", exist_ok=True)
        self._init_schema()

    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _init_schema(self):
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS games (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    started_at TEXT NOT NULL,
                    ended_at TEXT,
                    human_color TEXT NOT NULL,
                    opening_signature TEXT,
                    result TEXT NOT NULL DEFAULT 'in_progress',
                    termination TEXT
                );
                CREATE TABLE IF NOT EXISTS moves (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    game_id INTEGER NOT NULL REFERENCES games(id),
                    ply INTEGER NOT NULL,
                    color TEXT NOT NULL,
                    mover TEXT NOT NULL,
                    move_uci TEXT NOT NULL,
                    san TEXT,
                    fen_before TEXT,
                    fen_after TEXT,
                    eval_before REAL,
                    eval_after REAL,
                    difficulty INTEGER,
                    source TEXT,
                    explanation TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_moves_game ON moves(game_id);
                CREATE INDEX IF NOT EXISTS idx_moves_fen_before ON moves(fen_before);
                """
            )
            # Added after the table already existed in the wild, so it has
            # to be a migration rather than a column in the CREATE above -
            # an existing learning.db would otherwise never gain it.
            # SQLite has no "ADD COLUMN IF NOT EXISTS", hence the pragma.
            existing = {row["name"] for row in conn.execute("PRAGMA table_info(moves)")}
            if "quality" not in existing:
                conn.execute("ALTER TABLE moves ADD COLUMN quality TEXT")

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def start_game(self, human_color: str) -> int:
        """Call once per new/reset game. Returns the new game's id."""
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO games (started_at, human_color, result) VALUES (?, ?, 'in_progress')",
                (_now(), human_color),
            )
            return cur.lastrowid

    def update_move_quality(self, game_id: int, ply: int, quality: Optional[dict]) -> None:
        """
        Attach a move grade to an already-recorded move.

        Separate from record_move because grading is asynchronous - the move
        row is written the moment the move is played, and its grade lands a
        second or so later. Storing it means grades survive a server restart
        and stay available for post-game review, rather than living only in
        the in-memory game history.
        """
        if quality is None:
            return
        try:
            with self._connect() as conn:
                conn.execute(
                    "UPDATE moves SET quality = ? WHERE game_id = ? AND ply = ?",
                    (json.dumps(quality), game_id, ply),
                )
        except Exception as e:
            logger.warning(f"⚠️ Failed to persist move quality (game {game_id}, ply {ply}): {e}")

    def get_game_qualities(self, game_id: int) -> list:
        """Stored grades for one game, ordered by ply. Entries may be None
        for moves that were played while grading was switched off."""
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT ply, quality FROM moves WHERE game_id = ? ORDER BY ply ASC",
                    (game_id,),
                ).fetchall()
            return [json.loads(r["quality"]) if r["quality"] else None for r in rows]
        except Exception as e:
            logger.warning(f"⚠️ Failed to read stored move qualities for game {game_id}: {e}")
            return []

    def record_move(
        self,
        game_id: int,
        ply: int,
        color: str,
        mover: str,
        move_uci: str,
        san: Optional[str],
        fen_before: str,
        fen_after: str,
        eval_before: Optional[dict],
        eval_after: Optional[dict],
        difficulty: Optional[int] = None,
        source: Optional[str] = None,
        explanation: Optional[str] = None,
        quality: Optional[dict] = None,
    ) -> None:
        """
        Log one move. eval_before/eval_after are {"score": cp, "mate_in": n}
        dicts as returned by StockfishService.get_position_evaluation() (or
        None if unavailable) - only the centipawn "score" is persisted
        (mate-in positions are extreme enough that blunder-thresholding on
        them doesn't add anything useful).

        `quality` is the move grade, when one is already known by the time
        the move is logged. Grading is asynchronous and usually finishes
        after this insert, in which case it arrives later via
        update_move_quality() - but book moves are graded without touching
        the engine and so finish almost instantly, which used to leave their
        UPDATE racing ahead of this INSERT and matching no row at all.
        """
        eb = eval_before.get("score") if eval_before else None
        ea = eval_after.get("score") if eval_after else None
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO moves (
                    game_id, ply, color, mover, move_uci, san,
                    fen_before, fen_after, eval_before, eval_after,
                    difficulty, source, explanation, quality
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (game_id, ply, color, mover, move_uci, san, fen_before, fen_after,
                 eb, ea, difficulty, source, explanation,
                 json.dumps(quality) if quality else None),
            )

    def finalize_game(self, game_id: int, result: str, termination: str) -> None:
        """
        Call once, the moment game.is_game_over() first becomes true.
        result: 'white_win' | 'black_win' | 'draw'
        termination: 'checkmate' | 'stalemate' | 'insufficient_material' |
                     '75_move_rule' | 'fivefold_repetition' | 'other'
        """
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT move_uci FROM moves WHERE game_id = ? ORDER BY ply ASC LIMIT ?",
                (game_id, OPENING_SIGNATURE_PLIES),
            ).fetchall()
            signature = ",".join(r["move_uci"] for r in rows) if rows else None
            conn.execute(
                "UPDATE games SET ended_at = ?, result = ?, termination = ?, opening_signature = ? WHERE id = ?",
                (_now(), result, termination, signature, game_id),
            )

    # ------------------------------------------------------------------
    # Reading - opponent profile (query-time aggregation only)
    # ------------------------------------------------------------------

    def get_opponent_summary(self) -> dict:
        with self._connect() as conn:
            games = conn.execute("SELECT * FROM games WHERE result != 'in_progress'").fetchall()
            total = len(games)
            if total == 0:
                return {
                    "games_played": 0, "as_white": 0, "as_black": 0,
                    "wins": 0, "losses": 0, "draws": 0,
                    "blunder_rate": None, "top_openings": [],
                }

            as_white = sum(1 for g in games if g["human_color"] == "white")
            as_black = total - as_white

            def human_result(g):
                if g["result"] == "draw":
                    return "draw"
                won_color = "white" if g["result"] == "white_win" else "black"
                return "win" if won_color == g["human_color"] else "loss"

            wins = sum(1 for g in games if human_result(g) == "win")
            losses = sum(1 for g in games if human_result(g) == "loss")
            draws = sum(1 for g in games if human_result(g) == "draw")

            human_moves = conn.execute(
                "SELECT eval_before, eval_after, color FROM moves "
                "WHERE mover = 'human' AND eval_before IS NOT NULL AND eval_after IS NOT NULL"
            ).fetchall()
            blunders = 0
            scored = 0
            for m in human_moves:
                scored += 1
                # Swing from the mover's OWN perspective - eval is stored
                # from White's perspective, so flip the sign for Black.
                swing = m["eval_after"] - m["eval_before"]
                if m["color"] == "black":
                    swing = -swing
                if swing <= -BLUNDER_THRESHOLD_CP:
                    blunders += 1
            blunder_rate = (blunders / scored) if scored else None

            opening_counts: dict = {}
            for g in games:
                if g["opening_signature"]:
                    opening_counts[g["opening_signature"]] = opening_counts.get(g["opening_signature"], 0) + 1
            top_openings = sorted(opening_counts.items(), key=lambda kv: -kv[1])[:3]

            return {
                "games_played": total,
                "as_white": as_white,
                "as_black": as_black,
                "wins": wins,
                "losses": losses,
                "draws": draws,
                "blunder_rate": blunder_rate,
                "top_openings": [{"moves": sig, "count": count} for sig, count in top_openings],
            }

    def get_ai_self_summary(self) -> dict:
        with self._connect() as conn:
            ai_moves = conn.execute(
                """
                SELECT m.source, g.result, g.human_color
                FROM moves m JOIN games g ON g.id = m.game_id
                WHERE m.mover = 'ai' AND g.result != 'in_progress'
                """
            ).fetchall()
            gemini_moves = [m for m in ai_moves if m["source"] == "gemini"]
            fallback_moves = [m for m in ai_moves if m["source"] == "stockfish_fallback"]

            def ai_won(m):
                if m["result"] == "draw":
                    return None
                ai_color = "black" if m["human_color"] == "white" else "white"
                won_color = "white" if m["result"] == "white_win" else "black"
                return won_color == ai_color

            def win_rate(moves):
                decided = [ai_won(m) for m in moves if ai_won(m) is not None]
                return (sum(decided) / len(decided)) if decided else None

            return {
                "total_ai_moves": len(ai_moves),
                "gemini_moves": len(gemini_moves),
                "fallback_moves": len(fallback_moves),
                "gemini_win_rate": win_rate(gemini_moves),
                "fallback_win_rate": win_rate(fallback_moves),
            }

    def get_learning_summary(self) -> dict:
        """Combined payload for the frontend's live Learning panel."""
        return {
            "opponent": self.get_opponent_summary(),
            "ai_self": self.get_ai_self_summary(),
        }

    def get_prompt_context_summary(self) -> str:
        """
        A short natural-language blurb to hand to Gemini (both for move
        explanations and the mid-game chat feature) so it can actually
        reference what's been learned. Deliberately terse - this rides
        along in every request, so it stays a sentence or two, not a report.
        """
        opp = self.get_opponent_summary()
        if opp["games_played"] == 0:
            return "No history yet with this opponent - this is their first tracked game."
        parts = [
            f"Across {opp['games_played']} tracked games, the human has won {opp['wins']}, "
            f"lost {opp['losses']}, and drawn {opp['draws']}."
        ]
        if opp["blunder_rate"] is not None:
            parts.append(
                f"Their blunder rate (eval swings of {BLUNDER_THRESHOLD_CP}cp+ against them) "
                f"is about {opp['blunder_rate']:.0%}."
            )
        if opp["top_openings"]:
            top = opp["top_openings"][0]
            parts.append(f"Their most common opening start is {top['moves']} ({top['count']} games).")
        return " ".join(parts)

    # ------------------------------------------------------------------
    # Candidate re-weighting - the "AI's own move choices shift" piece
    # ------------------------------------------------------------------

    def reweight_candidates(self, fen_before: str, candidates: list) -> list:
        """
        Given Stockfish's already-difficulty-windowed candidate list
        (`[{"move": uci, "score": cp, "mate_in": n}, ...]`) for the current
        position, nudge the ordering using exact-position history: for each
        candidate move, look up past finalized games where the AI chose
        that exact move from this exact fen_before via Gemini, and how
        those games turned out. A move that's won more than it's lost from
        here moves toward the front of its tier; one that's lost more moves
        toward the back. Ties (including the common "no history at all"
        case) keep Stockfish's original relative order, since Python's sort
        is stable.
        """
        if not candidates or not fen_before:
            return candidates

        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT m.move_uci, g.result, g.human_color
                FROM moves m JOIN games g ON g.id = m.game_id
                WHERE m.fen_before = ? AND m.mover = 'ai' AND m.source = 'gemini'
                  AND g.result != 'in_progress'
                """,
                (fen_before,),
            ).fetchall()

        if not rows:
            return candidates

        def outcome_score(move_uci: str) -> int:
            wins = losses = 0
            for r in rows:
                if r["move_uci"] != move_uci or r["result"] == "draw":
                    continue
                ai_color = "black" if r["human_color"] == "white" else "white"
                won_color = "white" if r["result"] == "white_win" else "black"
                if won_color == ai_color:
                    wins += 1
                else:
                    losses += 1
            return wins - losses

        return sorted(candidates, key=lambda c: -outcome_score(c["move"]))


# Global instance, mirroring the stockfish_service module-level pattern.
learning_service = LearningService()
