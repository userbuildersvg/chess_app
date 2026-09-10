"""
The analysis layer for Post-Mortem: engine truth about an imported game,
shaped as evidence rather than as a screenful of centipawns.

This module is the only place in Post-Mortem that talks to Stockfish. Nothing
above it computes an evaluation, and nothing below it (postmortem_state.py)
knows an engine exists. That boundary is what the spec's "create a clean
analysis abstraction rather than hardcoding analysis into UI components" comes
to in practice: the UI and the coach both receive the same dict from here, and
neither can invent a number that is not in it.

The evidence packet
-------------------

One dict per half-move, and it is deliberately the shape the trust
architecture asks for - everything a claim about that move could need, with
its provenance attached:

    ply, san, uci, color, phase
    fen_before, fen_after
    eval_before, eval_after     White's absolute frame, {score, mate_in}
    best_move, best_san, pv_san the engine's preference and its own line
    cpl, quality                how much the move cost, and its grade
    depth                       what search produced all of the above

`depth` is in there because a shallow result and a deep one are not the same
claim, and the coach is told which it has. Nothing here is ever synthesised:
if the engine did not produce a value, the field is null and the layers above
say so rather than filling it in.

Why the whole-game scan costs one search per position
-----------------------------------------------------

The obvious implementation grades each move with `move_quality.classify_move`,
which searches the position before the move and then the position after it:
two searches per ply, and the second is a search of the position the *next*
ply will search again as its own "before". Over a 40-move game that is ~160
searches to answer ~80 questions.

Walking the game as a sequence of positions instead, each position is
evaluated exactly once and its result used twice - as "what was available
before move i" and as "what move i-1 actually achieved". 81 searches for the
same 80 answers, and the eval curve falls out of the same pass for free. The
grading rules themselves are not reimplemented: the numbers go to
`move_quality.grade_from_scores`, which is the same function the live game
grades through, so a blunder in a review is a blunder in play.

The one thing lost is the "Great" grade, which is a claim about the
second-best move and so needs a MultiPV search this pass does not do by
default. That is stated in the summary rather than quietly absorbed into
"Best". `POSTMORTEM_SCAN_MULTIPV=2` buys it, and the measured price is +80% on
the whole scan - see SCAN_MULTIPV for the numbers and why the default is off.

Depth, and being a good neighbour
---------------------------------

The scan runs at `SCAN_DEPTH` (default 12), not the full `SEARCH_DEPTH`,
because it is 80 searches rather than one and it shares a single engine
process with live play. Each search takes the shared lock separately, so a
game in progress interleaves with a scan rather than waiting behind all of
it - but a deeper scan would still make every move of that game slower, and
depth 12 is comfortably enough to see a blunder. A single position the user
asks about is analysed at full depth, because that one is worth it.
"""

import logging
import os
from typing import Optional

import chess
import chess.engine

import move_quality
from postmortem_state import phase_of
from stockfish_service import SEARCH_DEPTH, stockfish_service

logger = logging.getLogger(__name__)


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        logger.warning(f"⚠️ {name} is not an integer - using {default}")
        return default


# Depth for the whole-game scan. See the module docstring: this is per
# position, over a whole game, on the engine the live game is using.
SCAN_DEPTH = _env_int("POSTMORTEM_SCAN_DEPTH", 12)
# Depth for one position the user has actually asked about. Full strength -
# it is a single search, and it is the one that gets quoted back to them.
PROBE_DEPTH = _env_int("POSTMORTEM_PROBE_DEPTH", SEARCH_DEPTH)
# How many plies of the engine's own line to keep. Enough to show the idea,
# short enough that it is not a wall of notation in a chat prompt.
PV_PLIES = 8

# Whether the scan asks for a second principal variation, which is the only
# way it can ever produce the "Great" grade.
#
# "Great" is a claim about the RUNNER-UP - this was the only move that held the
# position, everything else drops a pawn and a half - so it cannot be detected
# without knowing what the second-best move was worth. The scan does one
# single-PV search per position, so it has never been able to award it, and
# `summarise` declares that rather than absorbing the missing category into
# "Best".
#
# Off by default, and that is a measurement rather than a preference. Timed
# over the 34 positions of the Opera Game at depth 12 on this machine:
#
#     multipv=1   2.13s   65ms per position
#     multipv=2   3.83s  116ms per position   -> 1.80x, +80%
#
# Asking for two principal variations costs 80% more because it kills the
# alpha-beta pruning that makes the first one cheap - the same effect §7's
# preserved benchmark records for MultiPV over all legal moves, just smaller.
# Eighty percent of the heaviest thing this backend does, on a free Render
# instance, to light up one cosmetic label is not a trade worth making by
# default. It is here so that a deployment with CPU to spare can make it.
#
# If you are tempted to turn it on globally, re-run the measurement first: the
# number above is this machine, this depth, this game.
SCAN_MULTIPV = max(1, min(2, _env_int("POSTMORTEM_SCAN_MULTIPV", 1)))


def _white_frame(score: chess.engine.PovScore) -> dict:
    """
    An engine score as {score, mate_in} from White's absolute point of view.

    Absolute rather than side-to-move because this is what gets drawn as a
    curve and read across a whole game: a number that flipped meaning every
    ply would make the chart meaningless. Note that the ranked-move scores
    elsewhere in the app use the opposite convention, which is correct for
    *those* - the two must not be mixed up.
    """
    white = score.pov(chess.WHITE)
    mate_in = white.mate()
    return {"score": white.score() if mate_in is None else None, "mate_in": mate_in}


def _mover_cp(score: chess.engine.PovScore, mover: bool) -> int:
    """The same score as one comparable number from the mover's point of view."""
    return move_quality._to_cp(score.pov(mover))


def _san_line(board: chess.Board, ucis: list) -> list:
    """A UCI line rendered in SAN, stopping at the first move that isn't legal."""
    walker = board.copy()
    sans = []
    for uci in ucis:
        try:
            move = chess.Move.from_uci(uci) if isinstance(uci, str) else uci
        except ValueError:
            break
        if move not in walker.legal_moves:
            break
        sans.append(walker.san(move))
        walker.push(move)
    return sans


class PositionView:
    """
    One evaluated position: what the engine thinks, and what it would play.

    A tiny value object rather than a bare tuple because the scan carries two
    of these at a time (the position before a move and the position after it)
    and mixing them up is exactly the bug that would make every grade wrong.
    """

    __slots__ = ("fen", "turn", "score", "best_move", "pv", "depth", "second")

    def __init__(self, fen: str, score, best_move, pv: list, depth: int, second=None):
        self.fen = fen
        self.turn = chess.Board(fen).turn
        self.score = score          # chess.engine.PovScore, or None
        self.best_move = best_move  # chess.Move, or None
        self.pv = pv                # list[chess.Move]
        self.depth = depth
        # The RUNNER-UP's score, when the search was asked for two lines.
        # None means "not measured", which is not the same as "there was no
        # second move" - grade_from_scores treats a missing second_cp as "the
        # Great grade cannot be detected here" rather than as evidence against
        # it, so leaving it None is the honest default. See SCAN_MULTIPV.
        self.second = second        # chess.engine.PovScore, or None

    @property
    def white_eval(self) -> dict:
        if self.score is None:
            return {"score": None, "mate_in": None}
        return _white_frame(self.score)

    def cp_for(self, mover: bool) -> Optional[int]:
        return None if self.score is None else _mover_cp(self.score, mover)


def evaluate_position(fen: str, depth: int = SCAN_DEPTH, multipv: int = None) -> PositionView:
    """
    One single-PV search. The scan's whole engine cost, and its only one.

    A finished position (mate or stalemate) has no line to search, so it is
    answered from the board rather than from the engine - asking Stockfish for
    a move in a position with none is how you get an exception where you
    wanted an evaluation.
    """
    board = chess.Board(fen)
    if board.is_game_over():
        return PositionView(fen, None, None, [], depth)
    multipv = multipv or SCAN_MULTIPV
    if multipv <= 1:
        info = stockfish_service.analyse(board, chess.engine.Limit(depth=depth))
        pv = list(info.get("pv") or [])[:PV_PLIES]
        return PositionView(fen, info.get("score"), pv[0] if pv else None, pv, depth)

    infos = stockfish_service.analyse(board, chess.engine.Limit(depth=depth), multipv=multipv)
    if isinstance(infos, dict):  # a position with one legal line comes back unwrapped
        infos = [infos]
    pv = list(infos[0].get("pv") or [])[:PV_PLIES]
    # Absent when the position has only one legal move, which is exactly the
    # case `grade_from_scores` already answers with "forced" before it ever
    # looks at a score.
    second = infos[1].get("score") if len(infos) > 1 else None
    return PositionView(fen, infos[0].get("score"), pv[0] if pv else None, pv, depth, second)


def build_evidence(
    ply: int,
    before: PositionView,
    after: PositionView,
    move: chess.Move,
    san: str,
    second_cp: Optional[int] = None,
    played_cp_override: Optional[int] = None,
) -> dict:
    """
    The evidence packet for one half-move, from two already-evaluated
    positions. Pure - it makes no engine calls of its own.

    `before` is the position the move was played from, `after` the position it
    produced. The grade comes from `move_quality.grade_from_scores`, which is
    the live game's own function; see the module docstring for why the scores
    reach it this way rather than through a second search.
    """
    board = chess.Board(before.fen)
    mover = board.turn
    board_after = chess.Board(after.fen)

    best_cp = before.cp_for(mover)
    # The runner-up, when the search measured one. Passed explicitly by callers
    # that have it another way; otherwise taken from the position's own view,
    # which carries it only when SCAN_MULTIPV asked for two lines. None means
    # "not measured", and grade_from_scores reads that as "Great cannot be
    # detected here" rather than as evidence against it.
    if second_cp is None and before.second is not None:
        second_cp = _mover_cp(before.second, mover)
    # What the move actually achieved, in the mover's frame. The position
    # after it is scored from the opponent's point of view by definition, so
    # `cp_for(mover)` is the same number negated - handled inside `_to_cp`'s
    # pov call rather than by flipping a sign here, which is the kind of
    # thing that is wrong once and then wrong everywhere.
    #
    # `played_cp_override` is how the single-move path (analyse_single_ply)
    # hands in a score taken from the SAME search tree as `best_cp` rather
    # than from a search of the position after the move. The scan cannot do
    # that - reusing each position's evaluation twice is what makes it
    # affordable - but a move the user has actually stopped and asked about is
    # worth the exact number, and this is where it arrives.
    if board_after.is_checkmate():
        played_cp = move_quality.MATE_SCORE - 10
    elif board_after.is_game_over():
        played_cp = 0
    elif played_cp_override is not None:
        played_cp = played_cp_override
    else:
        played_cp = after.cp_for(mover)

    quality = None
    if best_cp is not None and played_cp is not None:
        # Book and forced are facts about the position rather than about how
        # well the player chose, and the live grader treats them as such
        # before ever consulting the engine. Same order here, so the same move
        # gets the same label in both places.
        book_here = move_quality.OPENING_BOOK.get(move_quality._position_key(board), {})
        if move.uci() in book_here:
            names = book_here[move.uci()]
            opening = next(iter(names)) if len(names) == 1 else None
            quality = move_quality._result(
                "book", opening=opening, cpl=0, best_move=None, accuracy=100.0,
                depth=None, grade_source="book", confidence="high", note=None,
            )
        elif board.legal_moves.count() == 1:
            quality = move_quality._result(
                "forced", cpl=0, best_move=move.uci(), accuracy=None,
                depth=None, grade_source="rules", confidence="high", note=None,
            )
        else:
            # The scan's two scores come from searches of neighbouring
            # positions, one ply apart, so they share a depth but not a
            # horizon - the same asymmetry classify_move now avoids with
            # root_moves, and one the scan cannot avoid without giving up the
            # halving that makes it affordable. Passing the depth through is
            # what lets grade_from_scores hold its critical labels to the
            # margin instead of reading that asymmetry as a mistake.
            quality = move_quality.grade_from_scores(
                before.fen, move, before.best_move, best_cp, played_cp, second_cp,
                depth=before.depth,
            )

    best_move = before.best_move
    return {
        "ply": ply,
        "san": san,
        "uci": move.uci(),
        "color": "white" if mover else "black",
        "phase": phase_of(ply),
        "fen_before": before.fen,
        "fen_after": after.fen,
        "eval_before": before.white_eval,
        "eval_after": (
            {"score": None, "mate_in": 0 if board_after.is_checkmate() else None}
            if board_after.is_game_over() else after.white_eval
        ),
        "best_move": best_move.uci() if best_move else None,
        "best_san": board.san(best_move) if best_move else None,
        "pv_san": _san_line(board, before.pv),
        "cpl": (quality or {}).get("cpl"),
        "quality": quality,
        "depth": before.depth,
    }


def analyse_single_ply(fen_before: str, uci: str, depth: int = PROBE_DEPTH) -> Optional[dict]:
    """
    One move, analysed on its own at full depth.

    Three searches rather than the scan's one, because there is no neighbouring
    position whose result can be reused. This is the path for a move the user
    has actually asked about - a what-if they just played, or a position they
    have stopped on - where the extra searches buy a number worth quoting.

    The third one is what makes the number exact. `before` and `after` are
    searches of two different positions one ply apart, so they do not share a
    horizon and their difference measures the horizon as well as the move; the
    scan lives with that because halving its engine time is worth more than the
    last few centipawns, and grade_from_scores holds its critical labels to a
    margin because of it. Here there is one move and no budget to protect, so
    the played move is also scored from the SAME root as the engine's
    preference, restricted with root_moves. `after` is still searched, because
    the eval curve and the "position after" figure the UI shows are genuinely
    about that position.
    """
    try:
        board = chess.Board(fen_before)
        move = chess.Move.from_uci(uci)
    except ValueError:
        return None
    if move not in board.legal_moves or not board.is_valid():
        return None

    before = evaluate_position(fen_before, depth)
    board.push(move)
    after = evaluate_position(board.fen(), depth)
    board.pop()

    played_cp = None
    if before.best_move is not None and move != before.best_move:
        try:
            info = stockfish_service.analyse(
                board, chess.engine.Limit(depth=depth), root_moves=[move]
            )
            if isinstance(info, list):
                info = info[0]
            played_cp = move_quality._to_cp(info["score"].pov(board.turn))
        except Exception:
            # The horizon-matched number is an improvement, not a dependency.
            # Without it the scan's own pairing still produces a grade.
            played_cp = None
    elif before.best_move is not None:
        played_cp = before.cp_for(board.turn)

    return build_evidence(0, before, after, move, board.san(move),
                          played_cp_override=played_cp)


def summarise(evidence_by_ply: list) -> dict:
    """
    Per-colour accuracy and grade counts for a whole scanned game.

    `move_quality.summarize_accuracy` does the aggregation, so the number here
    is the same number the live game's review panel shows. The caveat is
    reported rather than hidden: at the default `SCAN_MULTIPV` this pass cannot
    detect "Great", and a summary that silently lacked a category would look
    like a game that simply never had one. It is reported CONDITIONALLY, since
    with two principal variations the runner-up is measured and the grade
    becomes available - saying it is missing then would be untrue in the other
    direction.
    """
    white = [e["quality"] for e in evidence_by_ply if e.get("color") == "white"]
    black = [e["quality"] for e in evidence_by_ply if e.get("color") == "black"]
    graded = [e for e in evidence_by_ply if e.get("quality")]
    worst = sorted(
        (e for e in graded if (e["quality"].get("cpl") or 0) > 0),
        key=lambda e: e["quality"].get("cpl") or 0,
        reverse=True,
    )
    return {
        "white": move_quality.summarize_accuracy(white),
        "black": move_quality.summarize_accuracy(black),
        # The moves worth opening the review on. Deterministic - biggest
        # centipawn loss first, ties broken by ply - so the same game always
        # produces the same list, which is what makes it something to build
        # turning-point detection on rather than a suggestion that moves
        # around between runs.
        "turning_points": [
            {
                "ply": e["ply"],
                "san": e["san"],
                "color": e["color"],
                "cpl": e["quality"].get("cpl"),
                "label": e["quality"].get("label"),
                "phase": e["phase"],
            }
            for e in sorted(worst[:6], key=lambda e: e["ply"])
        ],
        "depth": evidence_by_ply[0]["depth"] if evidence_by_ply else None,
        # Which grades this pass could not award, so the Report can say so
        # rather than leaving a category silently missing - a game with no
        # "Great" in it looks exactly like a game where Great was never
        # possible. Conditional now: with SCAN_MULTIPV at 2 the runner-up IS
        # measured and Great can be detected, so claiming otherwise would be
        # the same kind of untrue statement in the other direction.
        "grades_unavailable": [] if SCAN_MULTIPV >= 2 else ["great"],
    }
