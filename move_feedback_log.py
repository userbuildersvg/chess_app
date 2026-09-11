"""
One line per graded half-move, in one shape, from every mode.

Why this exists
---------------

A beta tester reported that a move they believed was best came back graded
badly. That report was unanswerable: there was nothing in the log that said
which position had been searched, from whose point of view, at what depth, or
whether the wording the user actually read had come from the engine or from
Gemini. The bug could have been in any of six places and the evidence for none
of them was written down.

So every grade the app produces now logs the whole chain that produced it,
under one logger (`move_feedback`) and one field order, whether it came from
Play, Learn, Review or the Post-Mortem scan. When the next report arrives, the
answer is a grep rather than a reconstruction.

The fields, and why each one is here
------------------------------------

Each names a specific way the pipeline could be wrong, and every one of them
was on the list of candidate causes for that first report:

    mode            Play / Learn / Review / Post-Mortem - the four gradings
                    could diverge, and did not, but only a log can say so
    fen_before      the position searched. A grade attached to the wrong
                    position is the failure with no visible symptom
    move / san      what was played, in both notations, so a UCI/SAN mismatch
                    or a lost promotion suffix shows up as a mismatch here
    fen_after       what it produced
    side_to_move    whose move it was in fen_before
    player_color    which side the human is - so "was this even my move?" is
                    answerable, which is the question behind the report
    eval_before     the engine's score for the position, mover's frame
    eval_after      the engine's score for the played move, mover's frame
    perspective     stated, never assumed. Every number on this line is in the
                    MOVER's frame; the eval BAR is White's absolute frame, and
                    mixing the two is the sign-flip bug that would show up as
                    Black being graded as if it were White
    best_move       what the engine preferred
    depth           what search produced all of it
    grade           the label the user sees
    cpl             the loss the label came from
    confidence      whether the evidence separates this grade from the next
    grade_source    stockfish / book / rules - never "gemini"
    explanation     "engine" when nothing wrote prose, "gemini:wording" when a
                    model phrased a grade it was handed, and nothing else is a
                    legal value. A model is never the source of a grade, and
                    the log is where that is checked rather than trusted

Nothing here is user content and nothing here is a secret: it is a position, a
move and a number. It logs at INFO because the whole point is that it is
present in an ordinary production log when a report arrives, not something a
maintainer has to reproduce with a debug flag.
"""
from __future__ import annotations

import logging

from move_grade_audit import audits

logger = logging.getLogger("move_feedback")

# The only two legal answers to "where did the words come from". A grade is
# never one of Gemini's outputs; the most it may do is phrase one it was given.
EXPLANATION_ENGINE = "engine"
EXPLANATION_GEMINI_WORDING = "gemini:wording"


def log_grade(
    mode: str,
    fen_before: str,
    move_uci: str,
    fen_after: str = None,
    san: str = None,
    player_color: str = None,
    quality: dict = None,
    eval_before=None,
    eval_after=None,
    explanation_source: str = EXPLANATION_ENGINE,
    identity: str = None,
    game_id: str = None,
    correction_id: str = None,
    key_decision: bool = False,
    provider: str = None,
    model: str = None,
    fallback: bool = False,
    timeout: bool = False,
) -> None:
    """
    Record one graded half-move. Never raises: a logging call that can break
    the thing it is observing is worse than no logging at all, and this one
    sits directly in the move path.

    `quality` is the dict `move_quality.grade_from_scores` produced, so the
    grade, its centipawn loss, its depth, its confidence and its source are all
    quoted from it rather than recomputed here - a log that derives its own
    version of the number cannot be used to check the number.
    """
    try:
        q = quality or {}
        side = _side_to_move(fen_before)
        fields = [
            f"mode={mode}",
            f"side_to_move={side}",
            f"player_color={player_color or '-'}",
            f"move={move_uci}",
            f"san={san or '-'}",
            f"grade={q.get('label') or '-'}",
            f"cpl={q.get('cpl')}",
            f"best={q.get('best_move') or '-'}",
            f"depth={q.get('depth')}",
            f"confidence={q.get('confidence') or '-'}",
            f"grade_source={q.get('grade_source') or '-'}",
            f"explanation={explanation_source}",
            # Stated on every line rather than documented once somewhere else.
            # The sign of an evaluation is the one thing in this pipeline that
            # is wrong silently, and a frame that has to be looked up is a
            # frame that gets assumed.
            "perspective=mover",
            f"eval_before={_fmt_eval(eval_before)}",
            f"eval_after={_fmt_eval(eval_after)}",
            f"fen_before={fen_before}",
            f"fen_after={fen_after or '-'}",
        ]
        logger.info(" ".join(fields))
        audits.record(
            identity=identity,
            game_id=game_id,
            correction_id=correction_id,
            source_flow=mode,
            fen_before=fen_before,
            move_played=move_uci,
            fen_after=fen_after,
            side_to_move=side,
            player_color=player_color,
            engine_best=q.get("best_move"),
            eval_before=q.get("eval_before") if q.get("eval_before") is not None else eval_before,
            eval_after=q.get("eval_after") if q.get("eval_after") is not None else eval_after,
            eval_perspective=q.get("eval_perspective") or "mover",
            engine_depth=q.get("depth"),
            grade_assigned=q.get("label"),
            key_decision=key_decision,
            provider=provider,
            model=model,
            fallback=fallback,
            timeout=timeout,
        )
    except Exception:  # pragma: no cover - observation must not break play
        pass


def _side_to_move(fen: str) -> str:
    try:
        return "white" if fen.split(" ")[1] == "w" else "black"
    except Exception:
        return "-"


def _fmt_eval(value) -> str:
    if value is None:
        return "-"
    if isinstance(value, dict):
        if value.get("mate_in") is not None:
            return f"mate{value['mate_in']:+d}"
        score = value.get("score")
        return "-" if score is None else str(score)
    return str(value)
