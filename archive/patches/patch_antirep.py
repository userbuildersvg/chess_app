#!/usr/bin/env python3
"""
Fixes the AI shuffling a piece back and forth forever (e.g. rook g8<->h8)
even when it has a forced mate available. Root cause: in heavily winning
positions, many different candidate moves legitimately tie on evaluation
("opponent is mated in N no matter what"), and move selection had zero
awareness of the AI's own move history - so it could keep re-picking the
exact reversal of its last move among tied options, a real repetition-draw
risk despite being completely winning.

Fix: track the AI's last played move (last_ai_move) and deprioritize (not
eliminate) any candidate that exactly reverses it, before difficulty-
windowing. It stays available as a fallback if it's truly the only good
option, but loses to any equally-ranked alternative that makes progress.

Run this from the repo root:
    python3 patch_antirep.py
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


app_replacements = [
    (
'''refresh_eval()

# Difficulty: 1 (weakest) - 10 (strongest / current top-of-the-list behavior).''',
'''refresh_eval()

# UCI of the last move the AI itself played (not the opponent's), so we can
# avoid immediately undoing it. In heavily winning positions many different
# candidate moves legitimately tie on evaluation (e.g. "opponent is mated in
# 1 no matter what"), and without this the AI can get stuck shuffling a
# piece back and forth forever between two tied-eval moves - a real
# repetition-draw risk despite being completely winning.
last_ai_move = None


def is_reverse_move(move: str, other_move: str) -> bool:
    """True if `move` exactly undoes `other_move` (same squares, reversed)."""
    return (
        len(move) >= 4 and len(other_move) >= 4
        and move[0:2] == other_move[2:4] and move[2:4] == other_move[0:2]
    )


def deprioritize_reversal(ranked_moves: list, last_move) -> list:
    """
    Stable-partition ranked_moves so any move that exactly reverses
    last_move sinks to the bottom - it stays available as a fallback if it's
    truly the only good option, but loses out to any equally-ranked
    alternative that actually makes progress.
    """
    if not last_move:
        return ranked_moves
    non_reversing = [m for m in ranked_moves if not is_reverse_move(m["move"], last_move)]
    reversing = [m for m in ranked_moves if is_reverse_move(m["move"], last_move)]
    return non_reversing + reversing

# Difficulty: 1 (weakest) - 10 (strongest / current top-of-the-list behavior).''',
        "add last_ai_move tracking + deprioritize_reversal()",
    ),
    (
'''    ranked_moves = stockfish_service.get_ranked_moves(current_fen, top_n=None)
    candidates = select_candidates_by_difficulty(ranked_moves, ai_difficulty, window_size=DIFFICULTY_WINDOW_SIZE)''',
'''    ranked_moves = stockfish_service.get_ranked_moves(current_fen, top_n=None)
    ranked_moves = deprioritize_reversal(ranked_moves, last_ai_move)
    candidates = select_candidates_by_difficulty(ranked_moves, ai_difficulty, window_size=DIFFICULTY_WINDOW_SIZE)''',
        "deprioritize reversal in decide_ai_move()",
    ),
    (
'''            if move_result["success"]:
                logging.info(f"✅ Background AI move completed ({source}): {ai_move} - {explanation}")
                refresh_eval()
            else:
                logging.error(f"❌ Background AI move invalid: {ai_move}")''',
'''            if move_result["success"]:
                logging.info(f"✅ Background AI move completed ({source}): {ai_move} - {explanation}")
                refresh_eval()
                global last_ai_move
                last_ai_move = ai_move
            else:
                logging.error(f"❌ Background AI move invalid: {ai_move}")''',
        "track last_ai_move in make_ai_move_async()",
    ),
    (
'''def reset_game():
    """Reset game to initial state"""
    try:
        game.reset_game()
        refresh_eval()
        return create_success_response(SUCCESS_MESSAGES['game_reset'], {
            'eval': current_eval
        })''',
'''def reset_game():
    """Reset game to initial state"""
    global last_ai_move
    try:
        game.reset_game()
        refresh_eval()
        last_ai_move = None
        return create_success_response(SUCCESS_MESSAGES['game_reset'], {
            'eval': current_eval
        })''',
        "reset last_ai_move in /api/reset",
    ),
    (
'''            if move_result["success"]:
                logger.info(f"✅ Manual AI move completed ({source}): {ai_move} - {explanation}")
                refresh_eval()

                return create_success_response(f"AI played {ai_move}", {''',
'''            if move_result["success"]:
                logger.info(f"✅ Manual AI move completed ({source}): {ai_move} - {explanation}")
                refresh_eval()
                global last_ai_move
                last_ai_move = ai_move

                return create_success_response(f"AI played {ai_move}", {''',
        "track last_ai_move in /api/ai-move",
    ),
]

patch_file("app.py", app_replacements)
print("🎉 All patches applied successfully.")
