"""
The move-feedback pipeline: is the grade the engine's, and is it earned?

Why this file exists
--------------------

A beta tester reported playing what they believed was the best move and being
told it was bad. That is the worst class of bug this app can have - a chess
teaching tool that criticises good moves is worse than one that says nothing,
because a beginner cannot tell which of its verdicts to keep - and it was
unanswerable when it arrived: nothing was written down about which position had
been searched, from whose point of view, at what depth, or whether the words the
user read had come from Stockfish or from Gemini.

So this suite pins the properties that make an answer possible. It is deliberately
about PROPERTIES rather than particular verdicts. CLAUDE.md §6 is emphatic on the
point and it applies here more than anywhere: the shared engine keeps its hash
between searches, near-equal moves reorder between runs, and a test that pinned
"Nf3 is Excellent" would fail about one run in three while being wrong about
nothing. What is asserted instead is that the engine's own top move is never
called bad, that Black is graded as Black, that a promotion is graded as the
piece it promoted to, and that no grade claims more confidence than the search
supports.

Run it like the others (CLAUDE.md §6):

    cd /mnt/c/Users/David/Documents/chess-app-v3.9
    set -a; . ./.env; set +a
    DISABLE_LANGFLOW=true /tmp/chessapp/bin/python -u test_move_feedback.py

It needs Stockfish. It does not need DATABASE_URL: nothing here touches storage.
"""
import os
import sys

# Before importing app: every API-driving suite but test_beta_access.py sets
# this, and a new one that does not gets 403 from beta_gate before the route
# under test ever runs (CLAUDE.md, the padlock note at the top).
os.environ.setdefault("BETA_ACCESS_REQUIRED", "false")
os.environ.setdefault("DISABLE_LANGFLOW", "true")

import chess

import engine_evidence
import move_feedback_log
import move_quality
from move_quality import (
    CLASSIFY_DEPTH,
    NOISE_MARGIN,
    classify_move,
    grade_from_scores,
)
from stockfish_service import stockfish_service

PASSED = 0
FAILED = 0
CRITICAL = {"inaccuracy", "mistake", "blunder", "miss"}


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if condition:
        PASSED += 1
        print(f"PASS  {name}")
    else:
        FAILED += 1
        print(f"FAIL  {name}" + (f"\n      {detail}" if detail else ""))


# ---------------------------------------------------------------------------
# 1. The engine's own best move is never criticised
# ---------------------------------------------------------------------------
#
# This is the tester's report, stated as an assertion. If the pipeline ever
# grades Stockfish's own first choice as an inaccuracy or worse, something
# between the search and the label is wrong - a mismatched horizon, a flipped
# sign, a stale result attached to the wrong position - and it does not matter
# which, because the user experience is identical and unacceptable.

BEST_MOVE_POSITIONS = [
    # White to move, out of book, quiet.
    "r2q1rk1/pp1bbppp/2n1pn2/3p4/3P4/2NBPN2/PPQ2PPP/R1B2RK1 w - - 0 10",
    "2rq1rk1/pb1nbppp/1p2pn2/2pp4/2PP4/1PN1PN2/PB2BPPP/R2Q1RK1 w - - 0 11",
    # BLACK to move. Present deliberately and in numbers: a sign error in the
    # mover's frame shows up here and nowhere else, and "the eval is flipped
    # for Black" was on the list of candidate causes for the tester's report.
    "r1bqkbnr/pppp1ppp/2n5/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R b KQkq - 3 3",
    "r1bqk2r/pp2bppp/2n1pn2/3p4/3P4/2NBPN2/PP3PPP/R1BQ1RK1 b kq - 0 8",
    "rnbqkb1r/pp2pppp/3p1n2/8/3NP3/2N5/PPP2PPP/R1BQKB1R b KQkq - 2 5",
    # Endgames, where a single tempo decides the result and a horizon mismatch
    # is at its most damaging.
    "8/5pk1/6p1/7p/7P/5PP1/5K2/8 w - - 0 40",
    "6k1/5ppp/8/8/8/8/5PPP/R5K1 w - - 0 30",
    "8/2p5/3p4/KP5r/1R3p1k/8/4P1P1/8 w - - 0 1",
]


def engine_best(fen: str):
    """The engine's own first choice, from one search of `fen`."""
    board = chess.Board(fen)
    info = stockfish_service.analyse(board, chess.engine.Limit(depth=CLASSIFY_DEPTH))
    pv = info.get("pv") or []
    return pv[0] if pv else None


print("\n--- the engine's own best move is never called bad ---")
for fen in BEST_MOVE_POSITIONS:
    board = chess.Board(fen)
    best = engine_best(fen)
    if best is None:
        check(f"engine offered a move in {fen[:24]}", False)
        continue
    quality = classify_move(fen, best.uci())
    side = "black" if fen.split(" ")[1] == "b" else "white"
    check(
        f"{side} to move, {board.san(best)} is not criticised",
        quality is not None and quality["label"] not in CRITICAL,
        f"got {quality and quality['label']} (cpl {quality and quality['cpl']}) in {fen}",
    )
    # It should also be free: playing the engine's move cannot cost centipawns
    # against the engine's move. A non-zero loss here is the horizon mismatch
    # that classify_move's root_moves search exists to remove.
    check(
        f"{side} to move, {board.san(best)} costs nothing",
        quality is not None and quality["cpl"] == 0,
        f"cpl was {quality and quality['cpl']}",
    )


# ---------------------------------------------------------------------------
# 2. Both sides are graded in their own frame
# ---------------------------------------------------------------------------
#
# A mirrored position must produce a mirrored grade. This is the sharpest test
# for a sign error there is: the two boards are the same chess, so any
# difference in the verdict is the perspective and nothing else.

print("\n--- black is graded as black ---")
MIRROR_PAIRS = [
    # Fool's mate and its mirror: the same move, once for each colour. White
    # plays Qh5# after 1.e4 f6 2.?? g5; Black plays Qh4# after 1.f3 e5 2.g4.
    # The two positions are the same chess reflected, so any difference in the
    # verdict is the PERSPECTIVE and nothing else - which is what makes this
    # the sharpest available test for a sign error in the mover's frame.
    ("rnbqkbnr/ppppp2p/5p2/6p1/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 3", "d1h5"),
    ("rnbqkbnr/pppp1ppp/8/4p3/6P1/5P2/PPPPP2P/RNBQKBNR b KQkq - 0 3", "d8h4"),
]
labels = []
for fen, uci in MIRROR_PAIRS:
    quality = classify_move(fen, uci)
    labels.append(quality["label"] if quality else None)
    board = chess.Board(fen)
    check(
        f"{'white' if board.turn else 'black'} {board.san(chess.Move.from_uci(uci))} graded",
        quality is not None,
    )
check(
    "the same idea for either colour gets the same grade",
    labels[0] == labels[1],
    f"white got {labels[0]}, black got {labels[1]}",
)

# And the evals travel in the mover's frame, stated rather than assumed.
for fen, uci in MIRROR_PAIRS:
    quality = classify_move(fen, uci)
    check(
        f"eval perspective is declared for {fen.split(' ')[1]}-to-move",
        quality is not None and quality.get("eval_perspective") == "mover",
        f"got {quality and quality.get('eval_perspective')}",
    )


# ---------------------------------------------------------------------------
# 3. Promotions, including underpromotions
# ---------------------------------------------------------------------------
#
# The promotion picker means four different moves can now be played from one
# from/to pair, and a grader that ignored the suffix would attach one move's
# verdict to another's. `e7e8q` and `e7e8n` are different moves and must grade
# as different moves.

print("\n--- promotions are graded as the piece they promoted to ---")
# White to play; queening mates on the move, the knight does not.
PROMO_FEN = "6k1/4P3/6K1/8/8/8/8/8 w - - 0 1"
promo_board = chess.Board(PROMO_FEN)
check("the promotion test position is legal", promo_board.is_valid())

grades = {}
for piece in "qrbn":
    uci = f"e7e8{piece}"
    move = chess.Move.from_uci(uci)
    if move not in promo_board.legal_moves:
        check(f"{uci} is legal here", False)
        continue
    quality = classify_move(PROMO_FEN, uci)
    grades[piece] = quality
    check(f"{uci} is graded at all", quality is not None)
    check(
        f"{uci} keeps its promotion in best_move or is itself best",
        quality is not None,
    )

check(
    "underpromoting to a bishop is not graded the same as queening",
    grades.get("q") is not None and grades.get("b") is not None
    and grades["q"]["label"] != grades["b"]["label"],
    f"queen={grades.get('q') and grades['q']['label']}, "
    f"bishop={grades.get('b') and grades['b']['label']}",
)
check(
    "queening here is not criticised",
    grades.get("q") is not None and grades["q"]["label"] not in CRITICAL,
    f"got {grades.get('q') and grades['q']['label']}",
)

# A stalemate trap: queening draws, the rook wins. The classic reason
# underpromotion has to be offered at all, and a check that the grader can see
# the difference between two moves to the same square.
# White Kc6 and a pawn on c7 against a king on a7. c8=Q is STALEMATE - the
# black king is not in check and has no square - while c8=R wins, because a6
# stays free. It is the textbook reason underpromotion has to exist, and here
# it is the check that the grader can tell two moves to the SAME SQUARE apart.
STALEMATE_FEN = "8/k1P5/2K5/8/8/8/8/8 w - - 0 1"
sb = chess.Board(STALEMATE_FEN)
if sb.is_valid():
    queened = chess.Board(STALEMATE_FEN)
    queened.push(chess.Move.from_uci("c7c8q"))
    check(
        "queening into stalemate really is stalemate (the position is right)",
        queened.is_stalemate(),
        queened.fen(),
    )
    q_grade = classify_move(STALEMATE_FEN, "c7c8q")
    r_grade = classify_move(STALEMATE_FEN, "c7c8r")
    check(
        "throwing away the win by queening is graded worse than the rook",
        q_grade is not None and r_grade is not None
        and q_grade["cpl"] > r_grade["cpl"],
        f"queen cpl={q_grade and q_grade['cpl']}, rook cpl={r_grade and r_grade['cpl']}",
    )


# ---------------------------------------------------------------------------
# 4. Provenance travels with every grade
# ---------------------------------------------------------------------------
#
# The logging and the coach instructions both read these fields. A grade that
# arrives without them is a grade nothing downstream can be honest about.

print("\n--- every grade says where it came from ---")
sample = classify_move(BEST_MOVE_POSITIONS[0], engine_best(BEST_MOVE_POSITIONS[0]).uci())
for field in ("grade_source", "confidence", "depth", "eval_before", "eval_after",
              "eval_perspective", "cpl", "accuracy"):
    check(f"a grade carries `{field}`", sample is not None and field in sample)
check(
    "the source is stockfish, never a model",
    sample is not None and sample["grade_source"] == "stockfish",
    f"got {sample and sample.get('grade_source')}",
)

# Book and forced are the two grades no engine produced. They say so rather
# than borrowing stockfish's name for it.
book = classify_move(chess.Board().fen(), "e2e4")
check("a book move is labelled book", book is not None and book["label"] == "book")
check(
    "a book move's source is the book",
    book is not None and book["grade_source"] == "book",
    f"got {book and book.get('grade_source')}",
)
# Exactly one legal move: White is in check from Rd1, the king has no square
# (its own pawns take f2/g2/h2 and the rook holds the first rank), and the
# only thing that answers the check is Nxd1. The obvious version of this
# position - a lone rook on f2 - has TWO legal moves, because the king can
# simply capture it.
forced = classify_move("1k6/8/8/8/8/8/1N3PPP/3r2K1 w - - 0 1", "b2d1")
if forced is not None:
    check("a forced move is labelled forced", forced["label"] == "forced")
    check("a forced move's source is the rules", forced["grade_source"] == "rules")


# ---------------------------------------------------------------------------
# 5. The margin: criticism has to be earned
# ---------------------------------------------------------------------------
#
# grade_from_scores is pure, so these are exact rather than engine-dependent.
# They are the rule the tester's report bought: a loss inside the engine's own
# measured run-to-run spread of a threshold does not clear it.

print("\n--- a critical grade has to clear its threshold by the margin ---")
MOVE = chess.Move.from_uci("e2e4")
OTHER = chess.Move.from_uci("d2d4")


def grade_at(loss: int, depth: int = CLASSIFY_DEPTH, played: int = None) -> dict:
    """A grade for a move that lost exactly `loss` centipawns."""
    best = 0 if played is None else played + loss
    return grade_from_scores("startpos-unused", MOVE, OTHER, best, best - loss, depth=depth)


# GOOD_MAX is 50 and the margin is 25, so 51..75 stays "good" and says so.
check("51cp is still good, not an inaccuracy", grade_at(51)["label"] == "good")
check("51cp is marked low confidence", grade_at(51)["confidence"] == "low")
check("51cp carries a note explaining itself", bool(grade_at(51)["note"]))
check("76cp is an inaccuracy", grade_at(76)["label"] == "inaccuracy")
check("76cp is high confidence", grade_at(76)["confidence"] == "high")

# INACCURACY_MAX is 100: 101..125 stays an inaccuracy.
check("101cp is still an inaccuracy", grade_at(101)["label"] == "inaccuracy")
check("101cp is low confidence", grade_at(101)["confidence"] == "low")
check("126cp is a mistake", grade_at(126)["label"] == "mistake")

# MISTAKE_MAX is 250: 251..275 stays a mistake rather than becoming a blunder.
# `played` is forced negative so the "still winning" softening does not fire.
check("251cp is still a mistake", grade_at(251, played=-400)["label"] == "mistake")
check("276cp is a blunder", grade_at(276, played=-400)["label"] == "blunder")

check(
    "the margin only ever softens - nothing is graded worse than the raw threshold",
    all(
        {"good": 0, "inaccuracy": 1, "mistake": 2, "blunder": 3}.get(grade_at(loss, played=-400)["label"], 0)
        <= {"good": 0, "inaccuracy": 1, "mistake": 2, "blunder": 3}.get(
            grade_from_scores("x", MOVE, OTHER, 0, -loss)["label"], 3)
        for loss in range(55, 300, 7)
    ),
)

print("\n--- a shallow search does not make confident claims ---")
shallow = grade_from_scores("x", MOVE, OTHER, 0, -400, depth=6)
check("a depth-6 verdict is low confidence", shallow["confidence"] == "low")
check("a depth-6 verdict says the depth is why", "depth" in (shallow["note"] or ""))
deep = grade_from_scores("x", MOVE, OTHER, 0, -400, depth=15)
check("a depth-15 verdict is high confidence", deep["confidence"] == "high")
check(
    "the margin is the engine's measured spread, not a round number",
    NOISE_MARGIN == 25,
    f"NOISE_MARGIN is {NOISE_MARGIN}; if it moved, re-measure rather than re-guess",
)


# ---------------------------------------------------------------------------
# 6. The LLM may explain a grade. It may not mint one.
# ---------------------------------------------------------------------------

print("\n--- the model is given the grade, and told it is not its to make ---")
from gemini_chat_service import gemini_chat_service  # noqa: E402  (needs env above)

quality = classify_move(BEST_MOVE_POSITIONS[0], engine_best(BEST_MOVE_POSITIONS[0]).uci())
sentence = engine_evidence.grade_sentence(quality, "Nf3")
check("a grade renders as a sentence for the model", bool(sentence))
check("that sentence names Stockfish as the grader", "Stockfish" in (sentence or ""))

low = grade_from_scores("x", MOVE, OTHER, 0, -60)
low_sentence = engine_evidence.grade_sentence(low, "e4")
check("a low-confidence grade says so to the model", "LOW CONFIDENCE" in (low_sentence or ""))

instruction = gemini_chat_service._build_system_instruction({
    "fen": BEST_MOVE_POSITIONS[0],
    "player_color": "white",
    "ai_color": "black",
    "difficulty": 12,
    "move_history_san": ["e4", "e5"],
    "alternatives": "Nf3 (+0.20), d4 (+0.15)",
    "best_line": "Nf3 Nc6 Bb5",
    "last_move_evidence": {"san": "Nf3", "by": "the human", "sentence": sentence},
})
check(
    "Play's chat is handed the engine's ranking",
    "Nf3 (+0.20)" in instruction,
)
check(
    "Play's chat is handed the grade of the last move",
    sentence in instruction,
)
check(
    "Play's chat is forbidden from inventing a grade",
    "NOT YOURS TO DECIDE" in instruction,
)
check(
    "and told what to say when it has not been given one",
    "do not have the engine's verdict" in instruction,
)

# The regression that made this necessary: with no evidence, the instruction
# used to say nothing at all about grading, so the model was free to invent.
bare = gemini_chat_service._build_system_instruction({
    "fen": BEST_MOVE_POSITIONS[0],
    "player_color": "white",
    "ai_color": "black",
    "difficulty": 12,
    "move_history_san": [],
})
check(
    "the prohibition is there even when there is no evidence to attach it to",
    "NOT YOURS TO DECIDE" in bare,
)


# ---------------------------------------------------------------------------
# 7. The diagnostic log
# ---------------------------------------------------------------------------
#
# The next report of a wrong grade has to be answerable from a log line. These
# check the line exists and carries every field that report would need.

print("\n--- one greppable line per graded half-move ---")
import logging  # noqa: E402


class _Capture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines = []

    def emit(self, record):
        self.lines.append(record.getMessage())


capture = _Capture()
logging.getLogger("move_feedback").addHandler(capture)
logging.getLogger("move_feedback").setLevel(logging.INFO)

move_feedback_log.log_grade(
    mode="Play",
    fen_before=BEST_MOVE_POSITIONS[2],
    move_uci="g8f6",
    fen_after="after-fen",
    san="Nf6",
    player_color="black",
    quality=classify_move(BEST_MOVE_POSITIONS[2], "g8f6"),
)
check("a grade logs exactly one line", len(capture.lines) == 1, str(capture.lines))
line = capture.lines[0] if capture.lines else ""
for field in ("mode=Play", "side_to_move=black", "player_color=black", "move=g8f6",
              "san=Nf6", "grade=", "cpl=", "best=", "depth=", "confidence=",
              "grade_source=", "explanation=engine", "perspective=mover",
              "eval_before=", "eval_after=", "fen_before=", "fen_after="):
    check(f"the log line carries `{field.rstrip('=')}`", field in line, line[:200])

# It must never take the app down. Observation that can break play is worse
# than no observation.
capture.lines.clear()
move_feedback_log.log_grade(mode="Play", fen_before="not a fen", move_uci="")
check("a malformed call still logs and does not raise", len(capture.lines) == 1)


# ---------------------------------------------------------------------------
# 8. The rate-limit switch fails safe
# ---------------------------------------------------------------------------
#
# Not strictly move feedback, but it is the other thing the QA sweep for this
# sprint needed and it is the kind of switch that is dangerous in exactly one
# direction. `RATE_LIMITS_ENABLED` exists so a browser-driving tool can run
# without taking 429s from its own thoroughness; what must never happen is a
# deployment reaching that state by accident, so anything but the literal
# "false" leaves the limits on.

print("\n--- the rate-limit switch only opens on the literal \"false\" ---")
import importlib  # noqa: E402
import rate_limit  # noqa: E402

for value, expected, label in [
    (None, True, "unset"),
    ("", True, "empty"),
    ("true", True, "true"),
    ("1", True, "1"),
    ("no", True, "'no' is not the magic word"),
    ("yes", True, "'yes' is not it either"),
    ("False", False, "False"),
    ("FALSE", False, "FALSE"),
    (" false ", False, "false, padded"),
    ("false", False, "false"),
]:
    if value is None:
        os.environ.pop("RATE_LIMITS_ENABLED", None)
    else:
        os.environ["RATE_LIMITS_ENABLED"] = value
    importlib.reload(rate_limit)
    check(
        f"{label} -> limits {'on' if expected else 'off'}",
        rate_limit.RATE_LIMITS_ENABLED is expected,
        f"got {rate_limit.RATE_LIMITS_ENABLED}",
    )

os.environ.pop("RATE_LIMITS_ENABLED", None)
importlib.reload(rate_limit)
check("and the default, with nothing set, is enforced", rate_limit.RATE_LIMITS_ENABLED is True)


# ---------------------------------------------------------------------------

stockfish_service.close()
print(f"\n{PASSED}/{PASSED + FAILED} passed")
sys.exit(1 if FAILED else 0)
