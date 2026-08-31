#!/usr/bin/env python3
"""
Fixes a real layout bug from the theme-picker patch: .board-area's grid was
left declaring 4 column tracks (220px 260px auto 260px) after the eval bar
got nested inside the new .left-panel-column instead of staying a direct
grid child - so there are only 3 actual grid items now (left-panel-column,
chess-board-wrapper, move-history-panel), not 4. With 4 tracks declared,
everything after the first column shifted into the wrong track: the board
got squeezed into a 260px column meant for nothing, and move-history-panel
landed where the board should be - overlapping it.

Fix: 3 tracks, matching the 3 real grid children.

Run this from chess-frontend/src/components/:
    python3 patch_fix_gridbug.py
"""
import sys


def patch_file(path, replacements):
    with open(path, "r", encoding="utf-8") as f:
        src = f.read()

    for old, new, label in replacements:
        count = src.count(old)
        if count != 1:
            print(f"❌ {path}: expected exactly 1 match for '{label}', found {count}. Aborting - no changes written.")
            sys.exit(1)
        src = src.replace(old, new, 1)

    with open(path, "w", encoding="utf-8") as f:
        f.write(src)
    print(f"✅ {path}: applied {len(replacements)} patch(es)")


replacements = [
    (
'''.board-area {
    display: grid;
    grid-template-columns: 220px 260px auto 260px;
    align-items: flex-start;
    gap: 14px;
}''',
'''.board-area {
    display: grid;
    grid-template-columns: 220px auto 260px;
    align-items: flex-start;
    gap: 14px;
}''',
        "fix .board-area to declare 3 grid tracks, not 4",
    ),
]

if len(sys.argv) == 2:
    target = sys.argv[1]
else:
    target = "ChessBoard.css"

patch_file(target, replacements)
print("🎉 Fixed.")
