"""
Configuration settings for chess application
"""

import os

# Server configuration
HOST = '0.0.0.0'

# Read from the environment so DEBUG can never accidentally ship as True.
# Enables uvicorn auto-reload in app.py; must stay false in any real deployment.
DEBUG = os.environ.get("DEBUG", "false").lower() == "true"

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