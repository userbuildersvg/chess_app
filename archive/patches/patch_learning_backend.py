#!/usr/bin/env python3
"""
Wires learning_service.py into app.py: every human/AI move gets logged
(position, eval swing, who chose it, difficulty), games get finalized with
a result the moment they end, and the AI's own candidate ordering gets a
nudge from what's worked before in positions it's seen exactly before.

Every replacement below anchors on a short, already-unique line rather than
reproducing large indented blocks - this session hit real, repeated
failures where big multi-line indented blocks silently lost whitespace in
transit through chat, even when they looked byte-identical on screen. Short
single-line anchors (verified unique against the real file before this
script was written) don't have that problem.

Run this from the repo root (same directory as app.py):
    python3 patch_learning_backend.py

Safe to run more than once: it checks for its own already-applied marker
first and exits without touching the file if it's already been patched,
rather than silently re-inserting all 16 blocks a second time (which is
what happens if you skip this check - each anchor is still present *after*
patching, since every insertion happens right after, not instead of, the
original line).
"""
import sys

ALREADY_APPLIED_MARKER = "from learning_service import learning_service"


def patch_file(path, replacements, already_applied_marker=None):
    with open(path, "r", encoding="utf-8") as f:
        src = f.read()

    if already_applied_marker and already_applied_marker in src:
        print(f"ℹ️ {path}: already contains '{already_applied_marker}' - looks like this patch was already applied. Skipping (no changes written).")
        return

    for old, new, label in replacements:
        count = src.count(old)
        if count != 1:
            print(f"❌ {path}: expected exactly 1 match for '{label}', found {count}. Aborting - no changes written.")
            sys.exit(1)
        src = src.replace(old, new, 1)

    with open(path, "w", encoding="utf-8") as f:
        f.write(src)
    print(f"✅ {path}: applied {len(replacements)} patch(es)")


LEARNING_STATE_BLOCK = '''# Cross-game "learning" state (see learning_service.py) - which persisted
# game row current moves are being logged against, and whether that row's
# already been finalized (so repeated is_game_over() checks after the game
# ends don't try to finalize the same game more than once).
current_game_id = learning_service.start_game(player_color)
game_finalized = False


def start_new_learning_game():
    """Call whenever the board is reset/restarted, so subsequent moves log
    against a fresh row instead of appending to the just-finished game."""
    global current_game_id, game_finalized
    current_game_id = learning_service.start_game(player_color)
    game_finalized = False


def determine_game_result_and_termination(g: ChessGame):
    """Only meaningful once g.is_game_over() is True. Returns
    (result, termination) - result is 'white_win' | 'black_win' | 'draw',
    termination is 'checkmate' | 'stalemate' | 'insufficient_material' |
    '75_move_rule' | 'fivefold_repetition' | 'other'."""
    board = g.board
    if board.is_checkmate():
        winner = "black" if board.turn else "white"
        return f"{winner}_win", "checkmate"
    if board.is_stalemate():
        return "draw", "stalemate"
    if board.is_insufficient_material():
        return "draw", "insufficient_material"
    if board.is_seventyfive_moves():
        return "draw", "75_move_rule"
    if board.is_fivefold_repetition():
        return "draw", "fivefold_repetition"
    return "draw", "other"


def finalize_learning_game_if_over():
    """Idempotent - safe to call after every move. Only actually records a
    result the first time the current game is found to be over."""
    global game_finalized
    if game_finalized or current_game_id is None or not game.board.is_game_over():
        return
    result, termination = determine_game_result_and_termination(game)
    learning_service.finalize_game(current_game_id, result, termination)
    game_finalized = True


'''

py_replacements = [
    (
        "from langflow_config import langflow_config",
        "from langflow_config import langflow_config\nfrom learning_service import learning_service",
        "import learning_service",
    ),
    (
        "def is_reverse_move(move: str, other_move: str) -> bool:",
        LEARNING_STATE_BLOCK + "def is_reverse_move(move: str, other_move: str) -> bool:",
        "add learning-service globals and helpers",
    ),
    (
        "candidates = select_candidates_by_difficulty(ranked_moves, ai_difficulty, window_size=DIFFICULTY_WINDOW_SIZE)",
        "candidates = select_candidates_by_difficulty(ranked_moves, ai_difficulty, window_size=DIFFICULTY_WINDOW_SIZE)\n    candidates = learning_service.reweight_candidates(current_fen, candidates)",
        "reweight candidates in decide_ai_move",
    ),
    (
        "result = game.make_player_move(player_move)",
        "mover_color = game.get_current_turn()\n        fen_before = game.get_fen()\n        eval_before = dict(current_eval)\n        result = game.make_player_move(player_move)",
        "capture pre-move state for human move",
    ),
    (
        "if game.board.is_game_over():",
        "san = game.game_history[-1].get('san') if game.game_history else None\n        learning_service.record_move(\n            current_game_id, len(game.game_history), mover_color, \"human\",\n            player_move, san, fen_before, game.get_fen(), eval_before, dict(current_eval)\n        )\n        finalize_learning_game_if_over()\n        if game.board.is_game_over():",
        "record human move + finalize",
    ),
    (
        "return create_success_response(SUCCESS_MESSAGES['game_reset'], {",
        "start_new_learning_game()\n        return create_success_response(SUCCESS_MESSAGES['game_reset'], {",
        "start new learning game on reset",
    ),
    (
        "ai_scheduled = False",
        "start_new_learning_game()\n        ai_scheduled = False",
        "start new learning game on set-color",
    ),
    (
        'game_mode = "ai_vs_ai"',
        'start_new_learning_game()\n        game_mode = "ai_vs_ai"',
        "start new learning game on ai-vs-ai start",
    ),
    (
        'logging.info("🤖 Background AI move starting...")',
        'logging.info("🤖 Background AI move starting...")\n            eval_before = dict(current_eval)',
        "capture pre-move eval in make_ai_move_async",
    ),
    (
        'logging.error(f"❌ Background AI move invalid: {ai_move}")',
        'logging.error(f"❌ Background AI move invalid: {ai_move}")\n            if move_result["success"]:\n                eval_after = dict(current_eval)\n                san = game.game_history[-1].get(\'san\') if game.game_history else None\n                learning_service.record_move(\n                    current_game_id, len(game.game_history), ai_color, "ai",\n                    ai_move, san, current_fen, game.get_fen(), eval_before, eval_after,\n                    difficulty=ai_difficulty, source=source, explanation=explanation\n                )\n                finalize_learning_game_if_over()',
        "record AI move in make_ai_move_async",
    ),
    (
        'if not legal_moves or game.is_game_over():',
        'eval_before = dict(current_eval)\n            if not legal_moves or game.is_game_over():',
        "capture pre-move eval in make_ai_vs_ai_move_async",
    ),
    (
        'last_ai_move_by_color[current_turn] = ai_move',
        'last_ai_move_by_color[current_turn] = ai_move\n                eval_after = dict(current_eval)\n                san = game.game_history[-1].get(\'san\') if game.game_history else None\n                learning_service.record_move(\n                    current_game_id, len(game.game_history), current_turn, "ai",\n                    ai_move, san, current_fen, game.get_fen(), eval_before, eval_after,\n                    difficulty=ai_difficulty, source=source, explanation=explanation\n                )\n                finalize_learning_game_if_over()',
        "record AI move in make_ai_vs_ai_move_async",
    ),
    (
        'logger.info("🤖 Manual AI move requested")',
        'logger.info("🤖 Manual AI move requested")\n            eval_before = dict(current_eval)',
        "capture pre-move eval in manual ai-move endpoint",
    ),
    (
        'return create_success_response(f"AI played {ai_move}", {',
        'san = game.game_history[-1].get(\'san\') if game.game_history else None\n                learning_service.record_move(\n                    current_game_id, len(game.game_history), ai_color, "ai",\n                    ai_move, san, current_fen, game.get_fen(), eval_before, dict(current_eval),\n                    difficulty=ai_difficulty, source=source, explanation=explanation\n                )\n                finalize_learning_game_if_over()\n                return create_success_response(f"AI played {ai_move}", {',
        "record AI move in manual ai-move endpoint",
    ),
    (
        '@app.get("/api/langflow/initialize")',
        '@app.get("/api/learning/summary")\ndef get_learning_summary():\n    """Live opponent-profile + AI self-history summary for the frontend\'s\n    Learning panel (see learning_service.py)."""\n    try:\n        return create_success_response("Learning summary retrieved", learning_service.get_learning_summary())\n    except Exception as e:\n        return create_error_response("Failed to get learning summary", details={"error": str(e)})\n\n\n@app.get("/api/langflow/initialize")',
        "add /api/learning/summary endpoint",
    ),
    (
        '"set_difficulty": "/api/difficulty"',
        '"set_difficulty": "/api/difficulty",\n            "learning_summary": "/api/learning/summary"',
        "list new endpoint in API index",
    ),
]

patch_file("app.py", py_replacements, already_applied_marker=ALREADY_APPLIED_MARKER)
print("🎉 Done.")
