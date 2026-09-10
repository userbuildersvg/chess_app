"""
Turning PGN text into something python-chess will read correctly.

One function, and it exists because of a bug that cost a P2 in the playtest
(§16): python-chess handed `1. ♘f3` does not raise. It drops the symbol it
cannot read, parses the rest as the PAWN move `f3`, and leaves `game.errors`
EMPTY - so a file that imported "cleanly" was a different game from the one in
the file, and the validation written precisely to refuse a game it could not
replay exactly could not see it.

This lived in `postmortem_state.py`, which is the right place for it while
Post-Mortem's importer was the only door a PGN came through. Learn now takes a
pasted PGN too (§27), and it needed the same treatment - so rather than have
Review handle figurines and Learn not, or have Learn import a Review module for
one helper, both import this.

`postmortem_state._defigurine` is kept as an alias so existing callers and the
tests that name it directly go on working.
"""
from __future__ import annotations

# Both colours of each piece map to the same letter: SAN does not distinguish
# them - the side to move already does - and a PGN exported with black figurines
# for Black's moves is common. The pawn symbols map to the EMPTY string, because
# SAN writes a pawn move with no piece letter at all; mapping them to "P" would
# turn "♟e5" into "Pe5", which python-chess rejects rather than reads.
FIGURINE = {
    "♔": "K", "♚": "K",
    "♕": "Q", "♛": "Q",
    "♖": "R", "♜": "R",
    "♗": "B", "♝": "B",
    "♘": "N", "♞": "N",
    "♙": "",  "♟": "",
}


def has_figurine(text: str) -> bool:
    """Whether `text` contains any figurine piece symbol at all."""
    return any(ch in text for ch in FIGURINE)


def defigurine(text: str) -> str:
    """
    Rewrite figurine movetext into letters, leaving header lines alone.

    Headers are skipped because a tag value is free text - a player really can
    be called "♕ Queen" - and rewriting it would corrupt a name rather than a
    move.
    """
    if not has_figurine(text):
        return text
    out = []
    for line in text.splitlines(keepends=True):
        if line.lstrip().startswith("["):
            out.append(line)
            continue
        out.append("".join(FIGURINE.get(ch, ch) for ch in line))
    return "".join(out)
