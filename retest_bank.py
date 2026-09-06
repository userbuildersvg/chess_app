"""
The re-test bank: a small constant table of positions with one right answer.

HOW THESE POSITIONS GOT HERE, AND WHY THAT MATTERS
--------------------------------------------------
Not written by hand and not produced by a model. They were harvested offline
by playing out semi-random games, scanning every quiet position with a
two-line search, and keeping only those where the gap between the best move
and the second-best is at least 250 centipawns - then **re-confirming each one
at depth 20** and discarding any where the best move changed or the gap
closed. `test_retest_bank.py` re-runs that confirmation against the real
engine, so a position that stops being an only-move cannot stay in this file.

The reason for all of that is a single sentence in the brief: do not display a
fake or invalid exercise. A re-test tells the player they got a concept right
or wrong. If two moves are equally good and we mark one of them wrong, we have
taught them something false about chess, which is worse than not testing them
at all.

WHY FIVE OF THE EIGHT THEMES HAVE NO POSITIONS
-----------------------------------------------
Because the engine would not certify any. The first candidate set included
quiet positions for king safety, piece activity, premature attack and
plan-before-reply; at depth 18 the top two moves in those were separated by
**0 to 20 centipawns**. There is no single correct answer in a position like
that, so there is no honest way to grade one. `position_for` returns `None`
for those themes and the API says the re-test is unavailable, which is the
truth and is a better product than a coin flip presented as an assessment.

That is a real limitation of this v0, not a thing to paper over. Anyone
extending this should add positions the same way - harvested, adjudicated,
and re-confirmed in the test - rather than by writing FENs into the table.
"""

from __future__ import annotations

from typing import Optional

# Minimum centipawn gap between best and second-best for a position to be
# usable as a test. The harvester enforced it; the test re-enforces it.
MIN_GAP = 250

# Depth the entries below were confirmed at. The test uses the same one, so a
# disagreement is a real change rather than a search-depth artefact.
CONFIRM_DEPTH = 20


# One entry per position.
#
#   fen        the position, side to move is the one being tested
#   best_uci   the only move. Verified, not asserted.
#   best_san   the same move, for the UI, so the frontend never renders SAN
#   gap        centipawns to the second-best move at CONFIRM_DEPTH
#   prompt     what the player is asked. Deliberately never names the motif -
#              "win material with a check" would be the answer, not the test.
#
# No evaluation is exposed to the player during a re-test (the brief is
# explicit), so `gap` is here for the test and the logs and is never sent to
# the client - see `public()`.
BANK: dict[str, list[dict]] = {
    "FORCING_MOVE_MISSED": [
        {
            "fen": "n1b4r/1p3kp1/7r/p3P3/1q2P3/2N3PP/P3B3/K2R2NR b - - 3 26",
            "best_uci": "b4c3", "best_san": "Qxc3+", "gap": 29396,
            "prompt": "Black to move. There is one move here that changes everything. Find it.",
        },
        {
            "fen": "2q2k1r/r1p2Ppp/ppnp4/5Q2/2P2PP1/2N4N/PP2P2n/R1B1K2b w Q - 0 18",
            "best_uci": "f5c8", "best_san": "Qxc8+", "gap": 1583,
            "prompt": "White to move. Only one move keeps the advantage. Which is it?",
        },
        {
            "fen": "1k5r/p4ppp/1p1q1n2/1PpNnQ2/2Pr4/P3B2P/4BP2/3RK2R b - - 7 29",
            "best_uci": "d4d1", "best_san": "Rxd1+", "gap": 346,
            "prompt": "Black to move. One move is clearly best here. Play it.",
        },
        {
            "fen": "3b4/5k2/7p/Q5p1/P4P2/4N1RP/1PP2P2/R3K3 b Q - 0 34",
            "best_uci": "d8a5", "best_san": "Bxa5+", "gap": 28959,
            "prompt": "Black to move, and in trouble. Exactly one move avoids the worst. Find it.",
        },
    ],
    "CAPTURE_RECALCULATION": [
        {
            "fen": "rnbq1b1r/ppppkppp/5n2/8/3Pp3/2PQB2N/PP2PPPP/RN2KB1R b KQ - 1 5",
            "best_uci": "e4d3", "best_san": "exd3", "gap": 603,
            "prompt": "Black to move. Work out the exchange before you commit to it.",
        },
        {
            "fen": "rnbqk2r/pp1pbppp/2p2n2/1P6/4Q3/7P/P1PP1PP1/RNB1KBNR b KQkq - 2 7",
            "best_uci": "f6e4", "best_san": "Nxe4", "gap": 429,
            "prompt": "Black to move. Count the sequence out, then play the best move.",
        },
        {
            "fen": "2kr3r/p1qn1ppp/1p3n2/1Pp1b3/2P2P2/P1N1B2P/2Q2P2/R3KB1R w Q - 1 25",
            "best_uci": "f4e5", "best_san": "fxe5", "gap": 481,
            "prompt": "White to move. One move is right here and the alternatives are not.",
        },
        {
            "fen": "rnb1kbnr/ppq1p1pp/8/5p2/P2P1B2/2NQ1N2/1PP2PPP/R3KB1R w KQq - 0 11",
            "best_uci": "f4c7", "best_san": "Bxc7", "gap": 268,
            "prompt": "White to move. Work out what the whole sequence actually wins.",
        },
    ],
    # Two, not three. A fourth candidate (Qa4 in
    # r1b1kr2/p1qn1ppp/Qp1b1n2/1Ppp4/2P5/P2P3P/5PP1/RNB1KBNR w Qq - 0 15) was
    # harvested at a 271cp gap and re-confirmed at 237 on a later run - the
    # shared engine keeps its hash between searches, so a borderline position
    # does not measure the same twice. It was dropped rather than kept by
    # lowering MIN_GAP, because a position that is only sometimes an only-move
    # is not one to grade a player on. If it is ever wanted back, it has to
    # clear the floor on a cold engine.
    "OPPONENT_THREAT_MISSED": [
        {
            "fen": "1r2k3/1p4b1/3Qb1Np/p4pp1/P5n1/N5P1/1PP2P1P/R3KBR1 b Q - 1 23",
            "best_uci": "e8f7", "best_san": "Kf7", "gap": 421,
            "prompt": "Black to move. Something is being threatened. Deal with it.",
        },
        {
            "fen": "2bq4/2p2p1k/3p4/2n1r1p1/R7/4QPNP/1P4PR/4KB2 w - - 0 25",
            "best_uci": "a4e4", "best_san": "Re4", "gap": 280,
            "prompt": "White to move. Find what Black is threatening, then answer it.",
        },
    ],
}


# Themes with no bank of their own that a bank position genuinely tests.
#
# This is a judgement, so it is written down rather than buried in a lookup:
# `TACTICAL_OVERLOOK` is defined in the taxonomy as "a concrete tactical shot
# was available to one side and the move played did not account for it", and
# that is precisely what the forcing-move and capture positions test. Testing
# it with those is the same skill under a different name, not a substitution.
#
# The four quiet themes are deliberately NOT mapped here. A king-safety lapse
# is not tested by finding a capture, and pretending otherwise is the exact
# dishonesty this file's docstring is about.
ALSO_TESTS: dict[str, tuple] = {
    "TACTICAL_OVERLOOK": ("FORCING_MOVE_MISSED", "CAPTURE_RECALCULATION"),
}


def sources_for(theme: str) -> list:
    """Which bank keys may answer a request for `theme`, in preference order."""
    keys = []
    if theme in BANK:
        keys.append(theme)
    keys.extend(k for k in ALSO_TESTS.get(theme, ()) if k in BANK)
    return keys


def has_position(theme: str) -> bool:
    return any(BANK.get(k) for k in sources_for(theme))


def public(entry: dict) -> dict:
    """
    What the client is allowed to see before answering.

    Note what is missing: `best_uci`, `best_san` and `gap`. The brief is
    explicit that a re-test shows no engine numbers and does not reveal the
    answer beforehand, and the reliable way to honour that is for the answer
    never to be in the payload - not for the UI to promise not to look.
    """
    return {"fen": entry["fen"], "prompt": entry["prompt"]}


def position_for(theme: str, index: int = 0) -> Optional[dict]:
    """
    A position testing `theme`, or None if none is certified for it.

    Deterministic in `index`, and the caller passes the number of attempts
    already made, so a player practising the same correction twice meets a
    different position the second time and the same one on the same attempt
    number every time. Deterministic selection is what makes the flow
    testable; rotating is what stops the "test" becoming a memory check.
    """
    pool: list = []
    for key in sources_for(theme):
        pool.extend(BANK[key])
    if not pool:
        return None
    return pool[index % len(pool)]


def is_correct(entry: dict, uci: str) -> bool:
    """Whether a played move is the certified answer. A set membership, nothing more."""
    return bool(uci) and uci == entry["best_uci"]


def all_entries() -> list:
    """Every (theme, entry) pair. For the test, which re-verifies all of them."""
    return [(theme, entry) for theme, rows in BANK.items() for entry in rows]


__all__ = [
    "ALSO_TESTS",
    "BANK",
    "CONFIRM_DEPTH",
    "MIN_GAP",
    "all_entries",
    "has_position",
    "is_correct",
    "position_for",
    "public",
    "sources_for",
]
