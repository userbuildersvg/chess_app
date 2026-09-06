"""
Learning-loop instrumentation: a seam, not an analytics stack.

WHY THIS IS TEN LINES OF STORAGE AND NOT A VENDOR
-------------------------------------------------
The audit in CLAUDE.md §20 established that this product has **no analytics of
any kind** - no GA, no Plausible, no PostHog, no pixel, no custom telemetry -
and the brief for this work was explicit that the absence is not a reason to
go and add one. But the learning funnel is the one thing worth counting: the
whole product thesis is that a player corrects a pattern, and "did they get
from a diagnosis to a passed re-test" is not answerable without recording that
they did.

So: an in-process counter and a bounded ring buffer, behind an `emit()` that a
real analytics client could later implement instead. Nothing leaves the
machine. Nothing is written to disk. If this is ever pointed at a vendor, this
file is the only thing that changes, and the privacy question gets asked once,
here, rather than at twenty call sites.

WHAT IS NEVER RECORDED
----------------------
**The identity string.** `identity.py` is explicit that it is a credential -
holding it is what grants access to a player's game, which is why it lives in
an HttpOnly cookie the client cannot read. Writing it into an event log would
put a live session key in the logs, so events carry a per-process salted hash
of it, truncated. That is enough to count distinct players inside one process
run and useless for anything else, which is exactly the trade wanted here.

Also never recorded: anything the player typed. Intent free-text is a sentence
about a chess position, but it is still their words, and a funnel does not
need it - `intent_submitted` records *that* they answered and which preset, if
any, they picked. The sentence itself lives on the correction card, where the
player can see it, and nowhere else.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import threading
import time
from collections import Counter, deque
from typing import Optional

logger = logging.getLogger(__name__)

# The funnel. A fixed vocabulary for the same reason the themes are fixed: an
# event name that varies by call site cannot be counted. Anything not in here
# is refused rather than recorded, so a typo shows up as a rejection instead
# of as a silently missing step.
EVENTS = (
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

MAX_BUFFERED = 500

# Per-process and random, so the same player hashes differently across
# restarts and the hash cannot be reversed by trying candidate cookie values.
_SALT = secrets.token_bytes(16)


def player_key(identity: Optional[str]) -> str:
    """A short, non-reversible stand-in for an identity. See the docstring."""
    if not identity:
        return "anon"
    return hashlib.blake2s(_SALT + identity.encode("utf-8"), digest_size=6).hexdigest()


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
                "player": player_key(identity),
                "at": time.time(),
                # Only scalars, and only ones a funnel needs. A dict here
                # would be an invitation to attach whatever is lying around,
                # which is how free-text ends up in a log.
                **{k: v for k, v in props.items() if isinstance(v, (str, int, float, bool, type(None)))},
            }
            with self._lock:
                self._events.append(row)
                self._counts[name] += 1
            return True
        except Exception as e:
            logger.debug(f"Learning event {name} not recorded: {e}")
            return False

    def counts(self) -> dict:
        with self._lock:
            return dict(self._counts)

    def recent(self, limit: int = 50) -> list:
        with self._lock:
            return list(self._events)[-limit:]

    def clear(self) -> None:
        """Tests only."""
        with self._lock:
            self._events.clear()
            self._counts.clear()


events = EventSink()


def emit(name: str, identity: Optional[str] = None, **props) -> bool:
    return events.emit(name, identity, **props)


__all__ = ["EVENTS", "EventSink", "emit", "events", "player_key"]
