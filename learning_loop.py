"""
The learning loop's own state: corrections, practice attempts, and the
controlled vocabulary they are filed under.

WHAT A CORRECTION IS
--------------------
Not "Stockfish says you lost 240 centipawns here". That is a measurement, and
the player already has it - `postmortem_analysis` computed it and the Report
tab shows it. A **Correction** is the thing a measurement is only evidence
for: a named decision pattern, what the player was trying to do when it
happened, what they did not see, and the one check that would have caught it.

One card per player per theme. A second diagnosis landing on a theme the
player already has does not create a second card - it increments
`occurrence_count`, appends its evidence, and moves `last_seen_at`. That is
the whole recurrence mechanism, and it is deliberately this small: it is a
controlled fact about which of eight labels a diagnosis carried, not a claim
to understand the player.

WHY THE STORE LOOKS LIKE `PlayerStore`
--------------------------------------
Because it is the same problem, and this codebase has already solved it once:
state owned by an opaque identity string (`identity.py`), capped so a public
instance cannot be memory-exhausted by visitors, and swept when idle. Reading
`player_state.py` first will explain most of the shape below.

WHERE THIS LIVES, AND FOR HOW LONG
----------------------------------
This store is the guest path: bounded process memory, swept after 24 hours and
never attached to an account. Authenticated cards use `correction_history.py`
and Postgres instead. Keeping the guest path here preserves the contract that
anonymous corrections are useful without pretending they survive a restart.
"""

from __future__ import annotations

import logging
import secrets
import threading
import time
from typing import Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# The taxonomy
#
# Eight themes, fixed, and the model may not invent a ninth. A controlled
# vocabulary is what makes recurrence checkable: "you have seen this before"
# is true because two diagnoses carry the same token, not because something
# judged two positions to feel alike. If a diagnosis does not fit one of
# these, that is a signal the loop should say less, not that the list should
# grow on the fly - see `is_theme`.
#
# `check` is the sentence the card offers the player as the thing to do next
# time. It is written here rather than by the model for the same reason the
# themes are: it is the part of the card that has to be the same every time
# the theme recurs, or the player is being taught a different lesson each
# time they meet it.
# ---------------------------------------------------------------------------

THEMES: dict[str, dict] = {
    "FORCING_MOVE_MISSED": {
        "label": "A forcing move was available",
        "description": "A check, capture or direct threat was on the board and a quieter move was played instead.",
        "check": "Before anything quiet, list the checks and captures and see what each one actually does.",
    },
    "OPPONENT_THREAT_MISSED": {
        "label": "The opponent's threat went unanswered",
        "description": "The move pursued its own plan while the opponent's last move was already threatening something.",
        "check": "Ask what the opponent's last move attacks or prepares before choosing your own.",
    },
    "TACTICAL_OVERLOOK": {
        "label": "A tactic was missed or allowed",
        "description": "A concrete tactical shot was available to one side and the move played did not account for it.",
        "check": "Look for loose pieces, alignments and undefended squares on both sides before committing.",
    },
    "PREMATURE_ATTACK": {
        "label": "The attack came too early",
        "description": "An attacking move was played before the pieces supporting it were in place.",
        "check": "Count how many pieces actually join the attack before starting it.",
    },
    "KING_SAFETY": {
        "label": "King safety was conceded",
        "description": "The move weakened the king's shelter or left the king in the centre when it mattered.",
        "check": "Before a committal move, ask what it does to the safety of your own king.",
    },
    "PIECE_ACTIVITY": {
        "label": "A piece was left out of the game",
        "description": "The move improved something already active while a piece sat undeveloped or passive.",
        "check": "Find your worst-placed piece and ask whether this move is better than improving it.",
    },
    "CAPTURE_RECALCULATION": {
        "label": "The capture sequence was miscounted",
        "description": "An exchange was entered or declined on a miscount of what the sequence actually wins.",
        "check": "Play the whole exchange out in your head to the last capture before starting it.",
    },
    "PLAN_BEFORE_OPPONENT_RESPONSE": {
        "label": "The plan ignored the reply",
        "description": "The move carried out a plan without accounting for the opponent's most natural answer.",
        "check": "Name the opponent's best reply and check your move still makes sense after it.",
    },
}

THEME_IDS = tuple(THEMES.keys())


def is_theme(value) -> bool:
    """Whether a string is one of the eight. The validator's whole job."""
    return isinstance(value, str) and value in THEMES


def theme_label(theme: str) -> str:
    return THEMES.get(theme, {}).get("label", theme)


# The reasons a player can push back on a card. Kept as data because the
# product doctrine (CLAUDE.md §18, "Correction Cards") lists them as
# first-class, and because a rejected diagnosis is a fact worth keeping rather
# than a card to delete: it is the difference between "we were wrong about
# this" and "this never happened".
STATUS_OPEN = "open"
STATUS_ACCEPTED = "accepted"
STATUS_REJECTED = "rejected"
STATUS_KNOWN = "known"
STATUSES = (STATUS_OPEN, STATUS_ACCEPTED, STATUS_REJECTED, STATUS_KNOWN)


def _now() -> float:
    return time.time()


def _new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(8)}"


class PracticeAttempt:
    """
    One try at one re-test position.

    `expected` is the set of moves that count as demonstrating the concept,
    and it comes from the bank, which verified it against Stockfish. Nothing
    here decides whether a move is good; it records which of a fixed set was
    played.
    """

    __slots__ = (
        "id", "correction_id", "fen", "played_uci", "expected", "passed",
        "hints_used", "response_ms", "created_at",
    )

    def __init__(
        self,
        correction_id: str,
        fen: str,
        played_uci: Optional[str],
        expected: list,
        passed: bool,
        hints_used: int = 0,
        response_ms: Optional[int] = None,
    ):
        self.id = _new_id("att")
        self.correction_id = correction_id
        self.fen = fen
        self.played_uci = played_uci
        self.expected = list(expected)
        self.passed = bool(passed)
        self.hints_used = int(hints_used)
        self.response_ms = response_ms
        self.created_at = _now()

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "correction_id": self.correction_id,
            "fen": self.fen,
            "played_uci": self.played_uci,
            "expected": list(self.expected),
            "passed": self.passed,
            "hints_used": self.hints_used,
            "response_ms": self.response_ms,
            "created_at": self.created_at,
        }


class Correction:
    """
    One named decision pattern for one player.

    Every field that makes a claim about chess (`evidence`) is a copy of a
    `postmortem_analysis` packet, not a restatement of one. Every field that
    interprets it (`diagnosis`, `missed_factor`) came from the model and was
    validated before it got here. `correction_rule` comes from the taxonomy,
    not the model, so the same theme teaches the same check every time.
    """

    __slots__ = (
        "id", "player_id", "theme", "player_intent", "missed_factor",
        "diagnosis", "correction_rule", "confidence", "uncertainty",
        "evidence", "attempts", "status", "created_at", "last_seen_at",
        "occurrence_count",
    )

    def __init__(
        self,
        player_id: str,
        theme: str,
        player_intent: str,
        missed_factor: str,
        diagnosis: str,
        confidence: float,
        evidence: dict,
        uncertainty: Optional[str] = None,
    ):
        self.id = _new_id("corr")
        self.player_id = player_id
        self.theme = theme
        self.player_intent = player_intent
        self.missed_factor = missed_factor
        self.diagnosis = diagnosis
        # From the taxonomy, deliberately. See the class docstring.
        self.correction_rule = THEMES[theme]["check"]
        self.confidence = confidence
        self.uncertainty = uncertainty
        # A list, because a recurring pattern is only interesting with more
        # than one instance behind it - and because "show me the supporting
        # games" (§18) needs every one of them, not the latest.
        self.evidence: list = [evidence]
        self.attempts: list = []
        self.status = STATUS_OPEN
        self.created_at = _now()
        self.last_seen_at = self.created_at
        self.occurrence_count = 1

    # -- recurrence ---------------------------------------------------------

    def record_recurrence(self, evidence: dict, diagnosis: Optional[str] = None) -> None:
        """
        Another instance of the same theme, for the same player.

        The card keeps its original diagnosis unless a newer one arrived; what
        changes is the count and the evidence, because those are what make the
        claim "this keeps happening" true rather than rhetorical.
        """
        self.occurrence_count += 1
        self.last_seen_at = _now()
        self.evidence.append(evidence)
        # Bounded for the same reason everything else here is: this is memory
        # owned by an anonymous visitor. Ten instances is far more than any
        # card needs to make its point.
        if len(self.evidence) > 10:
            self.evidence = self.evidence[-10:]
        if diagnosis:
            self.diagnosis = diagnosis

    # -- practice -----------------------------------------------------------

    def record_attempt(self, attempt: PracticeAttempt) -> None:
        self.attempts.append(attempt)
        if len(self.attempts) > 50:
            self.attempts = self.attempts[-50:]
        self.last_seen_at = _now()

    @property
    def practice_summary(self) -> dict:
        passed = sum(1 for a in self.attempts if a.passed)
        return {
            "attempted": len(self.attempts),
            "passed": passed,
            # Null rather than 0 with nothing behind it: "0%" and "not tried
            # yet" are different things and the UI has to be able to tell.
            "rate": round(passed / len(self.attempts), 2) if self.attempts else None,
            "hints_used": sum(a.hints_used for a in self.attempts),
            "last_passed": self.attempts[-1].passed if self.attempts else None,
        }

    def to_dict(self, include_evidence: bool = True) -> dict:
        payload = {
            "id": self.id,
            "theme": self.theme,
            "theme_label": theme_label(self.theme),
            "player_intent": self.player_intent,
            "missed_factor": self.missed_factor,
            "diagnosis": self.diagnosis,
            "correction_rule": self.correction_rule,
            "confidence": self.confidence,
            "uncertainty": self.uncertainty,
            "status": self.status,
            "created_at": self.created_at,
            "last_seen_at": self.last_seen_at,
            "occurrence_count": self.occurrence_count,
            "practice_summary": self.practice_summary,
        }
        if include_evidence:
            # The whole point of §14 traceability: the card can always answer
            # "why do you think this" with the packets it was built from.
            payload["evidence"] = list(self.evidence)
            payload["attempts"] = [a.to_dict() for a in self.attempts]
        # `player_id` is deliberately NOT in here. It is the identity cookie's
        # value, it is a credential (identity.py says so), and the client
        # already knows who it is by holding it.
        return payload


# ---------------------------------------------------------------------------


DEFAULT_MAX_PLAYERS = 500
# 24 hours, matching PlayerStore. A learning card is worth keeping for as long
# as the board it came from.
DEFAULT_IDLE_SECONDS = 24 * 60 * 60


class CorrectionStore:
    """
    Every player's corrections, keyed by the opaque identity string.

    Guest corrections only. Account cards use the durable sibling store.
    """

    def __init__(
        self,
        max_players: int = DEFAULT_MAX_PLAYERS,
        idle_seconds: int = DEFAULT_IDLE_SECONDS,
    ):
        # identity -> {correction_id: Correction}
        self._backing: dict[str, dict] = {}
        self._touched: dict[str, float] = {}
        self._max_players = max_players
        self._idle_seconds = idle_seconds
        self._lock = threading.Lock()

    # -- housekeeping -------------------------------------------------------

    def _sweep_unlocked(self) -> None:
        cutoff = _now() - self._idle_seconds
        for identity in [i for i, t in self._touched.items() if t < cutoff]:
            self._backing.pop(identity, None)
            self._touched.pop(identity, None)
        # Still over the cap after sweeping idle players: drop the least
        # recently touched. A public instance must have a ceiling, and an
        # arbitrary one enforced deterministically beats an unbounded dict.
        while len(self._backing) > self._max_players:
            oldest = min(self._touched, key=self._touched.get)
            self._backing.pop(oldest, None)
            self._touched.pop(oldest, None)

    def _cards(self, identity: str) -> dict:
        cards = self._backing.get(identity)
        if cards is None:
            cards = {}
            self._backing[identity] = cards
        self._touched[identity] = _now()
        return cards

    # -- reads --------------------------------------------------------------

    def list_for(self, identity: str) -> list:
        """This player's cards, most recently seen first."""
        with self._lock:
            cards = list(self._cards(identity).values())
        return sorted(cards, key=lambda c: c.last_seen_at, reverse=True)

    def get(self, identity: str, correction_id: str) -> Optional[Correction]:
        """
        One card, **scoped to the caller**.

        Not a global lookup with an ownership check bolted on afterwards: the
        identity is the key, so there is no code path that can read another
        player's card by id. `postmortem_api._require` reaches the same place
        by a different route; this one cannot be forgotten because there is
        nothing to remember.
        """
        with self._lock:
            return self._cards(identity).get(correction_id)

    def find_by_theme(self, identity: str, theme: str) -> Optional[Correction]:
        with self._lock:
            for card in self._cards(identity).values():
                if card.theme == theme:
                    return card
        return None

    def count_for(self, identity: str) -> int:
        with self._lock:
            return len(self._cards(identity))

    # -- writes -------------------------------------------------------------

    def upsert(
        self,
        identity: str,
        theme: str,
        player_intent: str,
        missed_factor: str,
        diagnosis: str,
        confidence: float,
        evidence: dict,
        uncertainty: Optional[str] = None,
    ) -> tuple[Correction, bool]:
        """
        The card for this theme, creating it if this is the first time.

        Returns `(card, recurred)`. `recurred` is what the UI turns into "we
        have seen this before" - and it is only ever true because two
        diagnoses carried the same controlled token, which is a fact rather
        than an inference.
        """
        if not is_theme(theme):
            raise ValueError(f"Unknown theme: {theme!r}")
        with self._lock:
            cards = self._cards(identity)
            for card in cards.values():
                if card.theme == theme:
                    card.record_recurrence(evidence, diagnosis)
                    return card, True
            card = Correction(
                player_id=identity,
                theme=theme,
                player_intent=player_intent,
                missed_factor=missed_factor,
                diagnosis=diagnosis,
                confidence=confidence,
                evidence=evidence,
                uncertainty=uncertainty,
            )
            cards[card.id] = card
            self._sweep_unlocked()
            return card, False

    def record_attempt(self, identity: str, correction_id: str, attempt: PracticeAttempt):
        with self._lock:
            card = self._cards(identity).get(correction_id)
            if card is None:
                return None
            card.record_attempt(attempt)
            return card

    def set_status(self, identity: str, correction_id: str, status: str):
        if status not in STATUSES:
            raise ValueError(f"Unknown status: {status!r}")
        with self._lock:
            card = self._cards(identity).get(correction_id)
            if card is None:
                return None
            card.status = status
            card.last_seen_at = _now()
            return card

    def drop_player(self, identity: str) -> bool:
        with self._lock:
            self._touched.pop(identity, None)
            return self._backing.pop(identity, None) is not None

    def clear(self) -> None:
        """Tests only."""
        with self._lock:
            self._backing.clear()
            self._touched.clear()


corrections = CorrectionStore()


__all__ = [
    "Correction",
    "CorrectionStore",
    "PracticeAttempt",
    "STATUSES",
    "STATUS_ACCEPTED",
    "STATUS_KNOWN",
    "STATUS_OPEN",
    "STATUS_REJECTED",
    "THEMES",
    "THEME_IDS",
    "corrections",
    "is_theme",
    "theme_label",
]
