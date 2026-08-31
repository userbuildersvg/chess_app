#!/usr/bin/env python3
"""
Fixes the board being off-center relative to the panels below it (AI
difficulty, turn indicator, AI Analysis). Root cause: .board-area was a
flex row with justify-content:center, centering the ROW AS A WHOLE - but
the eval bar (~60px) and move-history panel (200px) are different widths,
so the extra ~140px on the right side visually pulled the board itself
left of where the difficulty/turn/analysis panels (which center on their
own axis below) sit.

Fix: switch .board-area to a 3-column grid with equal 1fr side columns and
an auto-sized middle column for the board. Equal side columns keep the
board's center fixed regardless of how wide its flanking panels are;
justify-self end/start on the eval bar and move history panel keeps them
hugging the board rather than floating in the middle of their column.

Run this from the repo root:
    python3 patch_boardcenter.py chess-frontend/src/components/ChessBoard.css
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


if len(sys.argv) != 2:
    print("Usage: python3 patch_boardcenter.py <path to ChessBoard.css>")
    sys.exit(1)

css_replacements = [
    (
'''/* ===== Eval bar (v2) ===== */
.board-area {
    display: flex;
    align-items: flex-start;
    justify-content: center;
    gap: 14px;
}

.eval-bar-wrapper {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 6px;
    padding-top: 2px;
}''',
'''/* ===== Eval bar (v2) ===== */
/* Grid, not flex: the eval bar (~60px) and move history panel (200px) are
   different widths, so a flex row centered as a whole would leave the
   board itself off-center relative to the panels below it. Forcing both
   outer columns to the same 1fr keeps the board's center fixed no matter
   how wide its flanking panels are. */
.board-area {
    display: grid;
    grid-template-columns: 1fr auto 1fr;
    align-items: flex-start;
    gap: 14px;
}

.eval-bar-wrapper {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 6px;
    padding-top: 2px;
    justify-self: end;
}''',
        "switch .board-area to a centered 3-column grid",
    ),
    (
'''.move-history-panel {
    display: flex;
    flex-direction: column;
    width: 200px;
    background: #0f0f1a;
    border: 1px solid rgba(148, 163, 184, 0.25);
    border-radius: 6px;
    box-shadow: inset 0 1px 4px rgba(0, 0, 0, 0.5);
    overflow: hidden;
}''',
'''.move-history-panel {
    display: flex;
    flex-direction: column;
    width: 200px;
    background: #0f0f1a;
    border: 1px solid rgba(148, 163, 184, 0.25);
    border-radius: 6px;
    box-shadow: inset 0 1px 4px rgba(0, 0, 0, 0.5);
    overflow: hidden;
    justify-self: start;
}''',
        "hug move-history-panel to the board's right edge",
    ),
    (
'''@media (max-width: 480px) {
    .board-area {
        flex-wrap: wrap;
    }
    .move-history-panel {
        width: 100%;
        order: 3;
    }''',
'''@media (max-width: 480px) {
    .board-area {
        grid-template-columns: 1fr;
        justify-items: center;
    }
    .eval-bar-wrapper {
        justify-self: center;
    }
    .move-history-panel {
        width: 100%;
        justify-self: center;
        order: 3;
    }''',
        "stack board-area into a single column on mobile",
    ),
]

patch_file(sys.argv[1], css_replacements)
print("🎉 All patches applied successfully.")
