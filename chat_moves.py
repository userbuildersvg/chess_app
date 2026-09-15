"""
"Play Nf3." / "Make the best move." - an explicit request in the Learn chat
to put a move on the board, handled without the model.

    text -> parse_request() -> {"kind": "best"} | {"kind": "move", "text": "Nf3"} | None
         -> resolve_move(board, text) -> (chess.Move | None, refusal sentence)

The model never decides legality. Everything here is python-chess against
the board the session is actually on: SAN (with its own ambiguity errors),
UCI, and "knight to f3" / "my rook to d1" resolved through the legal-move
list. Anything not an explicit instruction returns None and goes to the
coach as an ordinary question. Only sandbox_api wires this in - Play keeps
its own moves, and Review branches through its explore flow.
"""
from __future__ import annotations

import re
from typing import Optional

import chess

SAN_RE = r"(?:O-O(?:-O)?|0-0(?:-0)?|[KQRBN][a-h]?[1-8]?x?[a-h][1-8](?:=[QRBN])?|[a-h](?:x[a-h])?[1-8](?:=[QRBN])?)[+#]?"
UCI_RE = r"[a-h][1-8][a-h][1-8][qrbn]?"
PIECES = {"king": chess.KING, "queen": chess.QUEEN, "rook": chess.ROOK, "bishop": chess.BISHOP,
          "knight": chess.KNIGHT, "pawn": chess.PAWN}
_VERB = r"(?:play|make|apply|put|do|execute|go with|move|push|castle)"
_BEST = re.compile(
    rf"\b{_VERB}\b[^.?!]*\b(?:best|engine|top|strongest|recommended)\b[^.?!]*\bmove\b"
    rf"|\b(?:best|engine|top|strongest)\s+move\b[^.?!]*\b(?:on the board|for me|play it|make it|apply it)\b"
    rf"|\bmake it on the board\b|\bplay it\b(?:\s+on the board)?",
    re.IGNORECASE)
# SAN is case-sensitive on purpose (b4 is a pawn, B4 a bishop); the verb is not.
_MOVE = re.compile(rf"(?i:\b{_VERB}\b\s+(?:the\s+move\s+)?)(?:(?P<san>{SAN_RE})|(?P<uci>(?i:{UCI_RE})))(?=$|[\s.,!?])")
_PIECE_TO = re.compile(
    rf"\b(?:{_VERB}\b\s+)?(?:my|the|your|a)?\s*(?P<piece>king|queen|rook|bishop|knight|pawn)\s+(?:to|->)\s+(?P<sq>[a-h][1-8])\b",
    re.IGNORECASE)
_CASTLE = re.compile(r"\bcastle\b(?:\s+(?P<side>king|queen|short|long)(?:\s*side)?)?", re.IGNORECASE)


def parse_request(text: str) -> Optional[dict]:
    """An explicit instruction to move, or None."""
    t = (text or "").strip()
    if not t:
        return None
    if _BEST.search(t):
        return {"kind": "best"}
    m = _MOVE.search(t)
    if m:
        return {"kind": "move", "text": m.group("san") or m.group("uci")}
    m = _PIECE_TO.search(t)
    if m:
        return {"kind": "move", "text": f"{m.group('piece').lower()} to {m.group('sq').lower()}"}
    m = _CASTLE.search(t)
    if m and re.search(rf"\b{_VERB}\b|\bcastle\b", t, re.IGNORECASE):
        side = (m.group("side") or "").lower()
        return {"kind": "move", "text": "O-O-O" if side in ("queen", "long") else "O-O"}
    return None


def resolve_move(board: chess.Board, text: str) -> tuple[Optional[chess.Move], Optional[str]]:
    """A legal move for `text` on `board`, or (None, why not)."""
    raw = (text or "").strip().replace("0-0-0", "O-O-O").replace("0-0", "O-O")
    if not raw:
        return None, "Tell me which move to play - Nf3, e2e4, or 'knight to f3'."
    # "knight to f3"
    m = re.fullmatch(r"(king|queen|rook|bishop|knight|pawn)\s+to\s+([a-h][1-8])", raw, re.IGNORECASE)
    if m:
        piece, target = PIECES[m.group(1).lower()], chess.parse_square(m.group(2).lower())
        options = [mv for mv in board.legal_moves
                   if mv.to_square == target and board.piece_type_at(mv.from_square) == piece
                   and (piece != chess.PAWN or mv.promotion in (None, chess.QUEEN))]
        if len(options) == 1:
            return options[0], None
        if not options:
            return None, f"No {m.group(1).lower()} can legally move to {m.group(2).lower()} here."
        sans = ", ".join(board.san(mv) for mv in options)
        return None, f"More than one {m.group(1).lower()} can go to {m.group(2).lower()} here - say which: {sans}."
    # UCI
    if re.fullmatch(UCI_RE, raw, re.IGNORECASE):
        mv = chess.Move.from_uci(raw.lower())
        if mv in board.legal_moves:
            return mv, None
        return None, f"{raw} is not legal in this position."
    # SAN, with python-chess's own ambiguity and legality verdicts
    try:
        mv = board.parse_san(raw)
    except chess.AmbiguousMoveError:
        candidates = [board.san(x) for x in board.legal_moves if _same_piece_target(board, x, raw)]
        return None, f"{raw} is ambiguous here - say which: {', '.join(candidates) or 'add the file or rank'}."
    except chess.IllegalMoveError:
        return None, f"{raw} is not legal in this position."
    except chess.InvalidMoveError:
        return None, f"I don't recognise '{raw}' as a move. Try Nf3, e2e4, or 'knight to f3'."
    return mv, None


def _same_piece_target(board: chess.Board, mv: chess.Move, san: str) -> bool:
    core = san.rstrip("+#")
    target = core[-2:] if core[-1].isdigit() else None
    if not target:
        return False
    piece = {"K": chess.KING, "Q": chess.QUEEN, "R": chess.ROOK, "B": chess.BISHOP, "N": chess.KNIGHT}.get(core[0], chess.PAWN)
    return chess.square_name(mv.to_square) == target and board.piece_type_at(mv.from_square) == piece


def confirm_text(san: str, best: bool, note: Optional[str] = None) -> str:
    lead = f"I played the engine's preferred move, {san}, on the board." if best else f"I played {san} on the board."
    return f"{lead} {note}".strip() if note else lead
