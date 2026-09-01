#!/usr/bin/env python3
"""
Wires gemini_chat_service.py into app.py: a new /api/chat endpoint that lets
you talk to the AI mid-game (its reasoning, its plans, predictions about
your next move, etc.) via a direct Gemini REST call - completely separate
from the Langflow "Chess" flow used for move selection, which this patch
does not touch at all.

Run this AFTER patch_learning_backend.py (it anchors on things that patch
adds, like start_new_learning_game and the learning_service import) from
the repo root (same directory as app.py):
    python3 patch_chat_backend.py

Same anchor discipline as patch_learning_backend.py: every replacement
below is a short, already-unique, no-leading-whitespace fragment rather
than a reproduced multi-line indented block, because large indented blocks
pasted through chat have repeatedly and silently lost whitespace in
transit this project. These anchors were verified against the real,
already-patched app.py (post patch_learning_backend.py) before this script
was written.

Safe to run more than once: it checks for its own already-applied marker
first and exits without touching the file if it's already been patched,
rather than silently re-inserting all 4 blocks a second time.
"""
import sys

ALREADY_APPLIED_MARKER = "from gemini_chat_service import gemini_chat_service"

# This patch depends on patch_learning_backend.py having already been
# applied (it anchors on start_new_learning_game's body and the
# learning_service import). Checked separately from ALREADY_APPLIED_MARKER
# so the error message is specific to which problem it actually is.
DEPENDENCY_MARKER = "from learning_service import learning_service"


def patch_file(path, replacements, already_applied_marker=None, dependency_marker=None):
    with open(path, "r", encoding="utf-8") as f:
        src = f.read()

    if already_applied_marker and already_applied_marker in src:
        print(f"ℹ️ {path}: already contains '{already_applied_marker}' - looks like this patch was already applied. Skipping (no changes written).")
        return

    if dependency_marker and dependency_marker not in src:
        print(f"❌ {path}: doesn't contain '{dependency_marker}' - run patch_learning_backend.py first. Aborting - no changes written.")
        sys.exit(1)

    for old, new, label in replacements:
        count = src.count(old)
        if count != 1:
            print(f"❌ {path}: expected exactly 1 match for '{label}', found {count}. Aborting - no changes written.")
            sys.exit(1)
        src = src.replace(old, new, 1)

    with open(path, "w", encoding="utf-8") as f:
        f.write(src)
    print(f"✅ {path}: applied {len(replacements)} patch(es)")


CHAT_ENDPOINT_BLOCK = '''class ChatRequest(BaseModel):
    message: str


# In-memory mid-game chat transcript: list of {"role": "user"|"model", "text": str},
# oldest first. Not persisted (unlike learning_service's SQLite log) - it's
# scoped to the current game only, and gets cleared alongside it in
# start_new_learning_game(). Fine to lose on a server restart.
chat_history = []


@app.post("/api/chat")
async def chat_with_ai(request: ChatRequest):
    """Mid-game chat: ask the AI about the current position, its reasoning,
    what it expects you to play, etc. Calls Gemini directly (see
    gemini_chat_service.py) - the Langflow "Chess" flow is untouched by
    this endpoint."""
    try:
        user_message = request.message
        if not user_message or not user_message.strip():
            return create_error_response("Message required", {"message": "message must not be empty"})
        ai_color = get_ai_color()
        last_ai_explanation = None
        if game.game_history:
            last_ai_explanation = game.game_history[-1].get("explanation")
        game_context = {
            "fen": game.get_fen(),
            "move_history_san": [m.get("san") for m in game.game_history if m.get("san")],
            "player_color": player_color,
            "ai_color": ai_color,
            "difficulty": ai_difficulty,
            "is_game_over": game.is_game_over(),
            "last_ai_explanation": last_ai_explanation,
            "learning_context": learning_service.get_prompt_context_summary(),
        }
        success, reply = await gemini_chat_service.send_message(user_message, chat_history, game_context)
        if not success:
            return create_error_response("Chat request failed", {"message": reply})
        chat_history.append({"role": "user", "text": user_message})
        chat_history.append({"role": "model", "text": reply})
        return create_success_response("Chat reply received", {"reply": reply, "history": chat_history})
    except Exception as e:
        return create_error_response("Failed to process chat message", details={"error": str(e)})


'''

py_replacements = [
    (
        "from learning_service import learning_service",
        "from learning_service import learning_service\nfrom gemini_chat_service import gemini_chat_service",
        "import gemini_chat_service",
    ),
    (
        "    global current_game_id, game_finalized\n    current_game_id = learning_service.start_game(player_color)\n    game_finalized = False",
        "    global current_game_id, game_finalized, chat_history\n    current_game_id = learning_service.start_game(player_color)\n    game_finalized = False\n    chat_history = []",
        "clear chat_history in start_new_learning_game",
    ),
    (
        "# Chess Game Endpoints",
        CHAT_ENDPOINT_BLOCK + "# Chess Game Endpoints",
        "add ChatRequest model, chat_history, and /api/chat endpoint",
    ),
    (
        '"learning_summary": "/api/learning/summary"',
        '"learning_summary": "/api/learning/summary",\n            "chat": "/api/chat"',
        "list new endpoint in API index",
    ),
]

patch_file("app.py", py_replacements, already_applied_marker=ALREADY_APPLIED_MARKER, dependency_marker=DEPENDENCY_MARKER)
print("🎉 Done.")
