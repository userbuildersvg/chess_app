#!/usr/bin/env python3
"""
Fixes the eval bar / control panel layout: the theme picker and eval bar
were nested together inside one .left-panel-column div, stacked vertically
(picker on top, eval bar below it). That made that column's TOTAL height
(picker + eval bar, ~200px + ~480px) taller than the board/move-history row,
so the grid row stretched to fit it - pushing "Chess Game Control" further
down the page than it should sit, and putting the eval bar underneath the
theme picker instead of beside the board.

Fix: theme picker and eval bar become separate grid columns again (picker |
eval bar | board | move history, 4 columns instead of the picker+eval-bar
sharing one), so the eval bar sits directly next to the board at full board
height like before the picker was added, and the row height goes back to
being set by the board/eval-bar/move-history, not an inflated stacked column.

Run this from chess-frontend/src/components/:
    python3 patch_fix_layout.py
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


tsx_replacements = [
    (
'''            <div className="board-area">
                <div className="left-panel-column">
                    <div className="theme-picker-panel">
                        <h3 className="theme-picker-title">🎨 Board Theme</h3>
                        <div className="theme-picker-list">
                            {PIECE_THEME_LIST.map(theme => {
                                const swatchColors = getBoardColors(theme.id);
                                return (
                                    <button
                                        key={theme.id}
                                        onClick={() => setPieceTheme(theme.id)}
                                        className={`theme-picker-option ${pieceTheme === theme.id ? 'active' : ''}`}
                                    >
                                        <span
                                            className="theme-picker-swatch"
                                            style={{
                                                background: swatchColors
                                                    ? `linear-gradient(135deg, ${swatchColors.light} 50%, ${swatchColors.dark} 50%)`
                                                    : 'linear-gradient(135deg, #f0d9b5 50%, #b58863 50%)'
                                            }}
                                        />
                                        {theme.label}
                                    </button>
                                );
                            })}
                        </div>
                    </div>
                <div className="eval-bar-wrapper">
                    <div className="eval-bar" style={{ height: boardSize }} title="Position evaluation (White's perspective)">
                        <div
                            className="eval-bar-fill"
                            style={{ height: `${evalToWhitePercent(boardEval)}%` }}
                        />
                    </div>
                    <span className="eval-bar-label">{formatEval(boardEval)}</span>
                </div>
                </div>
                <div className={`chess-board-wrapper ${langflowConfig.status === 'thinking' ? 'ai-thinking' : ''}`}>''',
'''            <div className="board-area">
                <div className="theme-picker-panel">
                    <h3 className="theme-picker-title">🎨 Board Theme</h3>
                    <div className="theme-picker-list">
                        {PIECE_THEME_LIST.map(theme => {
                            const swatchColors = getBoardColors(theme.id);
                            return (
                                <button
                                    key={theme.id}
                                    onClick={() => setPieceTheme(theme.id)}
                                    className={`theme-picker-option ${pieceTheme === theme.id ? 'active' : ''}`}
                                >
                                    <span
                                        className="theme-picker-swatch"
                                        style={{
                                            background: swatchColors
                                                ? `linear-gradient(135deg, ${swatchColors.light} 50%, ${swatchColors.dark} 50%)`
                                                : 'linear-gradient(135deg, #f0d9b5 50%, #b58863 50%)'
                                        }}
                                    />
                                    {theme.label}
                                </button>
                            );
                        })}
                    </div>
                </div>
                <div className="eval-bar-wrapper">
                    <div className="eval-bar" style={{ height: boardSize }} title="Position evaluation (White's perspective)">
                        <div
                            className="eval-bar-fill"
                            style={{ height: `${evalToWhitePercent(boardEval)}%` }}
                        />
                    </div>
                    <span className="eval-bar-label">{formatEval(boardEval)}</span>
                </div>
                <div className={`chess-board-wrapper ${langflowConfig.status === 'thinking' ? 'ai-thinking' : ''}`}>''',
        "split theme picker and eval bar into separate grid columns",
    ),
]

css_replacements = [
    (
'''.board-area {
    display: grid;
    grid-template-columns: 220px auto 260px;
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
'''.board-area {
    display: grid;
    grid-template-columns: 200px 70px auto 260px;
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
        "give theme picker and eval bar their own grid columns",
    ),
    (
'''.theme-picker-panel {
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
}''',
'''.theme-picker-panel {
    display: flex;
    flex-direction: column;
    width: 200px;
    background: rgba(30, 41, 59, 0.95);
    backdrop-filter: blur(20px);
    border-radius: 16px;
    box-shadow:
        0 10px 25px -5px rgba(0, 0, 0, 0.4),
        0 10px 10px -5px rgba(0, 0, 0, 0.2);
    border: 1px solid rgba(71, 85, 105, 0.3);
    overflow: hidden;
    justify-self: end;
}''',
        "resize theme picker to match its new column + align to the board",
    ),
    (
'''    .left-panel-column {
        justify-self: center;
        flex-direction: row;
        flex-wrap: wrap;
        justify-content: center;
        order: 0;
    }
    .theme-picker-panel {
        width: 100%;
        max-width: 320px;
    }''',
'''    .theme-picker-panel {
        justify-self: center;
        width: 100%;
        max-width: 320px;
        order: 0;
    }
    .eval-bar-wrapper {
        justify-self: center;
        order: 0;
    }''',
        "fix the mobile stacking rules for the split columns",
    ),
]

patch_file("ChessBoard.tsx", tsx_replacements)
patch_file("ChessBoard.css", css_replacements)
print("🎉 All patches applied successfully.")
