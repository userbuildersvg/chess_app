"""Persistent, exportable evidence for move-grade validation.

This is internal validation data, not a user-facing claim. Records contain
only chess positions, engine outputs, pseudonymous ownership, and provenance;
they contain no PGN, player prose, account id, or session credential.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from typing import Optional

import db
from learning_events import player_key

logger = logging.getLogger(__name__)
MAX_BUFFERED = 500


class AuditStore:
    def __init__(self, max_buffered: int = MAX_BUFFERED):
        self._rows = deque(maxlen=max_buffered)
        self._lock = threading.Lock()

    def record(self, *, identity: Optional[str], source_flow: str,
               fen_before: str, move_played: str, fen_after: Optional[str],
               side_to_move: Optional[str], player_color: Optional[str],
               engine_best: Optional[str], eval_before, eval_after,
               eval_perspective: str, engine_depth: Optional[int],
               grade_assigned: Optional[str], game_id: Optional[str] = None,
               correction_id: Optional[str] = None, key_decision: bool = False,
               provider: Optional[str] = None, model: Optional[str] = None,
               fallback: bool = False, timeout: bool = False) -> None:
        try:
            row = {
                "actor_key": player_key(identity),
                "game_id": game_id,
                "correction_id": correction_id,
                "source_flow": source_flow,
                "occurred_at": time.time(),
                "fen_before": fen_before,
                "move_played": move_played,
                "fen_after": fen_after,
                "side_to_move": side_to_move,
                "player_color": player_color,
                "engine_best": engine_best,
                "eval_before": _number(eval_before),
                "eval_after": _number(eval_after),
                "eval_perspective": eval_perspective,
                "engine_depth": engine_depth,
                "grade_assigned": grade_assigned,
                "key_decision": bool(key_decision),
                "provider": provider,
                "model": model,
                "fallback": bool(fallback),
                "timeout": bool(timeout),
            }
            with self._lock:
                self._rows.append(row)
            if db.configured():
                with db.connection() as conn:
                    conn.execute(
                        "INSERT INTO move_grade_audits "
                        "(actor_key, game_id, correction_id, source_flow, occurred_at, "
                        "fen_before, move_played, fen_after, side_to_move, player_color, "
                        "engine_best, eval_before, eval_after, eval_perspective, engine_depth, "
                        "grade_assigned, key_decision, provider, model, fallback, timeout) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, "
                        "%s, %s, %s, %s, %s, %s, %s, %s)",
                        tuple(row.values()),
                    )
        except Exception as exc:
            logger.debug("Move-grade audit persistence unavailable: %s", exc)

    def link_correction(self, *, identity: Optional[str], game_id: str,
                        fen_before: str, move_played: str, correction_id: str,
                        provider: Optional[str], model: Optional[str]) -> None:
        """Mark the matching grade as the decision used by a correction card."""
        actor = player_key(identity)
        with self._lock:
            for row in reversed(self._rows):
                if (row["actor_key"] == actor and row.get("game_id") == game_id
                        and row["fen_before"] == fen_before
                        and row["move_played"] == move_played
                        and row.get("correction_id") is None):
                    row.update({
                        "correction_id": correction_id,
                        "key_decision": True,
                        "provider": provider,
                        "model": model,
                    })
                    break
        if not db.configured():
            return
        try:
            with db.connection() as conn:
                conn.execute(
                    "UPDATE move_grade_audits SET correction_id = %s, key_decision = true, "
                    "provider = %s, model = %s WHERE id = ("
                    "SELECT id FROM move_grade_audits WHERE actor_key = %s AND game_id = %s "
                    "AND fen_before = %s AND move_played = %s AND correction_id IS NULL "
                    "ORDER BY occurred_at DESC LIMIT 1)",
                    (correction_id, provider, model, actor, game_id, fen_before, move_played),
                )
        except Exception as exc:
            logger.debug("Move-grade audit correction link unavailable: %s", exc)

    def recent(self, limit: int = 50) -> list:
        with self._lock:
            return list(self._rows)[-max(0, min(limit, MAX_BUFFERED)):]

    def clear(self) -> None:
        with self._lock:
            self._rows.clear()


def _number(value):
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, dict):
        # Audit comparison uses the mover-frame values from quality where
        # available. White-frame mate objects are not silently converted.
        return value.get("score")
    return None


audits = AuditStore()


__all__ = ["AuditStore", "audits"]
