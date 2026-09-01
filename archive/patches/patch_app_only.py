#!/usr/bin/env python3
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
        '# Initialize game components\ngame = ChessGame()\n',
        '# Initialize game components\n'
        'game = ChessGame()\n\n'
        '# Cached evaluation of the current position, from White\'s perspective, for\n'
        '# the frontend eval bar. Recomputed once per applied move (not on every\n'
        '# /api/status poll - that would mean a Stockfish call every second while\n'
        '# the AI is "thinking", which is both wasteful and slow).\n'
        'current_eval = {"score": None, "mate_in": None}\n\n\n'
        'def refresh_eval():\n'
        '    """Recompute current_eval from the game\'s current position."""\n'
        '    global current_eval\n'
        '    try:\n'
        '        current_eval = stockfish_service.get_position_evaluation(game.get_fen())\n'
        '    except Exception as e:\n'
        '        logger.warning(f"⚠️ Failed to refresh position evaluation: {e}")\n\n\n'
        'refresh_eval()\n',
        "add current_eval cache + refresh_eval()",
    ),
    (
'''            move_result = game.make_langflow_move(ai_move, explanation=explanation)

            if move_result["success"]:
                logging.info(f"✅ Background AI move completed ({source}): {ai_move} - {explanation}")
            else:
                logging.error(f"❌ Background AI move invalid: {ai_move}")''',
'''            move_result = game.make_langflow_move(ai_move, explanation=explanation)

            if move_result["success"]:
                logging.info(f"✅ Background AI move completed ({source}): {ai_move} - {explanation}")
                refresh_eval()
            else:
                logging.error(f"❌ Background AI move invalid: {ai_move}")''',
        "refresh_eval() after background AI move",
    ),
    (
'''        result = game.make_player_move(player_move)
        logging.info(f"📥 Player move result: {result}")

        if not result['success']:
            return result

        if game.board.is_game_over():
            return create_success_response('Game over!', {
                'game_over': True,
                'status': game.get_game_status(),
                'player_move': result
            })''',
'''        result = game.make_player_move(player_move)
        logging.info(f"📥 Player move result: {result}")

        if not result['success']:
            return result

        refresh_eval()

        if game.board.is_game_over():
            return create_success_response('Game over!', {
                'game_over': True,
                'status': game.get_game_status(),
                'player_move': result,
                'eval': current_eval
            })''',
        "refresh_eval() + eval in /api/move game-over branch",
    ),
    (
'''        response_data = {
            'player_move': result,
            'model_move': model_result,
            'status': game.get_game_status()
        }

        return create_success_response('Move processed', response_data)''',
'''        response_data = {
            'player_move': result,
            'model_move': model_result,
            'status': game.get_game_status(),
            'eval': current_eval
        }

        return create_success_response('Move processed', response_data)''',
        "eval in /api/move normal response",
    ),
    (
'''    """Reset game to initial state"""
    try:
        game.reset_game()
        return create_success_response(SUCCESS_MESSAGES['game_reset'])''',
'''    """Reset game to initial state"""
    try:
        game.reset_game()
        refresh_eval()
        return create_success_response(SUCCESS_MESSAGES['game_reset'], {
            'eval': current_eval
        })''',
        "refresh_eval() + eval in /api/reset",
    ),
    (
'''        return create_success_response('Status retrieved', {
            'status': game.get_game_status(),
            'history': game.game_history[-10:],
            'difficulty': ai_difficulty
        })''',
'''        return create_success_response('Status retrieved', {
            'status': game.get_game_status(),
            'history': game.game_history[-10:],
            'difficulty': ai_difficulty,
            'eval': current_eval
        })''',
        "eval in /api/status",
    ),
    (
'''            move_result = game.make_langflow_move(ai_move, explanation=explanation)

            if move_result["success"]:
                logger.info(f"✅ Manual AI move completed ({source}): {ai_move} - {explanation}")

                return create_success_response(f"AI played {ai_move}", {
                    "ai_move": ai_move,
                    "reasoning": explanation,
                    "source": source,
                    "difficulty": ai_difficulty,
                    "game_state": {
                        "fen": game.get_fen(),
                        "turn": game.get_current_turn(),
                        "is_game_over": game.is_game_over(),
                        "is_check": game.is_in_check(),
                        "is_checkmate": game.is_checkmate(),
                        "is_stalemate": game.is_stalemate(),
                        "legal_moves": game.get_legal_moves_uci()
                    }
                })''',
'''            move_result = game.make_langflow_move(ai_move, explanation=explanation)

            if move_result["success"]:
                logger.info(f"✅ Manual AI move completed ({source}): {ai_move} - {explanation}")
                refresh_eval()

                return create_success_response(f"AI played {ai_move}", {
                    "ai_move": ai_move,
                    "reasoning": explanation,
                    "source": source,
                    "difficulty": ai_difficulty,
                    "eval": current_eval,
                    "game_state": {
                        "fen": game.get_fen(),
                        "turn": game.get_current_turn(),
                        "is_game_over": game.is_game_over(),
                        "is_check": game.is_in_check(),
                        "is_checkmate": game.is_checkmate(),
                        "is_stalemate": game.is_stalemate(),
                        "legal_moves": game.get_legal_moves_uci()
                    }
                })''',
        "refresh_eval() + eval in /api/ai-move success response",
    ),
]

patch_file("app.py", app_replacements)
print("🎉 All patches applied successfully.")
