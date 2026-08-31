#!/usr/bin/env python3
"""
Fixes a stale-closure race in the color-switch feature: clicking "Play as
Black" reported the AI's opening move as "completed" almost instantly,
without actually waiting for it, because startAiMovePolling() was called
synchronously right after setPlayerColorState() in the same function -
before React re-rendered - so the poller's `playerColor` closure was still
bound to the OLD color from the render that was executing. Since a fresh
reset always starts with White to move, the poller's first check ("is it
this stale old color's turn?") matched the just-reset starting position
trivially, well before the real AI move had even started thinking on the
backend. The real move landed later in the background with nobody polling
for it, so the board just sat there until a manual "Make AI Move" click
happened to re-sync.

Fix: startAiMovePolling now takes an explicit targetColor parameter, and
handleSetColor passes the new color directly (a local variable, not
dependent on React's state-update timing) instead of relying on the
`playerColor` closure.

Run this from the repo root:
    python3 patch_colorbug.py chess-frontend/src/components/ChessBoard.tsx
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
    print("Usage: python3 patch_colorbug.py <path to ChessBoard.tsx>")
    sys.exit(1)

replacements = [
    (
'''    const startAiMovePolling = useCallback(() => {
        console.log('🔄 [AI] Starting AI move polling...');
        let pollCount = 0;''',
'''    // targetColor lets a caller that just changed playerColor (e.g.
    // handleSetColor) tell the poller explicitly which color it's waiting
    // to see on the clock, instead of relying on the `playerColor` closure
    // - which, called synchronously right after setPlayerColorState(), is
    // still bound to the OLD color from this render and hasn't caught up
    // to the state update yet. Without this, switching to Black caused the
    // very first poll to compare the fresh board's "white to move" against
    // the stale old "white" playerColor, match instantly, and report the
    // AI's move as "completed" before it had even started thinking.
    const startAiMovePolling = useCallback((targetColor: 'white' | 'black' = playerColor) => {
        console.log(`🔄 [AI] Starting AI move polling (waiting for ${targetColor}'s turn)...`);
        let pollCount = 0;''',
        "make startAiMovePolling accept an explicit targetColor",
    ),
    (
'''                    // AI move is done once it's the human's turn again.
                    if (currentTurn === playerColor) {''',
'''                    // AI move is done once it's the target color's turn again.
                    if (currentTurn === targetColor) {''',
        "compare against targetColor instead of the stale playerColor closure",
    ),
    (
'''            if (result.ai_scheduled) {
                console.log('🎯 [COLOR] AI moves first - starting polling');
                setLangflowConfig(prev => ({ ...prev, status: 'thinking' }));
                setAiExplanation('AI is thinking...');
                startAiMovePolling();''',
'''            if (result.ai_scheduled) {
                console.log('🎯 [COLOR] AI moves first - starting polling');
                setLangflowConfig(prev => ({ ...prev, status: 'thinking' }));
                setAiExplanation('AI is thinking...');
                // Pass `color` explicitly rather than letting the poller
                // default to the `playerColor` closure - see the comment
                // on startAiMovePolling for why that closure is stale here.
                startAiMovePolling(color);''',
        "pass the new color explicitly from handleSetColor",
    ),
]

patch_file(sys.argv[1], replacements)
print("🎉 All patches applied successfully.")
