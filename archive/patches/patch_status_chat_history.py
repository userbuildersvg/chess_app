"""
Adds chat_history to the /api/status response, so a page refresh doesn't
wipe the visible chat transcript - the backend already keeps the full
history server-side (see /api/chat's chat_history global, cleared
alongside every new game); this just exposes it the same way move history
(`history`) already is. ChessBoard.tsx's initializeGame() reads it on
mount to repopulate the chat box.

Idempotent: safe to re-run - does nothing if already applied.
"""
import sys

TARGET = "app.py"
ALREADY_APPLIED_MARKER = "'chat_history': chat_history"

OLD = """            'game_mode': game_mode,
            'ai_vs_ai_running': ai_vs_ai_running
        })
    except Exception as e:
        return create_error_response('Failed to get status', details={'error': str(e)})
@app.get("/api/learning/summary")"""

NEW = """            'game_mode': game_mode,
            'ai_vs_ai_running': ai_vs_ai_running,
            # So a page refresh doesn't wipe the visible chat transcript -
            # chat_history is already kept server-side (see /api/chat),
            # cleared alongside every new game. Loaded back into the chat
            # box on mount by ChessBoard.tsx's initializeGame().
            'chat_history': chat_history
        })
    except Exception as e:
        return create_error_response('Failed to get status', details={'error': str(e)})
@app.get("/api/learning/summary")"""


def main():
    with open(TARGET, "r", encoding="utf-8") as f:
        src = f.read()
    if ALREADY_APPLIED_MARKER in src:
        print(f"ℹ️ {TARGET}: already contains '{ALREADY_APPLIED_MARKER}' - looks like this patch was already applied. Skipping (no changes written).")
        return
    count = src.count(OLD)
    if count != 1:
        print(f"❌ {TARGET}: expected exactly 1 match for the /api/status block, found {count}. Aborting - no changes written.")
        sys.exit(1)
    src = src.replace(OLD, NEW, 1)
    with open(TARGET, "w", encoding="utf-8") as f:
        f.write(src)
    print(f"✅ {TARGET}: added chat_history to /api/status")


if __name__ == "__main__":
    main()
