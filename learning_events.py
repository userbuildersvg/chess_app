"""
Privacy-bounded product instrumentation for the Post-Mortem correction loop.

WHY THIS IS TEN LINES OF STORAGE AND NOT A VENDOR
-------------------------------------------------
The audit in CLAUDE.md §20 established that this product has **no analytics of
any kind** - no GA, no Plausible, no PostHog, no pixel, no custom telemetry -
and the brief for this work was explicit that the absence is not a reason to
go and add one. But the learning funnel is the one thing worth counting: the
whole product thesis is that a player corrects a pattern, and "did they get
from a diagnosis to a passed re-test" is not answerable without recording that
they did.

Events use a fixed vocabulary and a fixed scalar property allow-list. Raw
PGNs, file names, chat, and player intent are never accepted. When Postgres is
configured, events are also written to `product_events` so a beta cohort
survives process restarts and 14-day return can be measured. The bounded
in-process buffer remains as the no-database/dev fallback.

WHAT IS NEVER RECORDED
----------------------
**The identity string.** `identity.py` is explicit that it is a credential.
Events carry an HMAC pseudonym instead. It is stable across restarts when
`ANALYTICS_HASH_KEY` or `SESSION_COOKIE_SECRET` is configured, but cannot be
reversed or used to authenticate.

Also never recorded: anything the player typed. Intent free-text is a sentence
about a chess position, but it is still their words, and a funnel does not
need it - `intent_submitted` records *that* they answered and which preset, if
any, they picked. The sentence itself lives on the correction card, where the
player can see it, and nowhere else.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import secrets
import threading
import time
from collections import Counter, defaultdict, deque
from typing import Optional

import db

logger = logging.getLogger(__name__)

# The funnel. A fixed vocabulary for the same reason the themes are fixed: an
# event name that varies by call site cannot be counted. Anything not in here
# is refused rather than recorded, so a typo shows up as a rejection instead
# of as a silently missing step.
EVENTS = (
    "pgn_imported",
    "game_analysis_started",
    "game_analysis_completed",
    "key_decision_selected",
    "player_intention_submitted",
    "correction_generation_started",
    "correction_generated",
    "correction_viewed",
    "alternative_move_played",
    "fresh_practice_opened",
    "hint_requested",
    "practice_move_attempted",
    "practice_completed",
    "correction_card_completed",
    "diagnosis_disagreed",
    "correction_flow_abandoned",
    "user_returned_with_game",
    "correction_flow_error",
    "ai_move_generation_started",
    "ai_move_generation_completed",
    "engine_analysis_started",
    "engine_analysis_completed",
    "llm_request_started",
    "llm_request_completed",
    "move_rendered",
    "explanation_rendered",
    # Guided Play in the real game (guided_play.py): the toggle, and the two
    # things a guided AI move produces. Difficulty and ply travel with them;
    # the explanation text itself never does.
    "guided_play_enabled",
    "guided_play_disabled",
    "ai_move_explanation_generated",
    "guided_watchout_generated",
    # Play -> Review handoff ("Review this game", CLAUDE.md §32). The browser
    # reports the click and the handoff; the server reports the scan.
    "play_game_completed",
    "review_this_game_clicked",
    "play_game_review_handoff_started",
    "play_game_review_handoff_completed",
    "play_game_review_handoff_failed",
    "play_game_analysis_started",
    "play_game_analysis_completed",
    # Original vocabulary remains accepted for older clients.
    "critical_decision_opened",
    "intent_submitted",
    "diagnosis_viewed",
    "diagnosis_accepted",
    "diagnosis_rejected",
    "diagnosis_fallback",
    "diagnosis_rejected_unsupported",
    "correction_created",
    "pattern_recurred",
    "branch_started",
    "branch_matched_best",
    "practice_started",
    "practice_passed",
    "practice_failed",
    "hint_used",
)

ALLOWED_PROPERTIES = frozenset({
    "game_id", "correction_id", "node_id", "theme", "ply_index",
    "move_number", "side_to_move", "player_color", "source_mode",
    "duration_ms", "request_ms", "engine_ms", "llm_ms", "render_ms",
    "depth", "provider", "model", "fresh_practice_generated", "hint_used",
    "completed", "error_code", "error_category", "operation", "outcome",
    "occurrences", "attempt", "matched_best", "analysed_moves",
    "total_moves", "skipped_moves", "fallback", "timeout", "free_text",
    "preset", "difficulty", "guided", "source", "result", "termination",
})

MAX_BUFFERED = 1000
MAX_QUERY_ROWS = 20_000

# Stable in production so a 14-day return is measurable. HMAC is domain-
# separated from the cookie signature. Without either secret, local dev still
# records events but actor ids intentionally change at restart.
_HASH_SECRET_TEXT = os.environ.get("ANALYTICS_HASH_KEY") or os.environ.get(
    "SESSION_COOKIE_SECRET"
)
_HASH_SECRET = (_HASH_SECRET_TEXT.encode("utf-8") if _HASH_SECRET_TEXT
                else secrets.token_bytes(32))
ACTOR_IDS_STABLE = bool(_HASH_SECRET_TEXT)


def player_key(identity: Optional[str]) -> str:
    """A stable, non-reversible stand-in for a credential-bearing identity."""
    if not identity:
        return "anon"
    return hmac.new(
        _HASH_SECRET,
        b"zugzwang-product-analytics-v1\0" + identity.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()[:20]


def _safe_props(props: dict) -> dict:
    """The telemetry privacy boundary: fixed names, scalars, bounded strings."""
    out = {}
    for key, value in props.items():
        if key not in ALLOWED_PROPERTIES:
            continue
        if not isinstance(value, (str, int, float, bool, type(None))):
            continue
        out[key] = value[:120] if isinstance(value, str) else value
    return out


class EventSink:
    def __init__(self, max_buffered: int = MAX_BUFFERED):
        self._events: deque = deque(maxlen=max_buffered)
        self._counts: Counter = Counter()
        self._lock = threading.Lock()

    def emit(self, name: str, identity: Optional[str] = None, **props) -> bool:
        """
        Record one funnel step. Returns False for an unknown event name.

        Never raises. Instrumentation that can break the request it is
        instrumenting is worse than no instrumentation, and this one sits in
        the middle of the loop the player is walking through.
        """
        try:
            if name not in EVENTS:
                logger.warning(f"⚠️ Unknown learning event: {name!r} - not recorded")
                return False
            row = {
                "event": name,
                "actor": player_key(identity),
                "at": time.time(),
                **_safe_props(props),
            }
            with self._lock:
                self._events.append(row)
                self._counts[name] += 1
            # Useful even without Postgres. This line contains only the same
            # allow-listed values the table accepts; never PGN or player text.
            logger.info(
                "product_event %s",
                json.dumps(row, sort_keys=True, separators=(",", ":")),
            )
            self._persist(row)
            return True
        except Exception as e:
            logger.debug(f"Learning event {name} not recorded: {e}")
            return False

    @staticmethod
    def _persist(row: dict) -> None:
        if not db.configured():
            return
        try:
            properties = {
                key: value for key, value in row.items()
                if key not in {"event", "actor", "at"}
            }
            with db.connection() as conn:
                conn.execute(
                    "INSERT INTO product_events "
                    "(event_name, actor_key, game_id, correction_id, source_mode, occurred_at, properties) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)",
                    (
                        row["event"], row["actor"], row.get("game_id"),
                        row.get("correction_id"), row.get("source_mode"),
                        row["at"], json.dumps(properties, separators=(",", ":")),
                    ),
                )
        except Exception as exc:
            # Missing/unreachable storage is a supported degraded mode. The
            # in-memory row and structured log still exist.
            logger.debug("Product event persistence unavailable: %s", exc)

    def counts(self) -> dict:
        with self._lock:
            return dict(self._counts)

    def recent(self, limit: int = 50) -> list:
        with self._lock:
            return list(self._events)[-max(0, min(limit, MAX_BUFFERED)):]

    def _rows(self, days: int = 30) -> list:
        cutoff = time.time() - max(1, min(days, 365)) * 86400
        if db.configured():
            try:
                with db.connection() as conn:
                    rows = conn.execute(
                        "SELECT event_name, actor_key, occurred_at, properties "
                        "FROM product_events WHERE occurred_at >= %s "
                        "ORDER BY occurred_at DESC LIMIT %s",
                        (cutoff, MAX_QUERY_ROWS),
                    ).fetchall()
                return [
                    {
                        "event": row[0], "actor": row[1], "at": float(row[2]),
                        **(row[3] if isinstance(row[3], dict)
                           else json.loads(row[3] or "{}")),
                    }
                    for row in reversed(rows)
                ]
            except Exception as exc:
                logger.debug("Persistent product summary unavailable: %s", exc)
        return [row for row in self.recent(MAX_BUFFERED) if row["at"] >= cutoff]

    def has_prior(self, name: str, identity: Optional[str]) -> bool:
        actor = player_key(identity)
        if db.configured():
            try:
                with db.connection() as conn:
                    return conn.execute(
                        "SELECT 1 FROM product_events "
                        "WHERE event_name = %s AND actor_key = %s LIMIT 1",
                        (name, actor),
                    ).fetchone() is not None
            except Exception as exc:
                logger.debug("Return lookup unavailable: %s", exc)
        return any(
            row["event"] == name and row["actor"] == actor
            for row in self.recent(MAX_BUFFERED)
        )

    def summary(self, days: int = 30) -> dict:
        """Aggregate cohort, funnel, timing, return, and error metrics."""
        rows = self._rows(days)
        counts = Counter(row["event"] for row in rows)
        actors_by_event: dict[str, set] = defaultdict(set)
        imports_by_actor: dict[str, list] = defaultdict(list)
        durations: dict[str, list[float]] = defaultdict(list)
        errors = Counter()
        for row in rows:
            actor = row.get("actor", "anon")
            actors_by_event[row["event"]].add(actor)
            if row["event"] == "pgn_imported":
                imports_by_actor[actor].append(float(row["at"]))
            duration = row.get("duration_ms")
            if isinstance(duration, (int, float)):
                # Stage events share names across a few operations (whole-game
                # scan, correction evidence, review chat). Keep those timings
                # separate so an aggregate can answer which step is slow.
                timing_key = row["event"]
                if row.get("operation"):
                    timing_key += f":{row['operation']}"
                durations[timing_key].append(float(duration))
            if row["event"] == "correction_flow_error":
                errors[str(row.get("error_category") or "unknown")] += 1

        returned = 0
        returned_14d = 0
        for stamps in imports_by_actor.values():
            stamps.sort()
            if len(stamps) >= 2:
                returned += 1
                if stamps[-1] - stamps[0] >= 14 * 86400:
                    returned_14d += 1

        return {
            "window_days": max(1, min(days, 365)),
            "actor_ids_stable": ACTOR_IDS_STABLE,
            "events_recorded": len(rows),
            "testers": len({row.get("actor", "anon") for row in rows}),
            "funnel": dict(counts),
            "unique_testers_by_step": {
                name: len(actors) for name, actors in actors_by_event.items()
            },
            "players_with_multiple_imports": returned,
            "players_returned_after_14_days": returned_14d,
            "timing": {
                name: {
                    "count": len(values),
                    "average_ms": round(sum(values) / len(values), 1),
                    "max_ms": round(max(values), 1),
                }
                for name, values in durations.items() if values
            },
            "top_errors": [
                {"category": category, "count": count}
                for category, count in errors.most_common(10)
            ],
        }

    def clear(self) -> None:
        """Tests only."""
        with self._lock:
            self._events.clear()
            self._counts.clear()


events = EventSink()


def emit(name: str, identity: Optional[str] = None, **props) -> bool:
    return events.emit(name, identity, **props)


__all__ = [
    "ACTOR_IDS_STABLE", "ALLOWED_PROPERTIES", "EVENTS", "EventSink", "emit",
    "events", "player_key",
]
