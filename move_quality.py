"""
Chess.com-style move quality classification.

Every played half-move gets graded - Brilliant, Great, Best, Excellent,
Good, Book, Inaccuracy, Mistake, Blunder, Miss, Forced - by comparing what
was actually played against what Stockfish considers best in the same
position. The grade is attached to the move's game_history entry (see
app.py's schedule_move_quality) and rendered as a badge on the board and
in the move list.

Why the grading runs here and not inside stockfish_service: the existing
service is built around *choosing* the AI's move (rank every legal move at
depth 15, hand a window to Gemini). Grading needs something different and
much cheaper - a single position analysed with multipv=2, plus one look at
the position the played move actually reached. Reusing get_ranked_moves()
would analyse every legal move twice per ply for information we don't need.

Grading is deliberately run at a shallower depth than move selection. It
happens after every single half-move by both sides, so it sits directly in
the "how soon does a badge appear" path, whereas move selection happens
once per AI turn and can afford depth 15.
"""
# The runtime is Python 3.9 (see the Dockerfile), where `dict | None` in an
# annotation is a TypeError at import time rather than 3.10+ syntax. Making
# annotations lazy keeps the modern spelling readable here without pinning
# the image to a newer Python.
from __future__ import annotations

import math

import chess
import chess.engine
import logging

from stockfish_service import SEARCH_DEPTH, stockfish_service

logger = logging.getLogger(__name__)

# Matches stockfish_service's SEARCH_DEPTH deliberately. Grading at a
# shallower depth than the AI selects at produced a visible incoherence:
# the AI would play a move its own selector rated best at depth 15, and the
# grader would score it an Inaccuracy from a depth-12 view of the same
# position. Same depth, same verdict.
CLASSIFY_DEPTH = SEARCH_DEPTH

# Forced mates are reported by the engine as "mate in N", not a centipawn
# number, but the thresholds below need a single comparable scale. Mates map
# to a value far above any real centipawn eval, scaled so a faster mate
# beats a slower one (mate in 1 > mate in 5) and, on the losing side, being
# mated sooner is worse than being mated later.
MATE_SCORE = 10000

PIECE_VALUES = {
    chess.PAWN: 100,
    chess.KNIGHT: 320,
    chess.BISHOP: 330,
    chess.ROOK: 500,
    chess.QUEEN: 900,
    chess.KING: 0,
}

# label -> (display name, chess.com-style annotation glyph). The frontend
# keys its colors and icons off `label`, so these strings are part of the
# API contract with ChessBoard.tsx - don't rename one without the other.
#
# Excellent and Good previously shared the "✓" glyph and near-identical
# greens, which made three of the eleven grades read as one thing at badge
# size. They now differ in both glyph and color.
QUALITY_META = {
    "brilliant":  ("Brilliant", "!!"),
    "great":      ("Great", "!"),
    "best":       ("Best", "★"),
    "excellent":  ("Excellent", "✓"),
    "good":       ("Good", "○"),
    "book":       ("Book", "◇"),
    "inaccuracy": ("Inaccuracy", "?!"),
    "mistake":    ("Mistake", "?"),
    "blunder":    ("Blunder", "??"),
    "miss":       ("Miss", "✗"),
    "forced":     ("Forced", "□"),
}

# Grades that say nothing about how well the player chose, and so are
# excluded from the accuracy average: a forced move had no alternative, and
# a book move is memorised theory rather than a decision at the board.
NON_JUDGING_LABELS = {"forced", "book"}

# Centipawn-loss thresholds (how much worse than the engine's best move the
# played move is), from the mover's point of view. Anything above the last
# threshold is a blunder.
EXCELLENT_MAX = 20
GOOD_MAX = 50
INACCURACY_MAX = 100
MISTAKE_MAX = 250

# How far past a threshold a move has to sit before it is called something
# critical. This is not a taste setting: it is the engine's own measured
# repeatability. Asking the shared Stockfish the same question at the same
# depth three times running moves the answer by a median of 5cp, a 90th
# percentile of 18cp and a maximum of 25cp - it keeps its hash between
# searches (see CLAUDE.md 6), so near-equal moves reorder and rescore
# between runs. Measured over 25 candidate moves across six positions, four
# of them changed grade on repetition alone.
#
# So a move whose loss sits within this margin of a boundary has not been
# shown to be on the worse side of it. Rather than pretend otherwise, it
# takes the gentler label and is marked low confidence, which is what
# `confidence` below is for. The margin only ever softens: it can move a
# grade from "mistake" to "inaccuracy", never the other way.
NOISE_MARGIN = 25

# --- Opening book -----------------------------------------------------
#
# Rather than shipping a Polyglot .bin (another binary asset to keep in the
# image, and one more thing that can go missing at runtime), the book is
# built at import time by replaying these main lines with python-chess. It
# only needs to be deep and broad enough to cover the openings a casual
# game actually reaches - past ~10 plies real games leave book anyway, and
# a move that isn't found here simply gets graded normally instead.
OPENING_LINES = [
    ("Ruy Lopez", "e4 e5 Nf3 Nc6 Bb5 a6 Ba4 Nf6 O-O Be7 Re1 b5 Bb3 d6 c3 O-O"),
    ("Italian Game", "e4 e5 Nf3 Nc6 Bc4 Bc5 c3 Nf6 d3 d6 O-O O-O"),
    ("Two Knights Defense", "e4 e5 Nf3 Nc6 Bc4 Nf6 Ng5 d5 exd5 Na5"),
    ("Scotch Game", "e4 e5 Nf3 Nc6 d4 exd4 Nxd4 Bc5 Be3 Qf6"),
    ("Four Knights Game", "e4 e5 Nf3 Nc6 Nc3 Nf6 Bb5 Bb4 O-O O-O"),
    ("Petrov Defense", "e4 e5 Nf3 Nf6 Nxe5 d6 Nf3 Nxe4 d4 d5"),
    ("Vienna Game", "e4 e5 Nc3 Nf6 f4 d5 fxe5 Nxe4"),
    ("King's Gambit", "e4 e5 f4 exf4 Nf3 g5 h4 g4"),
    ("Philidor Defense", "e4 e5 Nf3 d6 d4 exd4 Nxd4 Nf6 Nc3 Be7"),
    ("Sicilian Najdorf", "e4 c5 Nf3 d6 d4 cxd4 Nxd4 Nf6 Nc3 a6 Be3 e5"),
    ("Sicilian Dragon", "e4 c5 Nf3 d6 d4 cxd4 Nxd4 Nf6 Nc3 g6 Be3 Bg7"),
    ("Sicilian Classical", "e4 c5 Nf3 d6 d4 cxd4 Nxd4 Nf6 Nc3 Nc6 Bg5 e6"),
    ("Sicilian Sveshnikov", "e4 c5 Nf3 Nc6 d4 cxd4 Nxd4 Nf6 Nc3 e5 Ndb5 d6"),
    ("Sicilian Accelerated Dragon", "e4 c5 Nf3 Nc6 d4 cxd4 Nxd4 g6 c4 Bg7"),
    ("Sicilian Taimanov", "e4 c5 Nf3 e6 d4 cxd4 Nxd4 Nc6 Nc3 Qc7"),
    ("Sicilian Alapin", "e4 c5 c3 d5 exd5 Qxd5 d4 Nf6 Nf3 Bg4"),
    ("Sicilian Closed", "e4 c5 Nc3 Nc6 g3 g6 Bg2 Bg7 d3 d6"),
    ("French Defense", "e4 e6 d4 d5 Nc3 Nf6 Bg5 Be7 e5 Nfd7"),
    ("French Winawer", "e4 e6 d4 d5 Nc3 Bb4 e5 c5 a3 Bxc3+ bxc3 Ne7"),
    ("French Advance", "e4 e6 d4 d5 e5 c5 c3 Nc6 Nf3 Qb6"),
    ("French Tarrasch", "e4 e6 d4 d5 Nd2 Nf6 e5 Nfd7 Bd3 c5"),
    ("Caro-Kann Classical", "e4 c6 d4 d5 Nc3 dxe4 Nxe4 Bf5 Ng3 Bg6 h4 h6"),
    ("Caro-Kann Advance", "e4 c6 d4 d5 e5 Bf5 Nf3 e6 Be2 c5"),
    ("Caro-Kann Panov", "e4 c6 d4 d5 exd5 cxd5 c4 Nf6 Nc3 e6"),
    ("Scandinavian Defense", "e4 d5 exd5 Qxd5 Nc3 Qa5 d4 Nf6 Nf3 c6"),
    ("Pirc Defense", "e4 d6 d4 Nf6 Nc3 g6 Nf3 Bg7 Be2 O-O"),
    ("Modern Defense", "e4 g6 d4 Bg7 Nc3 d6 f4 Nf6"),
    ("Alekhine Defense", "e4 Nf6 e5 Nd5 d4 d6 Nf3 g6"),
    ("Queen's Gambit Declined", "d4 d5 c4 e6 Nc3 Nf6 Bg5 Be7 e3 O-O Nf3 h6"),
    ("Queen's Gambit Accepted", "d4 d5 c4 dxc4 Nf3 Nf6 e3 e6 Bxc4 c5"),
    ("Slav Defense", "d4 d5 c4 c6 Nf3 Nf6 Nc3 dxc4 a4 Bf5"),
    ("Semi-Slav Defense", "d4 d5 c4 c6 Nf3 Nf6 Nc3 e6 e3 Nbd7"),
    ("Nimzo-Indian Defense", "d4 Nf6 c4 e6 Nc3 Bb4 e3 O-O Bd3 d5"),
    ("Queen's Indian Defense", "d4 Nf6 c4 e6 Nf3 b6 g3 Ba6 b3 Bb4+"),
    ("Bogo-Indian Defense", "d4 Nf6 c4 e6 Nf3 Bb4+ Bd2 Qe7"),
    ("King's Indian Defense", "d4 Nf6 c4 g6 Nc3 Bg7 e4 d6 Nf3 O-O Be2 e5"),
    ("Gruenfeld Defense", "d4 Nf6 c4 g6 Nc3 d5 cxd5 Nxd5 e4 Nxc3 bxc3 Bg7"),
    ("Catalan Opening", "d4 Nf6 c4 e6 g3 d5 Bg2 Be7 Nf3 O-O"),
    ("Benoni Defense", "d4 Nf6 c4 c5 d5 e6 Nc3 exd5 cxd5 d6"),
    ("Benko Gambit", "d4 Nf6 c4 c5 d5 b5 cxb5 a6"),
    ("Dutch Defense", "d4 f5 g3 Nf6 Bg2 e6 Nf3 Be7 O-O O-O"),
    ("London System", "d4 d5 Bf4 Nf6 e3 e6 Nf3 Bd6 Bg3 O-O"),
    ("Trompowsky Attack", "d4 Nf6 Bg5 Ne4 Bf4 d5"),
    ("English Opening", "c4 e5 Nc3 Nf6 Nf3 Nc6 g3 d5 cxd5 Nxd5"),
    ("English Symmetrical", "c4 c5 Nf3 Nf6 Nc3 Nc6 g3 g6 Bg2 Bg7"),
    ("Reti Opening", "Nf3 d5 c4 e6 g3 Nf6 Bg2 Be7 O-O O-O"),
    ("King's Indian Attack", "Nf3 d5 g3 Nf6 Bg2 e6 O-O Be7 d3 O-O"),
    ("Bird Opening", "f4 d5 Nf3 Nf6 e3 g6 b3 Bg7"),
]


def _position_key(board: chess.Board) -> str:
    """
    Book identity for a position: piece placement, side to move, castling
    rights and en-passant square - i.e. the FEN without the halfmove clock
    and fullmove number. Those two counters differ between transpositions
    that are otherwise the same position, and would stop an identical
    position reached by a different move order from matching the book.
    """
    return " ".join(board.fen().split(" ")[:4])


def _build_book() -> dict:
    """
    position key -> {uci move -> set of opening names that play it here}.

    A set, not a single name, because most early moves belong to many
    openings at once. The previous version kept whichever line loaded first,
    which made the app state things that were simply false - 1.e4 came back
    labelled "Ruy Lopez", and 1...c5 came back "Sicilian Najdorf" six moves
    before a Najdorf could exist. Now a name is only shown when exactly one
    line in the book plays that move from that position (see classify_move),
    so a move is named once it is genuinely distinctive and reads as a plain
    "Book" until then.
    """
    book: dict = {}
    for name, line in OPENING_LINES:
        board = chess.Board()
        for san in line.split():
            try:
                move = board.parse_san(san)
            except ValueError:
                logger.warning(f"⚠️ Bad SAN '{san}' in opening line '{name}' - skipping rest of line")
                break
            book.setdefault(_position_key(board), {}).setdefault(move.uci(), set()).add(name)
            board.push(move)
    return book


OPENING_BOOK = _build_book()


def _to_cp(score: chess.engine.PovScore) -> int:
    """
    Collapse a PovScore into a single comparable centipawn number, with
    forced mates mapped onto the MATE_SCORE scale (see MATE_SCORE above).
    """
    mate = score.mate()
    if mate is None:
        cp = score.score()
        return 0 if cp is None else cp
    if mate > 0:
        return MATE_SCORE - mate * 10
    return -MATE_SCORE - mate * 10


def _is_mate_score(cp: int) -> bool:
    return abs(cp) >= MATE_SCORE - 1000


def _win_percent(cp: int) -> float:
    """
    Centipawns -> expected score out of 100, from the mover's point of view.

    Centipawn loss on its own is a poor accuracy signal because it isn't
    linear in practical terms: 100cp thrown away from a dead-equal position
    changes the game, the same 100cp thrown away when already up a rook
    changes nothing. Converting to a win percentage first makes a
    "how much did this actually cost me" scale, which is what accuracy is
    meant to measure. Logistic curve as popularised by Lichess.
    """
    return 50.0 + 50.0 * (2.0 / (1.0 + math.exp(-0.00368208 * cp)) - 1.0)


def move_accuracy(best_cp: int, played_cp: int) -> float:
    """
    Accuracy for a single move, 0-100, from the drop in win percentage
    between the best move available and the one actually played.
    """
    before = _win_percent(best_cp)
    after = _win_percent(played_cp)
    drop = max(0.0, before - after)
    raw = 103.1668 * math.exp(-0.04354 * drop) - 3.1669
    return round(max(0.0, min(100.0, raw)), 1)


def summarize_accuracy(qualities) -> dict:
    """
    Aggregate a list of per-move quality dicts (one color's moves, in order)
    into {"accuracy": float|None, "counts": {label: n}, "graded": n}.

    A plain mean over judged moves. Deliberately not chess.com's volatility
    weighting - that needs the whole eval curve and would give a number that
    looks authoritative while being differently wrong.
    """
    counts: dict = {}
    scores = []
    for q in qualities:
        if not q:
            continue
        label = q.get("label")
        counts[label] = counts.get(label, 0) + 1
        if label in NON_JUDGING_LABELS:
            continue
        if q.get("accuracy") is not None:
            scores.append(q["accuracy"])
    return {
        "accuracy": round(sum(scores) / len(scores), 1) if scores else None,
        "counts": counts,
        "graded": len(scores),
    }


def _static_exchange_eval(board: chess.Board, move: chess.Move) -> int:
    """
    Static exchange evaluation: material won or lost if both sides trade
    optimally on `move`'s destination square, in centipawns from the
    mover's point of view. Negative means the move loses material.

    Replaces the earlier "is the piece attacked and worth more than what it
    captured" guess, which couldn't see that a defended piece is not a
    sacrifice, and missed exchange sacrifices outright (rook for bishop is
    only 170cp of nominal difference, under the old 200cp cutoff).
    """
    square = move.to_square
    captured = board.piece_at(square)
    gain = [PIECE_VALUES[captured.piece_type] if captured else 0]
    if board.is_en_passant(move):
        gain = [PIECE_VALUES[chess.PAWN]]

    working = board.copy(stack=False)
    attacker_piece = working.piece_at(move.from_square)
    if attacker_piece is None:
        return 0
    working.remove_piece_at(move.from_square)
    working.set_piece_at(square, attacker_piece)
    on_square = PIECE_VALUES[attacker_piece.piece_type]
    side = not board.turn

    # Alternate sides, each recapturing with its least valuable attacker,
    # accumulating what the running exchange is worth.
    while True:
        attackers = working.attackers(side, square)
        if not attackers:
            break
        cheapest_sq = None
        cheapest_val = None
        for sq in attackers:
            piece = working.piece_at(sq)
            if piece is None:
                continue
            value = PIECE_VALUES[piece.piece_type]
            if cheapest_val is None or value < cheapest_val:
                cheapest_val, cheapest_sq = value, sq
        if cheapest_sq is None:
            break
        gain.append(on_square - gain[-1])
        piece = working.piece_at(cheapest_sq)
        working.remove_piece_at(cheapest_sq)
        working.set_piece_at(square, piece)
        on_square = cheapest_val
        side = not side
        # A king that recaptures into a still-defended square would be
        # illegal; stop rather than model it.
        if piece.piece_type == chess.KING and working.attackers(not side, square):
            break

    # Fold the exchange back: at each step the side to move could stand pat
    # instead of continuing a losing capture sequence.
    for i in range(len(gain) - 2, -1, -1):
        gain[i] = -max(-gain[i], gain[i + 1])
    return gain[0]


def _result(label: str, **extra) -> dict:
    name, symbol = QUALITY_META[label]
    return {"label": label, "name": name, "symbol": symbol, **extra}


def classify_move(fen_before: str, move_uci: str, depth: int = CLASSIFY_DEPTH) -> dict | None:
    """
    Grade `move_uci`, played from position `fen_before`, chess.com style.

    Returns a dict shaped like
    {"label": "blunder", "name": "Blunder", "symbol": "??", "cpl": 380,
     "best_move": "d2d4"} - or None if the move isn't legal in that
    position (the caller has nothing sensible to show in that case).

    Blocking: runs one or two analyses against the shared Stockfish
    process, and waits its turn behind any search already running there.
    Call it off the event loop (app.py grades on a thread pool).
    """
    try:
        board = chess.Board(fen_before)
        move = chess.Move.from_uci(move_uci)
    except ValueError:
        logger.warning(f"⚠️ Cannot grade move - bad FEN or UCI: {fen_before} / {move_uci}")
        return None

    if move not in board.legal_moves:
        logger.warning(f"⚠️ Cannot grade move - {move_uci} illegal in {fen_before}")
        return None

    # Stockfish segfaults on a position that isn't reachable in a real game
    # (most easily: the side not to move left in check) rather than
    # rejecting it, taking the engine process down with it. Positions from
    # actual play are always valid, so this only guards against a corrupted
    # or hand-written FEN reaching the engine.
    if not board.is_valid():
        logger.warning(f"⚠️ Cannot grade move - illegal position {fen_before}")
        return None

    # Book and forced are decided without touching the engine - both are
    # facts about the position, not about how good the move is, and both
    # would otherwise be graded misleadingly (an opening move that isn't
    # the engine's top pick is "book", not "inaccuracy"; the only legal
    # move in a position can hardly be a blunder).
    book_here = OPENING_BOOK.get(_position_key(board), {})
    if move.uci() in book_here:
        names = book_here[move.uci()]
        # Named only when this position+move belongs to exactly one line in
        # the book - otherwise the label would be an arbitrary pick among
        # every opening sharing these first moves.
        opening = next(iter(names)) if len(names) == 1 else None
        # Book and forced carry the same four provenance fields every other
        # grade does, so a consumer never has to special-case their absence.
        # Both are facts about the position rather than engine verdicts, hence
        # a source of "book"/"rules" rather than "stockfish".
        return _result("book", opening=opening, cpl=0, best_move=None, accuracy=100.0,
                       depth=None, grade_source="book", confidence="high", note=None)

    if board.legal_moves.count() == 1:
        return _result("forced", cpl=0, best_move=move.uci(), accuracy=None,
                       depth=None, grade_source="rules", confidence="high", note=None)

    mover = board.turn
    try:
        # Shares the one long-lived engine owned by stockfish_service
        # rather than spawning a Stockfish per grade. Grading runs after
        # *every* half-move by both sides, so it was the single biggest
        # source of process churn in the app.
        limit = chess.engine.Limit(depth=depth)
        infos = stockfish_service.analyse(board, limit, multipv=2)
        if not infos:
            return None
        if isinstance(infos, dict):  # only one legal line - comes back unwrapped
            infos = [infos]

        best_info = infos[0]
        pv = best_info.get("pv") or []
        best_move = pv[0] if pv else None
        best_cp = _to_cp(best_info["score"].pov(mover))
        second_cp = _to_cp(infos[1]["score"].pov(mover)) if len(infos) > 1 else None

        if best_move is not None and move == best_move:
            played_cp = best_cp
        else:
            # Scored from the SAME position, restricted to this one move,
            # rather than by searching the position the move produces.
            #
            # The old implementation pushed the move and searched the child at
            # the same nominal depth, which is one ply deeper in the tree than
            # the search that produced best_cp. Subtracting two evaluations
            # taken from different horizons measures the horizon as well as the
            # move, and it does so asymmetrically: the child search sees one
            # more ply of the opponent's reply, so it tends to report the
            # played move as worse than the root search would. Measured across
            # 48 candidate moves in twelve positions, the two views of the same
            # move differed by up to 46cp and disagreed on the grade in 10% of
            # cases, three quarters of them in the harsher direction - one
            # crossing a real move from "good" into "inaccuracy".
            #
            # root_moves puts the played move and the engine's own preference
            # in the same search tree at the same depth, so the difference
            # between them is the move and nothing else. It costs the same one
            # extra search the child search did.
            info = stockfish_service.analyse(board, limit, root_moves=[move])
            if isinstance(info, list):  # some builds wrap even a single line
                info = info[0]
            played_cp = _to_cp(info["score"].pov(mover))
    except Exception as e:
        logger.warning(f"⚠️ Move grading failed for {move_uci}: {e}")
        return None

    return grade_from_scores(
        fen_before, move, best_move, best_cp, played_cp, second_cp, depth=depth
    )


def grade_from_scores(
    fen_before: str,
    move: chess.Move,
    best_move,
    best_cp: int,
    played_cp: int,
    second_cp: int = None,
    depth: int = None,
) -> dict:
    """
    Turn two engine scores into a chess.com-style grade. No engine calls.

    Split out of `classify_move` so that there is exactly one implementation
    of these thresholds. `classify_move` is the live game's path: it searches
    the position itself, with multipv=2, and hands the numbers straight here.
    The Post-Mortem scan (postmortem_analysis.py) reaches the same numbers a
    different way - it walks a finished game evaluating each position once and
    reusing each result as both "after move i" and "before move i+1", which
    halves the engine time over a whole game - and then calls this so a move
    graded in a review reads identically to the same move graded in play.

    `best_cp` and `played_cp` are centipawns from the MOVER's point of view on
    the MATE_SCORE scale (see `_to_cp`). `second_cp` is the second-best move's
    score where the caller has it; without it the "Great" grade cannot be
    detected, since being the only good move is a claim about the runner-up.
    `depth` is what search produced them, and it travels with the grade rather
    than being dropped: a verdict from depth 8 and a verdict from depth 15 are
    not the same claim, and everything downstream is entitled to know which one
    it is holding.

    Three fields come back beside the label, because this app is a coach and
    not a scoreboard:

    - `confidence` - "high" or "low". Low means the evidence does not actually
      separate this move from the gentler grade next door, either because the
      loss sits within the engine's own measured repeatability of a threshold
      (NOISE_MARGIN) or because the search was too shallow to be drawing fine
      distinctions with.
    - `note` - what to say instead, in words rather than centipawns, when
      confidence is low.
    - `grade_source` - always "stockfish". Written here, at the one place a
      grade is ever minted, so that anything downstream saying where a verdict
      came from is quoting a field rather than assuming an answer.
    """
    # Clamped at 0: the played move can still come out marginally *ahead* of
    # the "best" move, because multipv orders lines that the restricted search
    # then rescores. A negative loss would read as a below-zero centipawn loss.
    loss = max(0, best_cp - played_cp)
    is_best = best_move is not None and move == best_move
    best_uci = best_move.uci() if best_move else None

    # A search this shallow is not evidence for a fine distinction. The live
    # grader runs at STOCKFISH_DEPTH (15 locally, 12 on Render) and the
    # Post-Mortem scan at POSTMORTEM_SCAN_DEPTH (12); both are comfortably
    # above this. A deployment that turns either down to save CPU should say
    # so on the badge rather than go on asserting the same confidence.
    shallow = depth is not None and depth < 10

    detail = {
        "cpl": loss,
        "best_move": best_uci,
        "accuracy": move_accuracy(best_cp, played_cp),
        "depth": depth,
        "grade_source": "stockfish",
        # The two numbers the grade was actually computed from, kept rather
        # than discarded. Both are in the MOVER's frame on the MATE_SCORE
        # scale - not White's absolute frame, which is what the eval BAR uses.
        # Carrying them means a disputed grade can be checked against its own
        # inputs instead of re-derived, and it is what move_feedback_log logs.
        "eval_before": best_cp,
        "eval_after": played_cp,
        "eval_perspective": "mover",
    }

    def graded(label: str, confidence: str = "high", note: str = None) -> dict:
        # A shallow search caps confidence however clear the numbers look, and
        # this only ever downgrades - nothing here can talk confidence up.
        if shallow and label not in NON_JUDGING_LABELS:
            confidence = "low"
            note = note or "hard to judge at this depth"
        return _result(label, confidence=confidence, note=note, **detail)

    # Throwing away a forced mate is its own category - "Miss" - rather
    # than a blunder, matching how chess.com reports it. Without this the
    # MATE_SCORE scale would make every missed mate a ~10000cp blunder,
    # which is technically true and completely unhelpful.
    if _is_mate_score(best_cp) and best_cp > 0 and not (_is_mate_score(played_cp) and played_cp > 0):
        return graded("miss")

    if is_best or loss <= 30:
        # A sound sacrifice: the exchange on that square loses material
        # outright, yet the engine still rates the move as (essentially)
        # best and the position isn't lost afterwards.
        if played_cp > -100 and _static_exchange_eval(chess.Board(fen_before), move) <= -150:
            return graded("brilliant")
        # The only move that holds the position together - everything else
        # drops at least a pawn and a half. chess.com's "Great".
        if is_best and second_cp is not None and best_cp - second_cp >= 150:
            return graded("great")

    if is_best:
        return graded("best")
    if loss <= EXCELLENT_MAX:
        return graded("excellent")
    if loss <= GOOD_MAX:
        return graded("good")

    # Everything below here is a criticism, and a criticism the evidence does
    # not carry is the single failure this module exists to avoid: a player
    # told their good move was a mistake stops believing every grade after it,
    # including all the correct ones. So a critical label has to clear its
    # threshold by NOISE_MARGIN - the engine's own measured run-to-run spread -
    # before it is used. Inside that band the move takes the gentler label next
    # door and says out loud that it is a close call.
    if loss <= INACCURACY_MAX:
        if loss <= GOOD_MAX + NOISE_MARGIN:
            return graded(
                "good", confidence="low",
                note="not the engine's top choice, but inside the margin this search can resolve",
            )
        return graded("inaccuracy")
    if loss <= MISTAKE_MAX:
        if loss <= INACCURACY_MAX + NOISE_MARGIN:
            return graded(
                "inaccuracy", confidence="low",
                note="on the boundary between an inaccuracy and a mistake at this depth",
            )
        return graded("mistake")

    # Softening for positions that are still completely winning after the
    # move: dropping from "mate in 4" to "up a full queen" is a real
    # inaccuracy, but calling it a blunder while the player is still
    # crushing reads as noise rather than feedback.
    if played_cp >= 600:
        return graded(
            "inaccuracy", confidence="low",
            note="costly on paper, but the position is still winning",
        )
    if loss <= MISTAKE_MAX + NOISE_MARGIN:
        return graded(
            "mistake", confidence="low",
            note="on the boundary between a mistake and a blunder at this depth",
        )
    return graded("blunder")
