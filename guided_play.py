"""
guided_play.py

Guided Play for the real game: after the AI moves, the coach also says what
the player should LOOK AT before replying - not what to play.

Why this is a module and not three lines in the prompt
------------------------------------------------------
The "Watch out" section is the one place in Play where the model is asked to
talk about the player's side of the position, and a model asked to describe
threats will happily invent them. So the facts it reasons from are computed
here with python-chess for every shortlisted move BEFORE the model is asked:
does it give check, which enemy pieces it attacks, which of those are
undefended, and whether the moved piece itself lands loose. The model gets
those as text and is told they are the facts; it writes the prose. That is
the same division of labour the rest of the app runs on - engine decides,
Gemini explains - applied to the coaching sentence.

The product rule, which the prompt in gemini_move_service enforces and the
tests here hold to: Guided Play trains attention. It may say "check whether
f7 is still defended"; it must not say "play d3".

`split_watch_out` is the other half: the section comes back inside the same
reply as the move and its explanation (one Gemini call per move, by design -
no second request), so the caller needs to take it apart again to store the
section under its own key on the coach turn.
"""
from __future__ import annotations

import re

import chess

PIECE_NAMES = {
    chess.PAWN: "pawn", chess.KNIGHT: "knight", chess.BISHOP: "bishop",
    chess.ROOK: "rook", chess.QUEEN: "queen", chess.KING: "king",
}

# What candidate_facts answers when it cannot answer: a malformed or illegal
# move, or an unparseable FEN. Never an exception - the shortlist is engine-
# validated, but a grounding helper that can take the AI move down with it is
# strictly worse than one that says nothing.
EMPTY_FACTS = {"gives_check": False, "attacks": [], "hanging": [], "mover_loose": False}

# "Watch out:" / "**Watch out** -" / "WATCH OUT —", at the start of a line.
_WATCH_OUT = re.compile(
    r"^\s*\**\s*watch\s+out\s*\**\s*[:\-–—]?\s*\**\s*",
    re.IGNORECASE,
)


def _describe(board: chess.Board, square: int) -> str:
    piece = board.piece_at(square)
    name = PIECE_NAMES.get(piece.piece_type, "piece") if piece else "piece"
    return f"{name} on {chess.square_name(square)}"


def candidate_facts(fen: str, uci: str) -> dict:
    """
    What one move does, from the board rather than from the model.

    After `uci` is played from `fen`:
      gives_check  - the opponent's king is in check.
      attacks      - enemy pieces the moved piece now attacks (king excluded;
                     that is `gives_check`).
      hanging      - the subset of `attacks` with no defender at all.
      mover_loose  - the moved piece is attacked and has no defender.
    """
    try:
        board = chess.Board(fen)
        move = chess.Move.from_uci(uci)
        if move not in board.legal_moves:
            return dict(EMPTY_FACTS, attacks=[], hanging=[])
        mover = board.turn
        board.push(move)
    except Exception:
        return dict(EMPTY_FACTS, attacks=[], hanging=[])
    enemy = not mover
    landed = move.to_square
    attacks, hanging = [], []
    for target in board.attacks(landed):
        piece = board.piece_at(target)
        if piece is None or piece.color != enemy or piece.piece_type == chess.KING:
            continue
        label = _describe(board, target)
        attacks.append(label)
        if not board.is_attacked_by(enemy, target):
            hanging.append(label)
    mover_loose = board.is_attacked_by(enemy, landed) and not board.is_attacked_by(mover, landed)
    return {
        "gives_check": board.is_check(),
        "attacks": attacks,
        "hanging": hanging,
        "mover_loose": mover_loose,
    }


def describe_candidates(fen: str, candidates: list) -> str:
    """One line of board facts per shortlisted move, for the prompt."""
    lines = []
    for c in candidates:
        uci = c.get("move", "")
        facts = candidate_facts(fen, uci)
        parts = []
        if facts["gives_check"]:
            parts.append("gives check")
        if facts["attacks"]:
            parts.append("attacks " + ", ".join(facts["attacks"]))
        if facts["hanging"]:
            parts.append("leaves undefended: " + ", ".join(facts["hanging"]))
        if facts["mover_loose"]:
            parts.append("the moved piece lands undefended and can be captured")
        summary = "; ".join(parts) if parts else "no check, attacks nothing new"
        lines.append(f"- {uci}: {summary}")
    return "\n".join(lines)


def split_watch_out(text: str) -> tuple:
    """
    Separate the explanation from its "Watch out" section.

    Returns (body, watch_out). `watch_out` is None when the reply carries no
    section - which is every reply in standard mode, and the graceful case in
    guided mode when the model ignored the format.
    """
    if not text:
        return "", None
    lines = text.strip().splitlines()
    for i, line in enumerate(lines):
        m = _WATCH_OUT.match(line)
        if m:
            first = line[m.end():].strip().rstrip("*").strip()
            rest = [first] + [ln.strip() for ln in lines[i + 1:]]
            watch = " ".join(ln for ln in rest if ln).strip()
            body = "\n".join(lines[:i]).strip()
            return body, (watch or None)
    return text.strip(), None
