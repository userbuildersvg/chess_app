"""
"Play Nf3." / "Make the best move." - an explicit request in the Learn chat
to put a move on the board, handled without the model.

    text -> parse_request() -> {"kind": "best"} | {"kind": "move", "text": "Nf3"}
                             | {"kind": "referenced"} | {"kind": "capture", ...}
                             | {"kind": "promote", "piece": chess.QUEEN} | None
         -> resolve_move(board, text) -> (chess.Move | None, refusal sentence)
         -> referenced_move(board, history), resolve_capture(board, spec), resolve_promotion(board, piece)

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
    rf"\b{_VERB}\b[^.?!]*\b(?:best|engine|top|strongest)\b[^.?!]*\bmove\b"
    rf"|\b(?:best|engine|top|strongest)\s+move\b[^.?!]*\b(?:on the board|for me|play it|make it|apply it)\b",
    re.IGNORECASE)
# "Make that move" / "play it" / "go with your suggestion": the move the coach
# just named. Resolved against the transcript in referenced_move().
_REFERENCED = re.compile(
    rf"\b(?:play|make|do|apply|execute|go with|use)\b\s+(?:that|it|this|those|that one|this one|"
    rf"(?:the|your)\s+(?:move|one|suggestion|recommendation|idea)(?:\s+you\s+(?:suggested|recommended|mentioned|said|gave))?|"
    rf"the (?:recommended|suggested) move)\b(?:\s+(?:move|one))?",
    re.IGNORECASE)
_CAPTURE = re.compile(r"\b(?:take|takes|capture|captures|grab|grabs|win|wins)\b", re.IGNORECASE)
_PIECE_WORD = r"(?:king|queen|rook|bishop|knight|pawn)"
_PROMOTE = re.compile(rf"\bpromot(?:e|ion)\b.*?\b(?P<piece>queen|rook|bishop|knight)\b", re.IGNORECASE)
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
    m = _PROMOTE.search(t)
    if m and re.search(rf"\b{_VERB}\b|\bpromote\b", t, re.IGNORECASE):
        return {"kind": "promote", "piece": PIECES[m.group("piece").lower()]}
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
    # "Is it good to play it?" is a question; "What is best? Play it." is an
    # instruction in its second sentence. Judged per sentence.
    m = _CAPTURE.search(t)
    if m and _imperative(_sentence_of(t, m.start())):
        spec = _capture_spec(_sentence_of(t, m.start()))
        if spec:
            return {"kind": "capture", **spec}
    m = _REFERENCED.search(t)
    if m and _imperative(_sentence_of(t, m.start())):
        return {"kind": "referenced"}
    return None


def _sentence_of(t: str, pos: int) -> str:
    start = max(t.rfind(ch, 0, pos) for ch in ".?!") + 1
    ends = [i for i in (t.find(ch, pos) for ch in ".?!") if i != -1]
    return t[start:min(ends) + 1 if ends else len(t)].strip()


def _imperative(sentence: str) -> bool:
    return "?" not in sentence and not re.match(
        r"(?i)(?:is|was|should|would|could|can|what|why|how|do|does|did|if|when)\b", sentence)


def _capture_spec(t: str) -> Optional[dict]:
    """"take on e5 with the bishop" / "bishop takes knight" -> {square, piece, victim}."""
    sq = re.search(r"\b(?:on|at)\s+(?P<sq>[a-h][1-8])\b", t, re.IGNORECASE)
    if sq is None:
        sq = re.search(rf"\b(?:take|takes|capture|captures|grab|grabs)\s+(?:the\s+)?(?:{_PIECE_WORD}\s+on\s+)?(?P<sq>[a-h][1-8])\b", t, re.IGNORECASE)
    with_ = re.search(rf"\bwith\s+(?:the|my|a)?\s*(?P<piece>{_PIECE_WORD})\b", t, re.IGNORECASE)
    mover = with_ or re.search(rf"\b(?P<piece>{_PIECE_WORD})\s+(?:takes|captures|grabs|wins)\b", t, re.IGNORECASE)
    victim = re.search(rf"\b(?:take|takes|capture|captures|grab|grabs|win|wins)\s+(?:the|his|her|their|that)?\s*(?P<victim>{_PIECE_WORD})\b", t, re.IGNORECASE)
    spec = {
        "square": sq.group("sq").lower() if sq else None,
        "piece": mover.group("piece").lower() if mover else None,
        "victim": victim.group("victim").lower() if victim else None,
    }
    return spec if any(spec.values()) else None


def resolve_capture(board: chess.Board, spec: dict) -> tuple[Optional[chess.Move], Optional[str]]:
    """The one legal capture matching {square, piece, victim}, or why not."""
    options = [mv for mv in board.legal_moves if board.is_capture(mv)]
    if spec.get("square"):
        options = [mv for mv in options if mv.to_square == chess.parse_square(spec["square"])]
    if spec.get("piece"):
        options = [mv for mv in options if board.piece_type_at(mv.from_square) == PIECES[spec["piece"]]]
    if spec.get("victim"):
        options = [mv for mv in options if board.piece_type_at(mv.to_square) == PIECES[spec["victim"]]]
    options = _one_per_promotion(options)
    if len(options) == 1:
        return options[0], None
    what = " ".join(p for p in (spec.get("piece") and f"with the {spec['piece']}", spec.get("square") and f"on {spec['square']}",
                                spec.get("victim") and f"of the {spec['victim']}") if p)
    if not options:
        return None, f"There is no legal capture {what} in this position.".replace("  ", " ")
    return None, f"More than one capture fits - say which: {', '.join(board.san(mv) for mv in options)}."


def resolve_promotion(board: chess.Board, piece: int) -> tuple[Optional[chess.Move], Optional[str]]:
    options = [mv for mv in board.legal_moves if mv.promotion == piece]
    if len(options) == 1:
        return options[0], None
    if not options:
        return None, "No pawn can promote in this position."
    return None, f"More than one pawn can promote - say which: {', '.join(board.san(mv) for mv in options)}."


def _one_per_promotion(moves: list) -> list:
    """A promoting capture is four legal moves; count it once (as a queen) for ambiguity."""
    return [mv for mv in moves if mv.promotion in (None, chess.QUEEN)]


def referenced_move(board: chess.Board, history: list) -> tuple[Optional[chess.Move], Optional[str]]:
    """
    "Make that move": the legal move the coach's last reply named. One -> play
    it; several -> ask which; none -> (None, None), and the caller falls back
    to the engine's best, which is the only "it" left to mean.
    """
    last = next((t.get("text", "") for t in reversed(history or []) if t.get("role") == "model"), "")
    found: list[chess.Move] = []
    for token in re.findall(rf"(?<![A-Za-z0-9=]){SAN_RE}(?![A-Za-z0-9])", last):
        try:
            mv = board.parse_san(token.replace("0-0-0", "O-O-O").replace("0-0", "O-O"))
        except ValueError:
            continue
        if mv not in found:
            found.append(mv)
    if len(found) == 1:
        return found[0], None
    if len(found) > 1:
        return None, f"I mentioned more than one move - which do you mean: {', '.join(board.san(mv) for mv in found)}?"
    return None, None


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
                   if mv.to_square == target and board.piece_type_at(mv.from_square) == piece]
        if options and all(mv.promotion for mv in options) and len({mv.from_square for mv in options}) == 1:
            return None, _PROMOTION_ASK
        options = _one_per_promotion(options)
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
        if mv.promotion is None and chess.Move(mv.from_square, mv.to_square, chess.QUEEN) in board.legal_moves:
            return None, _PROMOTION_ASK
        return None, f"{raw} is not legal in this position."
    # SAN, with python-chess's own ambiguity and legality verdicts
    try:
        mv = board.parse_san(raw)
    except chess.AmbiguousMoveError:
        candidates = [board.san(x) for x in board.legal_moves if _same_piece_target(board, x, raw)]
        return None, f"{raw} is ambiguous here - say which: {', '.join(candidates) or 'add the file or rank'}."
    except chess.IllegalMoveError:
        if "=" not in raw and _needs_promotion(board, raw):
            return None, _PROMOTION_ASK
        return None, f"{raw} is not legal in this position."
    except chess.InvalidMoveError:
        return None, f"I don't recognise '{raw}' as a move. Try Nf3, e2e4, or 'knight to f3'."
    return mv, None


_PROMOTION_ASK = "That pawn promotes - which piece? Say e8=Q, or 'promote to queen' (or rook, bishop, knight)."


def _needs_promotion(board: chess.Board, san: str) -> bool:
    """A pawn SAN with no piece named that would be legal as a queen promotion."""
    try:
        board.parse_san(san.rstrip("+#") + "=Q")
        return True
    except ValueError:
        return False


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
