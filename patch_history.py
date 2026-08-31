#!/usr/bin/env python3
"""
Backend support for the v2 move history panel: /api/status now returns the
FULL game history (not just the last 10 half-moves), and /api/ai-move's
success response also includes it (mirroring how 'eval' was added there)
so the manual "Make AI Move" button updates the panel without an extra
round-trip.

Run this from the repo root:
    python3 patch_history.py
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
'''        return create_success_response('Status retrieved', {
            'status': game.get_game_status(),
            'history': game.game_history[-10:],
            'difficulty': ai_difficulty,
            'eval': current_eval
        })''',
'''        return create_success_response('Status retrieved', {
            'status': game.get_game_status(),
            # Full history (not just the last few moves) - the frontend move
            # history panel renders the whole game, not a recent window.
            'history': game.game_history,
            'difficulty': ai_difficulty,
            'eval': current_eval
        })''',
        "return full history in /api/status",
    ),
    (
'''                    "difficulty": ai_difficulty,
                    "eval": current_eval,
                    "game_state": {''',
'''                    "difficulty": ai_difficulty,
                    "eval": current_eval,
                    "history": game.game_history,
                    "game_state": {''',
        "add history to /api/ai-move success response",
    ),
]

patch_file("app.py", app_replacements)
print("🎉 All patches applied successfully.")
