#!/usr/bin/env python3
"""
Fixes the intermittent "AI error / not its turn, have to refresh to see the
move" bug. Root cause: Gemini/Langflow calls have been observed taking
20-25+ seconds, dangerously close to the frontend's 30-second AI-move
polling window. When a call lands near that ceiling, the polling loop
gives up right as the backend is about to finish - the move completes
server-side a moment later (which is why the eval bar looks right after a
refresh), but the frontend already stopped watching. Clicking "Make AI
Move" afterward then sends a request against a stale "it's still Black's
turn" belief, which the backend correctly rejects since the turn already
advanced - producing a confusing error instead of just doing nothing (or
picking up the already-completed move).

Fix:
1. Widen the polling window from 30s to 90s, giving real headroom.
2. Have "Make AI Move" re-sync with the server first, so a stale click
   just quietly re-syncs the board instead of firing a doomed request.

Run this from the repo root's chess-frontend/src/components directory:
    python3 patch_polltimeout.py
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
'''        let pollCount = 0;
        const maxPolls = 30; // 30 seconds max''',
'''        let pollCount = 0;
        // Gemini/Langflow calls have been observed taking 20-25+ seconds on
        // their own, before Stockfish ranking and network overhead - 30
        // consecutive 1s polls (30s total) was cutting it too close and
        // would occasionally time out the UI a moment before the backend
        // actually finished the move, leaving the frontend stuck showing a
        // stale "your turn" state until a manual refresh.
        const maxPolls = 90; // 90 seconds max''',
        "widen AI move polling window to 90s",
    ),
    (
'''    const handleMakeAIMove = async () => {
        console.log('🤖 [AI] Manual AI move requested');
        console.log('📊 [STATE] Current game state:', { turn: gameState.turn, is_game_over: gameState.is_game_over });
        if (gameState.is_game_over) {
            console.log('⚠️ [AI] Cannot make AI move - game is over');
            return;
        }
        console.log('⏳ [AI] Setting thinking status and clearing explanation...');''',
'''    const handleMakeAIMove = async () => {
        console.log('🤖 [AI] Manual AI move requested');
        console.log('📊 [STATE] Current game state:', { turn: gameState.turn, is_game_over: gameState.is_game_over });
        // Re-sync with the server before firing. If a background AI move
        // landed after the polling loop already gave up (e.g. a slow
        // Gemini call finishing just past the timeout window), our local
        // gameState here can be stale - sending a doomed request against a
        // turn that's already moved on is what produced a confusing
        // "Not AI's turn" error while the board had actually advanced.
        try {
            const statusResponse = await fetch('/api/status');
            const statusData = await statusResponse.json();
            if (statusData.success && statusData.status) {
                chessService.loadPosition(statusData.status.fen);
                const syncedGameState = chessService.getGameState();
                setGameState(syncedGameState);
                setMoveCount(syncedGameState.move_count);
                if (statusData.eval) {
                    setBoardEval(statusData.eval);
                }
                if (statusData.history) {
                    setMoveHistory(statusData.history);
                }
                if (syncedGameState.turn !== 'black') {
                    console.log('ℹ️ [AI] Server already advanced past Black\\'s turn - nothing to do');
                    setLangflowConfig(prev => ({ ...prev, status: 'idle' }));
                    return;
                }
                if (syncedGameState.is_game_over) {
                    console.log('⚠️ [AI] Cannot make AI move - game is over');
                    return;
                }
            }
        } catch (error) {
            console.error('❌ [ERROR] Failed to sync before AI move:', error);
        }
        console.log('⏳ [AI] Setting thinking status and clearing explanation...');''',
        "re-sync with server before manual AI move",
    ),
]

patch_file("ChessBoard.tsx", tsx_replacements)
print("🎉 All patches applied successfully.")
