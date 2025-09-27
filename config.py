"""
Configuration settings for chess application
"""

# Langflow configuration
LANGFLOW_FLOW_ID = "8f2ac28a-4b1b-4bb0-8703-70e58cb00def"

# Server configuration
HOST = '0.0.0.0'
PORT = 7860
DEBUG = True

# Game configuration
BOARD_SIZE = 400

# Error messages
ERROR_MESSAGES = {
    'langflow_not_available': 'Langflow AI not available',
    'invalid_move': 'Invalid move format',
    'game_over': 'Game is already over',
    'no_data': 'No data provided',
    'move_required': 'Move is required',
    'server_error': 'Internal server error'
}

# Success messages
SUCCESS_MESSAGES = {
    'langflow_connected': 'Langflow AI connected successfully',
    'move_played': 'Move played successfully',
    'game_reset': 'Game reset successfully',
    'ai_move_completed': 'AI move completed successfully'
}