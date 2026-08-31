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

import chess
import chess.engine
import logging

from stockfish_service import STOCKFISH_PATH

logger = logging.getLogger(__name__)

# Shallower than stockfish_service's SEARCH_DEPTH (15) on purpose - see the
# module docstring. Depth 12 still separates a blunder from a good move
# reliably; what it costs is precision on very deep tactics, which the
# centipawn thresholds below are far too coarse to care about anyway.
CLASSIFY_DEPTH = 12

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
QUALITY_META = {
    "brilliant":  ("Brilliant", "!!"),
    "great":      ("Great", "!"),
    "best":       ("Best", "★"),
    "excellent":  ("Excellent", "✓"),
    "good":       ("Good", "✓"),
    "book":       ("Book", "📖"),
    "inaccuracy": ("Inaccuracy", "?!"),
    "mistake":    ("Mistake", "?"),
    "blunder":    ("Blunder", "??"),
    "miss":       ("Miss", "✗"),
    "forced":     ("Forced", "□"),
}

# Centipawn-loss thresholds (how much worse than the engine's best move the
# played move is), from the mover's point of view. Anything above the last
# threshold is a blunder.
EXCELLENT_MAX = 20
GOOD_MAX = 50
INACCURACY_MAX = 100
MISTAKE_MAX = 250

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
    """position key -> {uci moves that are book here} -> opening name."""
    book: dict[str, dict[str, str]] = {}
    for name, line in OPENING_LINES:
        board = chess.Board()
        for san in line.split():
            try:
                move = board.parse_san(san)
            except ValueError:
                logger.warning(f"⚠️ Bad SAN '{san}' in opening line '{name}' - skipping rest of line")
                break
            key = _position_key(board)
            # First line to claim a move names it. Transpositions mean the
            # same move can appear in several lines; the earlier (more
            # mainline) entry wins rather than the last one loaded.
            book.setdefault(key, {}).setdefault(move.uci(), name)
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


def _result(label: str, **extra) -> dict:
    name, symbol = QUALITY_META[label]
    return {"label": label, "name": name, "symbol": symbol, **extra}


def _looks_like_sacrifice(board: chess.Board, move: chess.Move) -> bool:
    """
    Rough "is this move offering material?" test, used as the gate for
    Brilliant. True when the moved piece lands on a square the opponent
    attacks and is worth meaningfully more than whatever it captured -
    so a queen dropped en prise counts, an even trade or a defended
    recapture doesn't.

    This is a heuristic, not a static exchange evaluation: it doesn't work
    out whether the capture is actually good for the opponent. It doesn't
    need to - it only ever downgrades a move that the engine has already
    confirmed is essentially best, so the worst case is a sound sacrifice
    being graded Best or Great instead of Brilliant.
    """
    moving = board.piece_at(move.from_square)
    if moving is None:
        return False
    captured = board.piece_at(move.to_square)
    captured_value = PIECE_VALUES[captured.piece_type] if captured else 0
    if board.is_en_passant(move):
        captured_value = PIECE_VALUES[chess.PAWN]
    offered = PIECE_VALUES[moving.piece_type] - captured_value
    if offered < 200:
        return False
    mover = board.turn
    board.push(move)
    try:
        return board.is_attacked_by(not mover, move.to_square)
    finally:
        board.pop()


def classify_move(fen_before: str, move_uci: str, depth: int = CLASSIFY_DEPTH) -> dict | None:
    """
    Grade `move_uci`, played from position `fen_before`, chess.com style.

    Returns a dict shaped like
    {"label": "blunder", "name": "Blunder", "symbol": "??", "cpl": 380,
     "best_move": "d2d4"} - or None if the move isn't legal in that
    position (the caller has nothing sensible to show in that case).

    Blocking: opens a Stockfish process and runs two analyses. Call it off
    the event loop (app.py uses asyncio.to_thread).
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

    # Book and forced are decided without touching the engine - both are
    # facts about the position, not about how good the move is, and both
    # would otherwise be graded misleadingly (an opening move that isn't
    # the engine's top pick is "book", not "inaccuracy"; the only legal
    # move in a position can hardly be a blunder).
    book_here = OPENING_BOOK.get(_position_key(board), {})
    if move.uci() in book_here:
        return _result("book", opening=book_here[move.uci()], cpl=0, best_move=None)

    if board.legal_moves.count() == 1:
        return _result("forced", cpl=0, best_move=move.uci())

    mover = board.turn
    try:
        with chess.engine.SimpleEngine.popen_uci(STOCKFISH_PATH) as engine:
            limit = chess.engine.Limit(depth=depth)
            infos = engine.analyse(board, limit, multipv=2)
            if not infos:
                return None

            best_info = infos[0]
            pv = best_info.get("pv") or []
            best_move = pv[0] if pv else None
            best_cp = _to_cp(best_info["score"].pov(mover))
            second_cp = _to_cp(infos[1]["score"].pov(mover)) if len(infos) > 1 else None

            if best_move is not None and move == best_move:
                played_cp = best_cp
            else:
                board.push(move)
                try:
                    if board.is_checkmate():
                        played_cp = MATE_SCORE - 10
                    elif board.is_game_over():
                        played_cp = 0
                    else:
                        played_cp = _to_cp(engine.analyse(board, limit)["score"].pov(mover))
                finally:
                    board.pop()
    except Exception as e:
        logger.warning(f"⚠️ Move grading failed for {move_uci}: {e}")
        return None

    # Clamped at 0: the played move can come out marginally *ahead* of the
    # "best" move because the two evals come from searches of different
    # positions (multipv root vs. a fresh search one ply deeper), and a
    # negative loss would otherwise read as a below-zero centipawn loss.
    loss = max(0, best_cp - played_cp)
    is_best = best_move is not None and move == best_move
    best_uci = best_move.uci() if best_move else None
    detail = {"cpl": loss, "best_move": best_uci}

    # Throwing away a forced mate is its own category - "Miss" - rather
    # than a blunder, matching how chess.com reports it. Without this the
    # MATE_SCORE scale would make every missed mate a ~10000cp blunder,
    # which is technically true and completely unhelpful.
    if _is_mate_score(best_cp) and best_cp > 0 and not (_is_mate_score(played_cp) and played_cp > 0):
        return _result("miss", **detail)

    if is_best or loss <= 30:
        # A sound sacrifice: material offered, yet the engine still rates
        # the move as (essentially) the best available and the position
        # isn't lost afterwards.
        if played_cp > -100 and _looks_like_sacrifice(chess.Board(fen_before), move):
            return _result("brilliant", **detail)
        # The only move that holds the position together - everything else
        # drops at least a pawn and a half. chess.com's "Great".
        if is_best and second_cp is not None and best_cp - second_cp >= 150:
            return _result("great", **detail)

    if is_best:
        return _result("best", **detail)
    if loss <= EXCELLENT_MAX:
        return _result("excellent", **detail)
    if loss <= GOOD_MAX:
        return _result("good", **detail)
    if loss <= INACCURACY_MAX:
        return _result("inaccuracy", **detail)
    if loss <= MISTAKE_MAX:
        return _result("mistake", **detail)

    # Softening for positions that are still completely winning after the
    # move: dropping from "mate in 4" to "up a full queen" is a real
    # inaccuracy, but calling it a blunder while the player is still
    # crushing reads as noise rather than feedback.
    if played_cp >= 600:
        return _result("inaccuracy", **detail)
    return _result("blunder", **detail)
