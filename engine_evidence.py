"""
The engine's opinion of a position, rendered for a language model to quote.

This was written once inside `sandbox_api.py` and needed a second time the
moment Play's mid-game chat had to stop inventing move quality. A second copy
is how the coach in one mode ends up quoting a number the coach in another mode
would not recognise, so it lives here and both callers import it.

Two rules are baked in rather than left to the caller, because both have been
got wrong before:

1. **Refined, never raw.** `get_ranked_moves` runs at the shallow `RANK_DEPTH`
   and its scores are documented as good enough to sort by and not good enough
   to show. Handing those straight to a model is quoting a figure the engine
   does not stand behind, so everything here goes through `refine_candidates`
   first.
2. **The principal variation travels with the score.** Stockfish computed the
   whole line to produce the number it reported. Handing over only the first
   move leaves a language model to calculate a forced sequence, which is the
   thing it is least able to do - on a position verified as mate in two, the
   sandbox coach once named the right first move and then a second that did not
   mate. The line was there all along.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

import chess

from stockfish_service import stockfish_service

logger = logging.getLogger(__name__)


def score_text(entry: dict) -> str:
    """
    An engine score as something readable, including mates.

    `_analyse_moves` sets "score" to None whenever it found a forced mate and
    puts the distance in "mate_in" instead, so printing the score alone told
    the coach that every move in a mating position was worth "None". On a mate
    in two that is the single most important fact about the position, and it
    was the one thing being withheld.
    """
    mate_in = entry.get("mate_in")
    if mate_in is not None:
        return f"mate in {abs(mate_in)}" + ("" if mate_in > 0 else " against")
    score = entry.get("score")
    return "unknown" if score is None else f"{score / 100:+.2f}"


def line_text(board: chess.Board, entry: dict) -> Optional[str]:
    """The engine's continuation for one entry, in SAN, or None."""
    pv = entry.get("pv") or []
    if not pv:
        return None
    walker = board.copy()
    sans = []
    for uci in pv:
        try:
            move = chess.Move.from_uci(uci)
        except ValueError:
            break
        if move not in walker.legal_moves:
            break
        sans.append(walker.san(move))
        walker.push(move)
    return " ".join(sans) or None


async def ranked_evidence(fen: str, top_n: int = 5, label: str = "chat") -> tuple:
    """
    `(alternatives_text, best_line_text)` for the position, or `(None, None)`.

    Runs the searches on a thread so the event loop keeps serving. Failure is
    not an error: a coach that can still talk beats a 500, and the caller loses
    the engine's ordering rather than being handed a number to invent around.
    The model is told nothing at all in that case, which is the safe shape -
    an absent fact cannot be misquoted.
    """
    try:
        board = chess.Board(fen)
    except ValueError:
        return None, None
    if not board.is_valid() or board.is_game_over():
        return None, None

    try:
        ranked = await asyncio.to_thread(stockfish_service.get_ranked_moves, fen, top_n)
        ranked = await asyncio.to_thread(stockfish_service.refine_candidates, fen, ranked)
    except Exception as exc:
        logger.warning(f"⚠️ {label}: could not rank moves for evidence: {exc}")
        return None, None

    parts = []
    for entry in ranked:
        uci = entry.get("move")
        if not uci:
            continue
        try:
            san = board.san(chess.Move.from_uci(uci))
        except (chess.InvalidMoveError, chess.IllegalMoveError, ValueError):
            san = uci
        parts.append(f"{san} ({score_text(entry)})")

    return (", ".join(parts) or None), (line_text(board, ranked[0]) if ranked else None)


def grade_sentence(quality: dict, san: str = None) -> Optional[str]:
    """
    One sentence stating what the engine graded a move, for a system
    instruction - including, when the evidence is weak, that it is weak.

    This is the sentence that stops a coach contradicting the badge on the
    board. It is built from the grade dict rather than from prose, so the words
    the model is handed and the badge the user is looking at cannot drift; and
    when `confidence` is low it carries the note instead of the label alone,
    because the model repeating "that was a mistake" with more conviction than
    the engine had is exactly the failure being designed out.
    """
    if not quality:
        return None
    name = quality.get("name")
    if not name:
        return None
    subject = f"The move {san}" if san else "That move"
    bits = [f"{subject} was graded {name} by Stockfish"]
    if quality.get("cpl") is not None and quality.get("label") not in ("book", "forced"):
        bits.append(f"{quality['cpl']} centipawns behind its best move")
    if quality.get("best_move"):
        bits.append(f"which was {quality['best_move']}")
    if quality.get("depth"):
        bits.append(f"searched to depth {quality['depth']}")
    sentence = ", ".join(bits) + "."
    if quality.get("confidence") == "low":
        note = quality.get("note") or "the evidence is thin at this depth"
        sentence += (
            f" That grade is LOW CONFIDENCE - {note}. Say so if the student asks "
            "how good the move was, rather than repeating the label with more "
            "certainty than the engine had."
        )
    return sentence
