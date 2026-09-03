#!/usr/bin/env bash
# Run this from the repo root (chess-frontend/ must exist as a sibling dir).
# Drops in the Stencil piece set + theme picker, defaulting the board to
# Stencil so the old react-chessboard default pieces are gone on load.
#
# Before running: make sure pieceThemes.tsx, patch_pieceui_tsx.py, and
# patch_pieceui_css.py (all three delivered alongside this script) are sitting
# in the SAME directory as this script.
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC_DIR="chess-frontend/src"
COMPONENTS_DIR="$SRC_DIR/components"

if [ ! -d "$COMPONENTS_DIR" ]; then
    echo "❌ Can't find $COMPONENTS_DIR - run this from your repo root."
    exit 1
fi

echo "📄 Placing pieceThemes.tsx..."
cp "$SCRIPT_DIR/pieceThemes.tsx" "$SRC_DIR/pieceThemes.tsx"

echo "🔧 Patching ChessBoard.tsx..."
cp "$SCRIPT_DIR/patch_pieceui_tsx.py" "$COMPONENTS_DIR/patch_pieceui_tsx.py"
(cd "$COMPONENTS_DIR" && python3 patch_pieceui_tsx.py)

echo "🎨 Patching ChessBoard.css..."
cp "$SCRIPT_DIR/patch_pieceui_css.py" "$COMPONENTS_DIR/patch_pieceui_css.py"
(cd "$COMPONENTS_DIR" && python3 patch_pieceui_css.py)

echo "🧹 Cleaning up patch scripts..."
rm "$COMPONENTS_DIR/patch_pieceui_tsx.py" "$COMPONENTS_DIR/patch_pieceui_css.py"

echo ""
echo "🎉 Done. Rebuild/restart your frontend to see it:"
echo "   docker-compose up --build"
echo "   (or, for local dev: cd chess-frontend && npm run dev)"
