"""
The seven opponent profiles - the only strength setting the app has.

These replace the 1-20 "difficulty" integer. That number was the position of
a 3-move window slid along Stockfish's ranked list, which meant two things a
tester could feel but not name: level 20 was a 99%-accuracy engine wearing
Gemini's voice, and level 1 was the three *worst* legal moves on the board -
random garbage, not a weak player. Neither is an opponent anyone has met.

A profile is a description of a player. `candidate_selection.py` turns it
into a pool of moves such a player might consider (a centipawn ceiling, a
softmax temperature, a pull toward the checks and captures weak players see
and the quiet defence they miss, and a tolerance for hanging material), and
`gemini_move_service.py` tells the model who it is playing as, in the same
single call that picks the move.

The numbers here are tuning knobs, not Elo claims. `tools/profile_sanity.py`
plays the profiles against each other and prints what they actually do;
change a knob, re-run it, and keep the table in CLAUDE.md honest. The UI
says "about 1500", never "1500".

`get_profile()` never raises: anything it does not recognise - None, an old
integer, a typo - is the default, `club`, logged once per bad value. The
frontend mirrors this table in `chess-frontend/src/opponentProfiles.ts`, and
`test_opponent_profiles.py` fails if the two drift.
"""
from dataclasses import dataclass
import logging

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class OpponentProfile:
    id: str
    label: str
    approx_elo: int
    blurb: str
    # Centipawn-loss ceiling: moves worse than this (against the engine's
    # best) are not in the pool at all, unless nothing else is left.
    max_cpl: int
    # The centipawn loss this player's typical move costs. The pool is
    # weighted AROUND it - weight = exp(-|cpl - target| / temperature) - not
    # down from zero, because a pool weighted from zero always holds a
    # near-best move and a three-move pool then plays at ~20cpl whatever the
    # label says (measured: "beginner" at 22cpl mean). A beginner's typical
    # move is a mistake, not the best move with some noise on it.
    target_cpl: int
    # Softmax scale around the target. Small = the pool clusters at the
    # target; large = it spreads across the eligible range.
    temperature: float
    # 0-1. How reliably the player notices checks, captures, threats to their
    # own pieces, and a mate in one. Low values pull the pool toward
    # "obvious" moves regardless of quality and away from quiet defence.
    tactical_awareness: float
    # 0-1. Chance a move that hangs a piece, or walks into mate, stays
    # eligible. A beginner leaves pieces loose; a master does not.
    blunder_tolerance: float
    # How many moves Gemini is offered.
    pool_size: int
    # One sentence for the prompt: how this player talks about their move.
    commentary_style: str


_TABLE = (
    OpponentProfile(
        "beginner", "Beginner", 400,
        "Misses tactics and leaves pieces loose. Good for learning how the pieces work.",
        350, 140, 120, 0.25, 0.60, 3,
        "Use simple words for the one idea behind the move. You may admit you did "
        "not check everything, without playing a fool or a character.",
    ),
    OpponentProfile(
        "casual", "Casual", 800,
        "Sees obvious captures and checks, misses anything deeper.",
        250, 85, 80, 0.45, 0.40, 3,
        "Explain one immediate idea behind the move in plain language. Keep the "
        "reasoning simple without sounding careless or foolish.",
    ),
    OpponentProfile(
        "improving", "Improving", 1200,
        "Develops sensibly and spots one-move tactics; misses quiet defence.",
        160, 45, 50, 0.65, 0.20, 3,
        "Explain the idea and what you expect it to lead to. If the evidence shows "
        "an oversight, acknowledge it plainly rather than acting it out.",
    ),
    OpponentProfile(
        "club", "Club", 1500,
        "Reasonable, coherent chess with the occasional strategic slip.",
        100, 22, 30, 0.80, 0.08, 3,
        "Explain the plan and one concrete pressure the user should keep an eye on.",
    ),
    OpponentProfile(
        "advanced", "Advanced", 1800,
        "Coherent plans and solid defence; the errors are subtle.",
        60, 9, 18, 0.90, 0.03, 3,
        "Explain the plan and the positional reason behind it.",
    ),
    OpponentProfile(
        "expert", "Expert", 2100,
        "Strong tactics and sound positional play.",
        30, 3, 10, 0.96, 0.01, 3,
        "Explain the plan and the key question the position now asks of your opponent.",
    ),
    OpponentProfile(
        "master", "Master-like", 2400,
        "Near-best moves nearly every time. A very strong training partner.",
        12, 0, 6, 1.00, 0.00, 3,
        "Explain precisely why this move, and what the critical continuation is.",
    ),
)

PROFILES = {p.id: p for p in _TABLE}
PROFILE_IDS = tuple(p.id for p in _TABLE)
DEFAULT_PROFILE_ID = "club"
# Profiles weak enough that the prompt lets the "Watch out" section admit the
# AI's own move left something loose, and that the sampler pulls away from
# quiet defensive moves. Shared with candidate_selection.
LOW_PROFILE_IDS = ("beginner", "casual", "improving")

_warned = set()


def get_profile(value) -> OpponentProfile:
    """The profile for `value`, or the default. Never raises."""
    if isinstance(value, OpponentProfile):
        return value
    if isinstance(value, str) and value in PROFILES:
        return PROFILES[value]
    key = repr(value)
    if value is not None and key not in _warned:
        _warned.add(key)
        logger.warning(f"⚠️ Unknown opponent profile {key} - using {DEFAULT_PROFILE_ID}")
    return PROFILES[DEFAULT_PROFILE_ID]


def display_label(profile) -> str:
    """"Club (~1500)" - for PGN headers, chat context and log lines."""
    p = get_profile(profile)
    return f"{p.label} (~{p.approx_elo})"


def profile_summary(profile) -> dict:
    """What the API hands the UI about one profile."""
    p = get_profile(profile)
    return {"id": p.id, "label": p.label, "approx_elo": p.approx_elo, "blurb": p.blurb}


def all_summaries() -> list:
    return [profile_summary(i) for i in PROFILE_IDS]
