#!/usr/bin/env python3
"""
Combined fix: (1) properly centers the board (fixed 260px equal side
columns instead of the 1fr columns that failed last time), and (2)
restyles/enlarges/differentiates the move-history panel per request:
  - slate-blue card palette matching .game-controls / .ai-explanation
    (rgba(30,41,59,0.95) + blur(20px) + 16px radius) instead of the old
    flat near-black eval-bar look
  - widened from 200px to 260px so it reads as a real panel, not a
    sliver, without eating the whole right side
  - white/black move pills reusing the .turn-badge.white/.black gradient
    styling so it's immediately obvious whose move is whose
  - current-move highlight switched to a box-shadow ring so it doesn't
    clobber the new pill colors

Run this from the repo root:
    python3 patch_moveshow2.py chess-frontend/src/components/ChessBoard.css
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
    print("Usage: python3 patch_moveshow2.py <path to ChessBoard.css>")
    sys.exit(1)

css_replacements = [
    (
'''.chess-container {
    display: flex;
    flex-direction: column;
    align-items: center;
    padding: 30px 20px;
    margin: 0 auto;
    gap: 24px;
    min-height: 100vh;
    max-width: 820px;
}''',
'''.chess-container {
    display: flex;
    flex-direction: column;
    align-items: center;
    padding: 30px 20px;
    margin: 0 auto;
    gap: 24px;
    min-height: 100vh;
    max-width: 1080px;
}''',
        "widen chess-container to fit the bigger move-history panel",
    ),
    (
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
}

.eval-bar {
    width: 26px;
    background: #0f0f1a;
    border: 1px solid rgba(148, 163, 184, 0.25);
    border-radius: 6px;
    overflow: hidden;
    display: flex;
    flex-direction: column-reverse;
    box-shadow: inset 0 1px 4px rgba(0, 0, 0, 0.5);
}

.eval-bar-fill {
    width: 100%;
    background: linear-gradient(180deg, #ffffff, #d8dee9);
    transition: height 0.5s ease;
}

.eval-bar-label {
    font-size: 0.7rem;
    font-weight: 700;
    color: #e2e8f0;
    background: rgba(15, 23, 42, 0.7);
    border: 1px solid rgba(148, 163, 184, 0.2);
    padding: 3px 8px;
    border-radius: 5px;
    min-width: 38px;
    text-align: center;
    font-variant-numeric: tabular-nums;
}

@media (max-width: 480px) {
    .eval-bar {
        width: 18px;
    }
    .eval-bar-label {
        font-size: 0.6rem;
        min-width: 30px;
        padding: 2px 6px;
    }
}

/* ===== Move history panel (v2) ===== */
.move-history-panel {
    display: flex;
    flex-direction: column;
    width: 200px;
    background: #0f0f1a;
    border: 1px solid rgba(148, 163, 184, 0.25);
    border-radius: 6px;
    box-shadow: inset 0 1px 4px rgba(0, 0, 0, 0.5);
    overflow: hidden;
    justify-self: start;
}

.move-history-title {
    margin: 0;
    padding: 8px 10px;
    font-size: 0.75rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: #94a3b8;
    border-bottom: 1px solid rgba(148, 163, 184, 0.2);
}

.move-history-list {
    overflow-y: auto;
    padding: 4px;
}

.move-history-row {
    display: grid;
    grid-template-columns: 26px 1fr 1fr;
    gap: 6px;
    padding: 3px 6px;
    border-radius: 4px;
    font-size: 0.85rem;
    font-family: 'Courier New', monospace;
    color: #e2e8f0;
}

.move-history-row:nth-child(odd) {
    background: rgba(148, 163, 184, 0.06);
}

.move-history-number {
    color: #64748b;
}

.move-history-black {
    color: #cbd5e1;
}

.move-history-current {
    background: #fbbf24;
    color: #0f0f1a;
    border-radius: 3px;
    padding: 0 4px;
    font-weight: 700;
}

.move-history-empty {
    padding: 12px;
    color: #64748b;
    font-style: italic;
    font-size: 0.85rem;
    text-align: center;
}

@media (max-width: 480px) {
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
    }
}''',
'''/* ===== Eval bar (v2) ===== */
/* Grid, not flex: the eval bar (~60px) and move history panel (260px) are
   different widths, so a flex row centered as a whole would leave the
   board itself off-center relative to the panels below it. `1fr` side
   columns aren't enough on their own either - without an explicit width
   on .board-area, grid tracks fall back to sizing around each column's
   own content minimum, so the 260px move-history column still ends up
   much wider than the ~60px eval-bar column. Fixed-width side columns
   (matching the wider panel) are the only way to force them equal
   regardless of content, keeping the board's center locked in place. */
.board-area {
    display: grid;
    grid-template-columns: 260px auto 260px;
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
}

.eval-bar {
    width: 26px;
    background: #0f0f1a;
    border: 1px solid rgba(148, 163, 184, 0.25);
    border-radius: 6px;
    overflow: hidden;
    display: flex;
    flex-direction: column-reverse;
    box-shadow: inset 0 1px 4px rgba(0, 0, 0, 0.5);
}

.eval-bar-fill {
    width: 100%;
    background: linear-gradient(180deg, #ffffff, #d8dee9);
    transition: height 0.5s ease;
}

.eval-bar-label {
    font-size: 0.7rem;
    font-weight: 700;
    color: #e2e8f0;
    background: rgba(15, 23, 42, 0.7);
    border: 1px solid rgba(148, 163, 184, 0.2);
    padding: 3px 8px;
    border-radius: 5px;
    min-width: 38px;
    text-align: center;
    font-variant-numeric: tabular-nums;
}

@media (max-width: 480px) {
    .eval-bar {
        width: 18px;
    }
    .eval-bar-label {
        font-size: 0.6rem;
        min-width: 30px;
        padding: 2px 6px;
    }
}

/* ===== Move history panel (v2) ===== */
/* Restyled to match the app's card design system (.game-controls /
   .ai-explanation): slate-blue translucent background, blurred backdrop,
   16px radius, matching border + two-layer shadow - instead of the old
   flat near-black eval-bar-style look. Widened to 260px so it reads as a
   real panel rather than a narrow sidebar, without eating the whole
   right side of the layout. */
.move-history-panel {
    display: flex;
    flex-direction: column;
    width: 260px;
    background: rgba(30, 41, 59, 0.95);
    backdrop-filter: blur(20px);
    border-radius: 16px;
    box-shadow:
        0 10px 25px -5px rgba(0, 0, 0, 0.4),
        0 10px 10px -5px rgba(0, 0, 0, 0.2);
    border: 1px solid rgba(71, 85, 105, 0.3);
    overflow: hidden;
    justify-self: start;
}

.move-history-title {
    margin: 0;
    padding: 12px 14px;
    font-size: 0.8rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: #93c5fd;
    text-align: center;
    border-bottom: 1px solid rgba(71, 85, 105, 0.3);
}

.move-history-list {
    overflow-y: auto;
    max-height: 480px;
    padding: 6px;
}

.move-history-row {
    display: grid;
    grid-template-columns: 30px 1fr 1fr;
    gap: 6px;
    align-items: center;
    padding: 4px 6px;
    border-radius: 4px;
    font-size: 0.85rem;
    font-family: 'Courier New', monospace;
    color: #e2e8f0;
}

.move-history-row:nth-child(odd) {
    background: rgba(71, 85, 105, 0.15);
}

.move-history-number {
    color: #94a3b8;
}

/* White/black move pills reuse the same gradients as .turn-badge.white
   and .turn-badge.black so the history panel visually matches the turn
   indicator elsewhere in the UI. */
.move-history-white {
    background: linear-gradient(135deg, #f1f5f9 0%, #e2e8f0 100%);
    color: #334155;
    border-radius: 4px;
    padding: 2px 6px;
    font-weight: 600;
    text-align: center;
}

.move-history-black {
    background: linear-gradient(135deg, #0f172a 0%, #1e293b 100%);
    color: #f1f5f9;
    border: 1px solid rgba(71, 85, 105, 0.4);
    border-radius: 4px;
    padding: 2px 6px;
    font-weight: 600;
    text-align: center;
}

/* Ring instead of a flat background override, so the current-move
   highlight doesn't clobber the new white/black pill colors. */
.move-history-current {
    box-shadow: 0 0 0 2px #fbbf24;
}

.move-history-empty {
    padding: 16px 12px;
    color: #64748b;
    font-style: italic;
    font-size: 0.85rem;
    text-align: center;
}

@media (max-width: 480px) {
    .board-area {
        grid-template-columns: 1fr;
        justify-items: center;
    }
    .eval-bar-wrapper {
        justify-self: center;
    }
    .move-history-panel {
        width: 100%;
        max-width: 480px;
        justify-self: center;
        order: 3;
    }
    .move-history-list {
        max-height: 320px;
    }
}''',
        "restyle/enlarge move-history panel + fix board centering with 260px columns",
    ),
]

patch_file(sys.argv[1], css_replacements)
print("🎉 All patches applied successfully.")
