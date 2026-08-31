#!/usr/bin/env python3
"""
CSS side of the board/piece theme picker: adds a new 220px left column to
the .board-area grid (holding the theme picker + eval bar stacked), widens
.chess-container to fit it, and adds the .theme-picker-* card styles
(matching the existing .move-history-panel card look).

Run this from chess-frontend/src/components/:
    python3 patch_pieceui_css.py
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
'''.chess-container {
    display: flex;
    flex-direction: column;
    align-items: center;
    padding: 30px 20px;
    margin: 0 auto;
    gap: 24px;
    min-height: 100vh;
    max-width: 1320px;
}''',
        "widen .chess-container for the new theme picker column",
    ),
    (
'''.board-area {
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
}''',
'''.board-area {
    display: grid;
    grid-template-columns: 220px 260px auto 260px;
    align-items: flex-start;
    gap: 14px;
}

.left-panel-column {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 16px;
    justify-self: end;
}

.eval-bar-wrapper {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 6px;
    padding-top: 2px;
}''',
        "add the new left-panel-column grid track",
    ),
    (
'''.move-history-empty {
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
'''.move-history-empty {
    padding: 16px 12px;
    color: #64748b;
    font-style: italic;
    font-size: 0.85rem;
    text-align: center;
}

/* ===== Theme picker panel ===== */
/* Same card language as .move-history-panel (slate-blue translucent card,
   blurred backdrop, 16px radius, matching border + shadow) so it reads as
   part of the same design system rather than a bolted-on extra. */
.theme-picker-panel {
    display: flex;
    flex-direction: column;
    width: 220px;
    background: rgba(30, 41, 59, 0.95);
    backdrop-filter: blur(20px);
    border-radius: 16px;
    box-shadow:
        0 10px 25px -5px rgba(0, 0, 0, 0.4),
        0 10px 10px -5px rgba(0, 0, 0, 0.2);
    border: 1px solid rgba(71, 85, 105, 0.3);
    overflow: hidden;
}

.theme-picker-title {
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

.theme-picker-list {
    display: flex;
    flex-direction: column;
    gap: 8px;
    padding: 10px;
}

.theme-picker-option {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 8px 10px;
    border-radius: 10px;
    border: 1px solid rgba(71, 85, 105, 0.3);
    background: rgba(15, 23, 42, 0.6);
    color: #e2e8f0;
    font-size: 0.85rem;
    font-weight: 500;
    cursor: pointer;
    transition: all 0.2s ease;
    text-align: left;
}

.theme-picker-option:hover {
    border-color: rgba(59, 130, 246, 0.5);
    background: rgba(30, 58, 95, 0.5);
}

.theme-picker-option.active {
    border-color: #3b82f6;
    background: rgba(37, 99, 235, 0.25);
    box-shadow: 0 0 0 1px rgba(59, 130, 246, 0.4);
}

.theme-picker-swatch {
    width: 22px;
    height: 22px;
    border-radius: 6px;
    border: 1px solid rgba(148, 163, 184, 0.4);
    flex-shrink: 0;
}

@media (max-width: 480px) {
    .board-area {
        grid-template-columns: 1fr;
        justify-items: center;
    }
    .left-panel-column {
        justify-self: center;
        flex-direction: row;
        flex-wrap: wrap;
        justify-content: center;
        order: 0;
    }
    .theme-picker-panel {
        width: 100%;
        max-width: 320px;
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
        "add theme-picker-panel styles + its mobile layout rules",
    ),
]

if len(sys.argv) == 2:
    target = sys.argv[1]
else:
    target = "ChessBoard.css"

patch_file(target, replacements)
print("🎉 All patches applied successfully.")
