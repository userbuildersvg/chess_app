"""
What a recurring mistake looks like, in evidence rather than in adjectives.

WHY THIS FILE IS PURE
---------------------
No engine, no database, no FastAPI. It takes the evidence packet
`postmortem_analysis.build_evidence()` already produces for one half-move and
answers a single question: does this ply show one of the patterns we can
honestly claim to detect?

That purity is the reason the taxonomy can be tested at all. Every theme below
has a test that constructs a position and asserts the detector fires - or,
just as importantly, that it does not.

THE TAXONOMY, AND WHY IT IS THIS ONE
------------------------------------
The learning loop (`learning_loop.py`) already has eight themes, grounded in
engine evidence, with correction rules written for each. **Those eight are
reused verbatim.** A pattern found across a library of games and a correction
card offered after a single move must speak the same language, or the product
has two vocabularies for one idea and a person has to learn both.

Four are added, because the recurring patterns people actually describe -
"I overextend", "I can't convert endgames", "I'm lost in the opening", "I
blunder in time trouble" - are not expressible in the eight.

WHAT IS DELIBERATELY ABSENT
---------------------------
**Discovered attacks.** Naming them needs a motif classifier this evidence
does not support; they are counted as `TACTICAL_OVERLOOK`, which is true.
A theme that cannot be detected honestly is worse than a missing one, because
a person will go and practise it.

**Time management without clocks.** Most PGNs carry no `[%clk]`. Where it is
absent, `TIME_PRESSURE` is never claimed - not estimated from move numbers,
not inferred from the phase. An unavailable measurement is reported as
unavailable.
"""

from __future__ import annotations

import chess

from learning_loop import THEMES as LOOP_THEMES

# ---------------------------------------------------------------------------
# The four themes the loop's eight cannot express
# ---------------------------------------------------------------------------

PROFILE_THEMES: dict[str, dict] = {
    "FLANK_PAWN_COMMITTAL": {
        "label": "Wing pawns move before the centre is settled",
        "description": (
            "A pawn on the a-, b-, g- or h-file was pushed while the centre was still "
            "unresolved, and the move cost material or position."
        ),
        "check": "Before a wing pawn moves, ask what it does about the centre.",
        "claim": "You frequently push wing pawns before the centre is settled.",
    },
    "ENDGAME_CONVERSION": {
        "label": "Winning endgames slip away",
        "description": (
            "The endgame was reached with a decisive advantage and the move gave a "
            "large part of it back."
        ),
        "check": "In a won endgame, prefer the move that keeps it simple over the move that keeps it exciting.",
        "claim": "You often trade a winning endgame down into one you have to work for.",
    },
    "OPENING_UNCERTAINTY": {
        "label": "The opening is where the ground is lost",
        "description": (
            "Evaluation was lost inside the opening, on a move that was not a "
            "recognised book line."
        ),
        "check": "Learn the first eight moves of the lines you actually play, in both colours.",
        "claim": "You lose ground in the opening more often than anywhere else on the board.",
    },
    "TIME_PRESSURE": {
        "label": "Mistakes cluster when the clock is low",
        "description": (
            "A costly move was played with little time left, as recorded by the clock "
            "in the game file."
        ),
        "check": "Bank time in positions that play themselves, so it is there when the position does not.",
        "claim": "Your mistakes cluster in the last minutes on your clock.",
    },
}

# The eight loop themes gain a `claim` - the sentence the profile says about a
# person - while keeping their existing label, description and correction rule
# untouched. Written here rather than in learning_loop.py because a claim is
# only meaningful across many games, which is this file's concern and not that
# one's.
LOOP_CLAIMS: dict[str, str] = {
    "FORCING_MOVE_MISSED": "You regularly play a quiet move while a check or capture was available.",
    "OPPONENT_THREAT_MISSED": "You rarely calculate your opponent's forcing replies before choosing your own move.",
    "TACTICAL_OVERLOOK": "You miss or allow concrete tactics more often than the rest of your play would suggest.",
    "PREMATURE_ATTACK": "You start attacks before enough of your pieces have joined them.",
    "KING_SAFETY": "You concede your own king's safety in positions where it turns out to matter.",
    "PIECE_ACTIVITY": "You improve pieces that are already working while another one sits out of the game.",
    "CAPTURE_RECALCULATION": "You miscount exchanges - you trade into worse positions than you expect.",
    "PLAN_BEFORE_OPPONENT_RESPONSE": "You follow your own plan without checking your opponent's most natural reply.",
}

THEMES: dict[str, dict] = {}
for _id, _t in LOOP_THEMES.items():
    THEMES[_id] = dict(_t, claim=LOOP_CLAIMS[_id])
THEMES.update(PROFILE_THEMES)

THEME_IDS = tuple(THEMES)


def is_theme(value) -> bool:
    return isinstance(value, str) and value in THEMES


def theme_label(theme: str) -> str:
    return THEMES.get(theme, {}).get("label", theme)


def theme_claim(theme: str) -> str:
    return THEMES.get(theme, {}).get("claim", theme_label(theme))


# ---------------------------------------------------------------------------
# Severity
#
# Three bands, from what the move actually cost. The thresholds are the ones
# move_quality.py already grades against, so a "serious" finding here and a
# red badge in Review mean the same thing to the same move.
# ---------------------------------------------------------------------------

MINOR_CPL = 50
SERIOUS_CPL = 120
CRITICAL_CPL = 300

# Below this a move is not a mistake, it is a choice. Detectors do not run on
# it at all - a pattern assembled from noise is a pattern that will not
# reproduce, and telling somebody they "consistently" do something on the
# strength of 30-centipawn wobbles is the fastest way to make the whole
# feature untrustworthy.
NOISE_FLOOR_CPL = MINOR_CPL


def severity_of(cpl) -> str:
    if cpl is None:
        return "minor"
    if cpl >= CRITICAL_CPL:
        return "critical"
    if cpl >= SERIOUS_CPL:
        return "serious"
    return "minor"


# ---------------------------------------------------------------------------
# The centre, for FLANK_PAWN_COMMITTAL
# ---------------------------------------------------------------------------

WING_FILES = frozenset("abgh")
CENTRE_SQUARES = (chess.D4, chess.D5, chess.E4, chess.E5)


def _centre_is_unresolved(board: chess.Board) -> bool:
    """
    Whether the centre is still being argued about.

    Deliberately crude, and crude in the safe direction: if either side still
    has a pawn on one of the four central squares, or the central files still
    hold pawns of both colours, the centre is not settled. A wing push into a
    settled position is often exactly right - it is the wing push while the
    centre is still live that this theme is about, so the test has to be able
    to say no.
    """
    for sq in CENTRE_SQUARES:
        piece = board.piece_at(sq)
        if piece is not None and piece.piece_type == chess.PAWN:
            return True
    for file_index in (chess.BB_FILES.index(chess.BB_FILE_D), chess.BB_FILES.index(chess.BB_FILE_E)):
        white = black = False
        for rank in range(8):
            piece = board.piece_at(chess.square(file_index, rank))
            if piece is not None and piece.piece_type == chess.PAWN:
                if piece.color == chess.WHITE:
                    white = True
                else:
                    black = True
        if white and black:
            return True
    return False


# ---------------------------------------------------------------------------
# Clock, for TIME_PRESSURE
# ---------------------------------------------------------------------------

# Under two minutes. Not a universal truth about chess - it is a threshold that
# has to be SOME number, and this one is low enough that a move played under it
# is genuinely rushed at every time control people actually import.
LOW_CLOCK_SECONDS = 120


def seconds_left(evidence: dict):
    """The mover's remaining clock in seconds, or None if the file has none.

    None is the normal case and is treated as "unknown", never as "plenty".
    """
    value = evidence.get("clock_seconds")
    if isinstance(value, (int, float)) and value >= 0:
        return float(value)
    return None


# ---------------------------------------------------------------------------
# The detectors
# ---------------------------------------------------------------------------

def _tactical_theme(board: chess.Board, evidence: dict) -> str:
    """
    Which of the eight the engine's own facts point at.

    Extends `diagnosis_service.classify_from_evidence` rather than replacing
    it: the same three cases it can tell apart, plus the ones a whole game of
    context makes available that a single position does not.
    """
    best_uci = evidence.get("best_move")
    played_uci = evidence.get("uci")
    if not best_uci:
        return "TACTICAL_OVERLOOK"
    try:
        best = chess.Move.from_uci(best_uci)
        played = chess.Move.from_uci(played_uci) if played_uci else None
    except ValueError:
        return "TACTICAL_OVERLOOK"
    if best not in board.legal_moves:
        return "TACTICAL_OVERLOOK"

    if board.gives_check(best):
        return "FORCING_MOVE_MISSED"

    if board.is_capture(best):
        played_was_capture = bool(played and played in board.legal_moves and board.is_capture(played))
        # The played move was itself a capture and still lost material: that is
        # a miscounted exchange, not a missed one.
        return "TACTICAL_OVERLOOK" if played_was_capture else "CAPTURE_RECALCULATION"

    # The played move WAS a capture and the engine wanted something else - the
    # sequence was entered on a miscount.
    if played and played in board.legal_moves and board.is_capture(played):
        return "CAPTURE_RECALCULATION"

    # The player's own king is already exposed and the best move addressed it.
    if board.is_check():
        return "OPPONENT_THREAT_MISSED"

    return "TACTICAL_OVERLOOK"


def detect(evidence: dict) -> dict | None:
    """
    The one finding this ply shows, or None.

    ONE finding, not a list, and that is a deliberate limit. A ply that trips
    three detectors would contribute three counts to three themes off a single
    mistake, and every claim in the profile is a count - so one bad move would
    inflate three separate patterns at once.

    Themes are tried in order of how SPECIFIC they are, and the most specific
    one that fits wins. Specificity here means "how much it tells the player
    about what to do differently": a measured clock beats everything, a named
    tactical miss beats a phase, and a phase is only the best description of a
    mistake when nothing better fits.

    Returns a dict ready to become a `game_findings` row, or None when the ply
    is not evidence of anything: a good move, a book move, a forced move, or a
    wobble below the noise floor.
    """
    cpl = evidence.get("cpl")
    quality = evidence.get("quality") or {}
    label = quality.get("label") or quality.get("grade")

    # Facts about the position, not about how well somebody chose. The live
    # grader treats them the same way and so must this.
    if label in ("book", "forced"):
        return None
    if cpl is None or cpl < NOISE_FLOOR_CPL:
        return None

    fen_before = evidence.get("fen_before")
    if not fen_before:
        return None
    try:
        board = chess.Board(fen_before)
    except ValueError:
        return None

    played_uci = evidence.get("uci")
    try:
        played = chess.Move.from_uci(played_uci) if played_uci else None
    except ValueError:
        played = None

    phase = evidence.get("phase") or "middlegame"
    theme = None

    # --- most specific first ------------------------------------------------

    # 1. The clock, where the file recorded one. It outranks everything else
    #    because "you were out of time" is a better explanation of a bad move
    #    than any pattern in the position, and claiming otherwise would file
    #    time-trouble blunders under whichever theme they happened to look like.
    left = seconds_left(evidence)
    if left is not None and left <= LOW_CLOCK_SECONDS:
        theme = "TIME_PRESSURE"

    # 2. A decisive endgame given away.
    elif phase == "endgame" and cpl >= SERIOUS_CPL and _was_winning(evidence):
        theme = "ENDGAME_CONVERSION"

    else:
        # 3. What the engine's own facts point at, IF they point at something
        #    specific. This runs before the phase themes on purpose, and the
        #    ordering was wrong the first time in a way worth recording: with
        #    `opening` checked first, a hung queen on move four was filed as
        #    OPENING_UNCERTAINTY. True - it was the opening, and ground was
        #    lost - and useless, because the thing to practise is seeing the
        #    reply, not learning a line. A phase is *where* a mistake happened;
        #    it is only the best description of one when nothing better fits.
        specific = _tactical_theme(board, evidence)
        if specific != "TACTICAL_OVERLOOK":
            theme = specific

        # 4. A wing pawn committed while the centre was still live. Also more
        #    specific than the phase: "you push wing pawns too early" names a
        #    habit, where "you lose ground in the opening" names a clock face.
        elif played is not None and _is_wing_pawn_push(board, played) and _centre_is_unresolved(board):
            theme = "FLANK_PAWN_COMMITTAL"

        # 5. Ground lost in the opening with nothing more specific to say.
        elif phase == "opening":
            theme = "OPENING_UNCERTAINTY"

        # 6. The honest catch-all.
        else:
            theme = "TACTICAL_OVERLOOK"

    return {
        "ply": evidence.get("ply"),
        "theme": theme,
        "severity": severity_of(cpl),
        "cpl": int(cpl),
        "fen_before": fen_before,
        "move_san": evidence.get("san") or "",
        "best_san": evidence.get("best_san"),
        "phase": phase,
    }


def _is_wing_pawn_push(board: chess.Board, move: chess.Move) -> bool:
    """A non-capturing pawn move on the a, b, g or h file."""
    piece = board.piece_at(move.from_square)
    if piece is None or piece.piece_type != chess.PAWN:
        return False
    if board.is_capture(move):
        return False
    return chess.square_name(move.from_square)[0] in WING_FILES


def _was_winning(evidence: dict) -> bool:
    """
    Whether the mover was decisively winning before the move.

    Reads the white-framed evaluation the evidence already carries and turns it
    into the mover's frame, because "winning" is a claim about the person whose
    move it was and the stored number is not.
    """
    before = evidence.get("eval_before") or {}
    score = before.get("score")
    mate_in = before.get("mate_in")
    white_to_move = evidence.get("color") == "white"
    if mate_in is not None:
        return (mate_in > 0) == white_to_move
    if score is None:
        return False
    # +3 pawns. Below that an endgame is better, not won, and "you gave away a
    # winning endgame" would be a claim the evidence does not support.
    return score >= 300 if white_to_move else score <= -300


__all__ = [
    "CRITICAL_CPL",
    "LOW_CLOCK_SECONDS",
    "MINOR_CPL",
    "NOISE_FLOOR_CPL",
    "PROFILE_THEMES",
    "SERIOUS_CPL",
    "THEMES",
    "THEME_IDS",
    "detect",
    "is_theme",
    "seconds_left",
    "severity_of",
    "theme_claim",
    "theme_label",
]
