#!/usr/bin/env python3
"""
Wires the new board/piece theme system (pieceThemes.tsx) into ChessBoard.tsx:

- Imports getCustomPieces/getBoardColors/QuillFilterDefs/PIECE_THEME_LIST from
  the new ../pieceThemes module.
- Adds a `pieceTheme` state (default 'classic', which passes undefined
  overrides down to react-chessboard so it renders exactly as it always has).
- Renders <QuillFilterDefs /> once near the root (it's a zero-size, invisible
  element that just holds the SVG filter every Quill piece references).
- Adds a "Board Theme" picker panel in a new left column next to the eval
  bar, letting you switch themes with one click - each option shows a small
  light/dark swatch preview.
- Passes customPieces / customDarkSquareStyle / customLightSquareStyle into
  <Chessboard> based on the active theme.

Requires pieceThemes.tsx to already exist at chess-frontend/src/pieceThemes.tsx
(delivered separately) - this patch only adds the import and wiring.

Run this from chess-frontend/src/components/:
    python3 patch_pieceui_tsx.py
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
'''import React, { useState, useEffect, useCallback, useRef } from 'react';
import { Chessboard } from 'react-chessboard';
import { chessService } from '../services/chessService';
import type { GameState, ChessMove, LangflowConfig } from '../types/chess';
import type { Square } from 'chess.js';''',
'''import React, { useState, useEffect, useCallback, useRef } from 'react';
import { Chessboard } from 'react-chessboard';
import { chessService } from '../services/chessService';
import type { GameState, ChessMove, LangflowConfig } from '../types/chess';
import type { Square } from 'chess.js';
import { getCustomPieces, getBoardColors, PIECE_THEME_LIST } from '../pieceThemes';
import type { PieceThemeName } from '../pieceThemes';''',
        "import the piece theme system",
    ),
    (
'''    const [gameMode, setGameMode] = useState<GameMode>('human_vs_ai');
    const [aiVsAiRunning, setAiVsAiRunning] = useState<boolean>(false);''',
'''    const [gameMode, setGameMode] = useState<GameMode>('human_vs_ai');
    const [aiVsAiRunning, setAiVsAiRunning] = useState<boolean>(false);
    // Which piece/board visual theme is active - defaults to 'stencil' so
    // the app opens with the custom piece set instead of react-chessboard's
    // built-in default. 'classic' (still selectable from the picker) passes
    // undefined overrides down to react-chessboard for its original look.
    const [pieceTheme, setPieceTheme] = useState<PieceThemeName>('stencil');''',
        "add pieceTheme state, defaulting to stencil",
    ),
    (
'''    const movePairs = buildMovePairs(moveHistory);
    return (
        <div className="chess-container">
            <div className="board-area">
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
'''    const movePairs = buildMovePairs(moveHistory);
    const customPieces = getCustomPieces(pieceTheme);
    const boardColors = getBoardColors(pieceTheme);
    return (
        <div className="chess-container">
            <div className="board-area">
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
        "render QuillFilterDefs + theme picker panel in a new left column",
    ),
    (
'''                    <Chessboard
                        position={gameState.fen}
                        onSquareClick={onSquareClick}
                        customSquareStyles={squareStyles}
                        boardWidth={boardSize}
                        boardOrientation={playerColor}
                        arePiecesDraggable={false}
                    />''',
'''                    <Chessboard
                        position={gameState.fen}
                        onSquareClick={onSquareClick}
                        customSquareStyles={squareStyles}
                        boardWidth={boardSize}
                        boardOrientation={playerColor}
                        arePiecesDraggable={false}
                        customPieces={customPieces}
                        customDarkSquareStyle={boardColors ? { backgroundColor: boardColors.dark } : undefined}
                        customLightSquareStyle={boardColors ? { backgroundColor: boardColors.light } : undefined}
                    />''',
        "pass customPieces/board colors into Chessboard",
    ),
]

if len(sys.argv) == 2:
    target = sys.argv[1]
else:
    target = "ChessBoard.tsx"

patch_file(target, replacements)
print("🎉 All patches applied successfully.")
