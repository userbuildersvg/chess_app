"""
learning_service.py

Persistent, cross-game "learning" layer for the chess platform.

Neither Stockfish nor Gemini can be retrained live, so "learning" here means
a small database-backed feedback loop instead: every move (human or AI) gets
logged with enough context (position, eval swing, who chose it, how the
game it belonged to eventually turned out) that later games can look back
at it. Two things build on that raw log:

  - An opponent profile, computed on demand straight from the games/moves
    tables (never maintained as a separate running total, so it can never
    silently drift out of sync with the raw data) - how the human tends to
    open, how often they blunder, which color they favor, win/loss/draw
    record. It is scoped to ONE owner, and that scoping is not cosmetic:
    `get_prompt_context_summary()` goes straight into Gemini's prompt on
    every move and every chat turn, so an unscoped version would narrate one
    player's record and blunder rate to a different player.
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

OWNERSHIP
---------
Every instance is bound to one owner - the identity string `identity.py`
resolved the request to, `guest:8f2a1c…` or `user:42`, stored verbatim and
never parsed here. That is the same seam the rest of the app uses, so this
file needs no notion of what an account is.

`app.py`'s `learning_for()` is the single place that decides which owner a
request gets, and it now hands everyone a real one. Guests used to receive a
subclass backed by an in-memory database (`guest_learning.py`, deleted) so
that nothing they did was saved. That changed deliberately: guest history is
now written like anyone else's, so that signing up can CLAIM it - see
`claim_guest_games()`. The cost is that anonymous rows accumulate, which is
what `purge_unclaimed_guest_games()` exists to bound.

STORAGE
-------
Postgres (Neon), through the shared pool in `db.py`. It was SQLite at
`data/learning.db`, on Render's ephemeral disk, so every redeploy silently
reset all of it - see DEPLOY.md.
"""
import json
import logging
import os
from datetime import datetime, timezone
from typing import Optional

import db

logger = logging.getLogger(__name__)

# Kept as a name because tests read it to assert the old SQLite file is never
# written. It now points at a file nothing creates, which is what those
# assertions should find.
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "learning.db")

# How many plies (half-moves) of the opening to fingerprint for opponent
# habit-tracking. Short enough to bucket real repeated habits, long enough
# to mean something beyond "everyone plays e4 or d4".
OPENING_SIGNATURE_PLIES = 6

# An eval swing (centipawns, sign-adjusted to the mover's own perspective)
# beyond this magnitude, on the mover's own move, counts as a blunder.
BLUNDER_THRESHOLD_CP = 150

# How long an unclaimed guest's games survive. Long enough that someone who
# plays, leaves and comes back a fortnight later still has a history to claim
# by signing up; short enough that an anonymous public URL does not grow the
# table forever.
GUEST_RETENTION_DAYS = int(os.environ.get("GUEST_RETENTION_DAYS", "30"))

EMPTY_OPPONENT = {
    "games_played": 0, "as_white": 0, "as_black": 0,
    "wins": 0, "losses": 0, "draws": 0,
    "blunder_rate": None, "top_openings": [],
}
EMPTY_AI_SELF = {
    "total_ai_moves": 0, "gemini_moves": 0, "fallback_moves": 0,
    "gemini_win_rate": None, "fallback_win_rate": None,
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class LearningService:
    """One player's view of the learning store. `owner` is an identity string."""

    def __init__(self, owner: str, connect=None):
        self.owner = owner
        # For tests, mirroring AuthService: a callable returning a connection
        # to a throwaway schema. See db.temporary_schema().
        self._connect_fn = connect

    def _connect(self):
        if self._connect_fn is not None:
            return self._connect_fn()
        return db.connection()

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def start_game(self, human_color: str) -> Optional[int]:
        """
        Call once per new/reset game. Returns the new game's id, or None if
        the store is unreachable.

        None rather than an exception because losing the database must
        degrade the app to "plays fine, records nothing" rather than break
        every board - and callers already treat a None game id as "do not
        log".
        """
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "INSERT INTO games (owner, started_at, human_color, result)"
                    " VALUES (%s, %s, %s, 'in_progress') RETURNING id",
                    (self.owner, _now(), human_color),
                ).fetchone()
                return row[0]
        except Exception as e:
            logger.warning(f"⚠️ Failed to start a learning game row: {e}")
            return None

    def update_move_quality(self, game_id: Optional[int], ply: int, quality: Optional[dict]) -> None:
        """
        Attach a move grade to an already-recorded move.

        Separate from record_move because grading is asynchronous - the move
        row is written the moment the move is played, and its grade lands a
        second or so later. Storing it means grades survive a server restart
        and stay available for post-game review, rather than living only in
        the in-memory game history.
        """
        if quality is None or game_id is None:
            return
        try:
            with self._connect() as conn:
                conn.execute(
                    "UPDATE moves SET quality = %s WHERE game_id = %s AND ply = %s",
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
                    "SELECT quality FROM moves WHERE game_id = %s ORDER BY ply ASC",
                    (game_id,),
                ).fetchall()
            return [json.loads(r[0]) if r[0] else None for r in rows]
        except Exception as e:
            logger.warning(f"⚠️ Failed to read stored move qualities for game {game_id}: {e}")
            return []

    def record_move(
        self,
        game_id: Optional[int],
        ply: int,
        color: str,
        mover: str,
        move_uci: str,
        san: Optional[str],
        fen_before: str,
        fen_after: str,
        eval_before: Optional[dict],
        eval_after: Optional[dict],
        opponent_profile: Optional[str] = None,
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

        ON CONFLICT closes that race from the other side: if the grade's
        UPDATE still ran first and matched nothing, the row written here
        carries the grade already, and a genuine duplicate ply updates rather
        than raising. SQLite had no such guard - the (game_id, ply) key it
        relies on is new with Postgres.
        """
        if game_id is None:
            return
        eb = eval_before.get("score") if eval_before else None
        ea = eval_after.get("score") if eval_after else None
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO moves (
                        game_id, ply, color, mover, move_uci, san,
                        fen_before, fen_after, eval_before, eval_after,
                        opponent_profile, source, explanation, quality
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (game_id, ply) DO UPDATE SET
                        color = EXCLUDED.color,
                        mover = EXCLUDED.mover,
                        move_uci = EXCLUDED.move_uci,
                        san = EXCLUDED.san,
                        fen_before = EXCLUDED.fen_before,
                        fen_after = EXCLUDED.fen_after,
                        eval_before = EXCLUDED.eval_before,
                        eval_after = EXCLUDED.eval_after,
                        opponent_profile = EXCLUDED.opponent_profile,
                        source = EXCLUDED.source,
                        explanation = EXCLUDED.explanation,
                        quality = COALESCE(EXCLUDED.quality, moves.quality)
                    """,
                    (game_id, ply, color, mover, move_uci, san, fen_before, fen_after,
                     eb, ea, opponent_profile, source, explanation,
                     json.dumps(quality) if quality else None),
                )
        except Exception as e:
            logger.warning(f"⚠️ Failed to record move {move_uci} (game {game_id}, ply {ply}): {e}")

    def finalize_game(self, game_id: Optional[int], result: str, termination: str) -> None:
        """
        Call once, the moment game.is_game_over() first becomes true.
        result: 'white_win' | 'black_win' | 'draw'
        termination: 'checkmate' | 'stalemate' | 'insufficient_material' |
                     '75_move_rule' | 'fivefold_repetition' | 'other'
        """
        if game_id is None:
            return
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT move_uci FROM moves WHERE game_id = %s ORDER BY ply ASC LIMIT %s",
                    (game_id, OPENING_SIGNATURE_PLIES),
                ).fetchall()
                signature = ",".join(r[0] for r in rows) if rows else None
                conn.execute(
                    "UPDATE games SET ended_at = %s, result = %s, termination = %s,"
                    " opening_signature = %s WHERE id = %s",
                    (_now(), result, termination, signature, game_id),
                )
        except Exception as e:
            logger.warning(f"⚠️ Failed to finalize game {game_id}: {e}")

    # ------------------------------------------------------------------
    # Reading - opponent profile (query-time aggregation only)
    # ------------------------------------------------------------------

    def get_opponent_summary(self) -> dict:
        try:
            with self._connect() as conn:
                games = conn.execute(
                    "SELECT human_color, result, opening_signature FROM games"
                    " WHERE owner = %s AND result != 'in_progress'",
                    (self.owner,),
                ).fetchall()
                total = len(games)
                if total == 0:
                    return dict(EMPTY_OPPONENT)

                as_white = sum(1 for g in games if g[0] == "white")
                as_black = total - as_white

                def human_result(g):
                    if g[1] == "draw":
                        return "draw"
                    won_color = "white" if g[1] == "white_win" else "black"
                    return "win" if won_color == g[0] else "loss"

                wins = sum(1 for g in games if human_result(g) == "win")
                losses = sum(1 for g in games if human_result(g) == "loss")
                draws = sum(1 for g in games if human_result(g) == "draw")

                # Joined to games and filtered by owner. The SQLite version
                # did neither, which was harmless only because each player
                # had a database to themselves.
                human_moves = conn.execute(
                    "SELECT m.eval_before, m.eval_after, m.color"
                    " FROM moves m JOIN games g ON g.id = m.game_id"
                    " WHERE g.owner = %s AND m.mover = 'human'"
                    "   AND m.eval_before IS NOT NULL AND m.eval_after IS NOT NULL",
                    (self.owner,),
                ).fetchall()

            blunders = 0
            scored = 0
            for eval_before, eval_after, color in human_moves:
                scored += 1
                # Swing from the mover's OWN perspective - eval is stored
                # from White's perspective, so flip the sign for Black.
                swing = eval_after - eval_before
                if color == "black":
                    swing = -swing
                if swing <= -BLUNDER_THRESHOLD_CP:
                    blunders += 1
            blunder_rate = (blunders / scored) if scored else None

            opening_counts: dict = {}
            for g in games:
                if g[2]:
                    opening_counts[g[2]] = opening_counts.get(g[2], 0) + 1
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
        except Exception as e:
            logger.warning(f"⚠️ Failed to build opponent summary: {e}")
            return dict(EMPTY_OPPONENT)

    def get_ai_self_summary(self) -> dict:
        try:
            with self._connect() as conn:
                ai_moves = conn.execute(
                    """
                    SELECT m.source, g.result, g.human_color
                    FROM moves m JOIN games g ON g.id = m.game_id
                    WHERE g.owner = %s AND m.mover = 'ai' AND g.result != 'in_progress'
                    """,
                    (self.owner,),
                ).fetchall()

            gemini_moves = [m for m in ai_moves if m[0] == "gemini"]
            fallback_moves = [m for m in ai_moves if m[0] == "stockfish_fallback"]

            def ai_won(m):
                if m[1] == "draw":
                    return None
                ai_color = "black" if m[2] == "white" else "white"
                won_color = "white" if m[1] == "white_win" else "black"
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
        except Exception as e:
            logger.warning(f"⚠️ Failed to build AI self summary: {e}")
            return dict(EMPTY_AI_SELF)

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
    # Claiming - a guest's history becomes an account's
    # ------------------------------------------------------------------

    @staticmethod
    def claim_guest_games(guest_identity: str, user_identity: str, connect=None) -> int:
        """
        Hand every game owned by `guest_identity` to `user_identity`, and
        return how many moved.

        Called once, when someone signs up or signs in from a browser that
        has been playing as a guest. Three properties make it safe:

        1. One-way, and once per guest identity. `claimed_guests` is checked
           and written inside the same transaction, so a browser shared by
           two people cannot hand the first person's games to the second -
           the second sign-in finds the identity already claimed and moves
           nothing.
        2. Purely additive. Games are append-only records of things that
           happened, so merging a device's guest history into an account can
           never conflict with what that account already holds; there is no
           "which copy wins" question to get wrong.
        3. It rewrites ownership on `games` only. Moves hang off game_id and
           follow automatically, so no move row is ever touched.

        Static because it acts across two owners and belongs to neither.
        """
        opener = connect or db.connection
        try:
            with opener() as conn:
                with conn.transaction():
                    already = conn.execute(
                        "SELECT user_identity FROM claimed_guests WHERE guest_identity = %s",
                        (guest_identity,),
                    ).fetchone()
                    if already is not None:
                        logger.info(f"ℹ️ {guest_identity} was already claimed - nothing to merge")
                        return 0
                    moved = conn.execute(
                        "UPDATE games SET owner = %s WHERE owner = %s",
                        (user_identity, guest_identity),
                    ).rowcount
                    import time as _time
                    conn.execute(
                        "INSERT INTO claimed_guests (guest_identity, user_identity, claimed_at)"
                        " VALUES (%s, %s, %s)",
                        (guest_identity, user_identity, _time.time()),
                    )
            logger.info(f"🔗 Claimed {moved} guest game(s) for {user_identity}")
            return moved
        except Exception as e:
            logger.warning(f"⚠️ Failed to claim guest games for {guest_identity}: {e}")
            return 0

    @staticmethod
    def purge_unclaimed_guest_games(max_age_days: int = GUEST_RETENTION_DAYS, connect=None) -> int:
        """
        Delete guest-owned games older than `max_age_days`, and return how
        many went.

        The price of making guest history claimable is that every anonymous
        visitor now leaves rows behind. This bounds that. Moves cascade with
        their game, and a guest whose history has already been claimed is not
        affected - claiming rewrites `owner` to `user:…`, so those rows no
        longer match.

        `started_at` is an ISO-8601 string, which sorts lexicographically in
        the same order it sorts chronologically, so a plain text comparison
        is correct here.
        """
        from datetime import timedelta
        cutoff = (datetime.now(timezone.utc) - timedelta(days=max_age_days)).isoformat()
        opener = connect or db.connection
        try:
            with opener() as conn:
                removed = conn.execute(
                    "DELETE FROM games WHERE owner LIKE 'guest:%%' AND started_at < %s",
                    (cutoff,),
                ).rowcount
            if removed:
                logger.info(f"🧹 Purged {removed} unclaimed guest game(s) older than {max_age_days}d")
            return removed
        except Exception as e:
            logger.warning(f"⚠️ Failed to purge unclaimed guest games: {e}")
            return 0

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

        The pool is ACCOUNT-OWNED GAMES ONLY, across all accounts - the one
        read here that is neither owner-scoped nor unscoped. Two reasons in
        tension, settled this way:

        - Not per-owner, because a position recurring byte-for-byte is rare
          enough that one player's own history would essentially never
          match. Scoping it per owner switches the feature off rather than
          isolating it.
        - Not everyone, because guests are anonymous and unlimited. Letting
          guest games into the pool means anyone who can open the URL can
          shift what the AI plays against everybody else, and can do it
          repeatedly. Requiring an account does not make that impossible,
          but it makes it cost something.

        Guests still benefit from the pool; they just do not feed it until
        they sign up, at which point claiming moves their games into it.
        """
        if not candidates or not fen_before:
            return candidates

        try:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT m.move_uci, g.result, g.human_color
                    FROM moves m JOIN games g ON g.id = m.game_id
                    WHERE m.fen_before = %s AND m.mover = 'ai' AND m.source = 'gemini'
                      AND g.result != 'in_progress' AND g.owner LIKE 'user:%%'
                    """,
                    (fen_before,),
                ).fetchall()
        except Exception as e:
            logger.warning(f"⚠️ Candidate reweighting skipped: {e}")
            return candidates

        if not rows:
            return candidates

        def outcome_score(move_uci: str) -> int:
            wins = losses = 0
            for r in rows:
                if r[0] != move_uci or r[1] == "draw":
                    continue
                ai_color = "black" if r[2] == "white" else "white"
                won_color = "white" if r[1] == "white_win" else "black"
                if won_color == ai_color:
                    wins += 1
                else:
                    losses += 1
            return wins - losses

        return sorted(candidates, key=lambda c: -outcome_score(c["move"]))
