from pydantic import BaseModel
import asyncio
import logging
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from game_logic import ChessGame
from utils import create_success_response, create_error_response
from config import HOST, DEBUG, ERROR_MESSAGES, SUCCESS_MESSAGES
import chess
from langflow_service import ChessLangflowManager
from langflow_config import langflow_config

app = FastAPI(title="Chess AI Platform", version="1.0.0", description="Modern chess game with AI opponent")

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize game components
game = ChessGame()

# Initialize Langflow manager with error handling
try:
    langflow_manager = ChessLangflowManager(langflow_config)
    logger.info("✅ Langflow manager initialized successfully")
except Exception as e:
    logger.error(f"❌ Failed to initialize Langflow manager: {e}")
    langflow_manager = None

async def make_ai_move_async():
    """Make AI move in background"""
    try:
        logging.info("🤖 Background AI move starting...")
        
        # Get current game state
        current_fen = game.get_fen()
        legal_moves = game.get_legal_moves_uci()
        current_turn = game.get_current_turn()
        
        if current_turn != "black" or not legal_moves:
            logging.info(f"⚠️ Background AI move cancelled - turn: {current_turn}, moves: {len(legal_moves)}")
            return
        
        # Check if Langflow manager is available
        if not langflow_manager:
            logging.error("❌ Langflow manager not available")
            return
        
        # Get AI move
        success, result = await langflow_manager.get_best_move(current_fen, legal_moves)
        
        if success and result.get("outputs", {}).get("best_move"):
            ai_move = result["outputs"]["best_move"]
            explanation = result.get("outputs", {}).get("reasoning", "")
            
            # Use make_langflow_move to store explanation
            move_result = game.make_langflow_move(ai_move, explanation=explanation)
            
            if move_result["success"]:
                logging.info(f"✅ Background AI move completed: {ai_move} - {explanation}")
            else:
                logging.error(f"❌ Background AI move invalid: {ai_move}")
        else:
            error_info = result.get("error", "Unknown error")
            logging.error(f"❌ Background AI move failed: {error_info}")
            
    except Exception as e:
        logging.error(f"❌ Error in background AI move: {e}")

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Pydantic models
class MoveRequest(BaseModel):
    move: str

# Async AI move function
# Background AI move function removed - use manual /api/ai-move endpoint instead

# Chess Game Endpoints

@app.get("/")
def index():
    """API info"""
    return {
        "message": "Chess AI Platform Backend",
        "version": "1.0.0",
        "description": "Modern chess game with AI opponent",
        "endpoints": {
            "game_status": "/api/status",
            "make_move": "/api/move",
            "ai_move": "/api/ai-move",
            "reset_game": "/api/reset",
            "langflow_init": "/api/langflow/initialize"
        }
    }

@app.post("/api/move")
async def make_move(request: MoveRequest):
    """Make player move and get AI response"""
    try:
        player_move = request.move
        logging.info(f"🔄 Received move request: {player_move}")
        
        if not player_move:
            raise HTTPException(status_code=400, detail=ERROR_MESSAGES['move_required'])
        
        # Player move
        result = game.make_player_move(player_move)
        logging.info(f"📥 Player move result: {result}")
        
        if not result['success']:
            return result
        
        # Check if game is over after player move
        if game.board.is_game_over():
            return create_success_response('Game over!', {
                'game_over': True,
                'status': game.get_game_status(),
                'player_move': result
            })
        
        # Schedule automatic AI move in background
        if game.board.turn == chess.BLACK:
            logging.info("🤖 It's black's turn - scheduling AI move in background")
            asyncio.create_task(make_ai_move_async())
            model_result = {
                'success': True,
                'message': 'AI move scheduled in background',
                'ai_scheduled': True
            }
        else:
            logging.info("⚪ It's white's turn - no AI move needed")
            model_result = {
                'success': False,
                'message': 'White\'s turn - player moves next',
                'skip_ai': True
            }
        
        response_data = {
            'player_move': result,
            'model_move': model_result,
            'status': game.get_game_status()
        }
        
        return create_success_response('Move processed', response_data)
        
    except Exception as e:
        return create_error_response('Failed to process move', details={'error': str(e)})

@app.get("/api/reset")
def reset_game():
    """Reset game to initial state"""
    try:
        game.reset_game()
        return create_success_response(SUCCESS_MESSAGES['game_reset'])
    except Exception as e:
        return create_error_response('Failed to reset game', details={'error': str(e)})

@app.get("/api/status")
def get_status():
    """Get current game status"""
    try:
        return create_success_response('Status retrieved', {
            'status': game.get_game_status(),
            'history': game.game_history[-10:]
        })
    except Exception as e:
        return create_error_response('Failed to get status', details={'error': str(e)})

@app.get("/api/langflow/initialize")
async def initialize_langflow():
    """Initialize Langflow connection"""
    try:
        return create_success_response("Langflow initialized successfully", {
            "status": "initialized",
            "flows_created": True
        })
    except Exception as e:
        return create_error_response("Error initializing Langflow", {
            "error": str(e)
        })

@app.post("/api/ai-move")
async def make_ai_move():
    """Manually trigger AI move"""
    try:
        logger.info("🤖 Manual AI move requested")
        
        # Get current game state
        current_fen = game.get_fen()
        legal_moves = game.get_legal_moves_uci()
        current_turn = game.get_current_turn()
        
        # AI can play for any color (allows AI vs AI)
        logger.info(f"🎯 AI will play for {current_turn}")
        
        if not legal_moves:
            return create_error_response("No legal moves available", {
                "fen": current_fen,
                "game_over": game.is_game_over()
            })
        
        # Check if Langflow manager is available
        if not langflow_manager:
            return create_error_response("Langflow manager not available", {
                "fen": current_fen,
                "legal_moves": legal_moves
            })
        
        # Get AI move
        success, result = await langflow_manager.get_best_move(current_fen, legal_moves)
        
        if success and result.get("outputs", {}).get("best_move"):
            ai_move = result["outputs"]["best_move"]
            explanation = result.get("outputs", {}).get("reasoning", "")
            
            # Make the AI move using make_langflow_move to store explanation
            move_result = game.make_langflow_move(ai_move, explanation=explanation)
            
            if move_result["success"]:
                logger.info(f"✅ Manual AI move completed: {ai_move} - {explanation}")
                
                return create_success_response(f"AI played {ai_move}", {
                    "ai_move": ai_move,
                    "reasoning": explanation,
                    "game_state": {
                        "fen": game.get_fen(),
                        "turn": game.get_current_turn(),
                        "is_game_over": game.is_game_over(),
                        "is_check": game.is_in_check(),
                        "is_checkmate": game.is_checkmate(),
                        "is_stalemate": game.is_stalemate(),
                        "legal_moves": game.get_legal_moves_uci()
                    }
                })
            else:
                return create_error_response(f"Invalid AI move: {ai_move}", {
                    "ai_move": ai_move,
                    "error": move_result.get("message", "Unknown error"),
                    "reasoning": result.get("outputs", {}).get("reasoning", "")
                })
        else:
            # Return detailed error information
            error_info = result.get("error", "Unknown error")
            flow_used = result.get("flow_used", "Unknown")
            reasoning = result.get("outputs", {}).get("reasoning", "No reasoning provided")
            
            return create_error_response("AI move generation failed", {
                "error": error_info,
                "flow_used": flow_used,
                "reasoning": reasoning,
                "fen": current_fen,
                "legal_moves": legal_moves
            })
            
    except Exception as e:
        logger.error(f"❌ Error in manual AI move: {e}")
        return create_error_response("Error making AI move", {
            "error": str(e)
        })

if __name__ == "__main__":
    LOCAL_PORT = 8080
    print("🚀 Starting Chess AI Platform...")
    print(f"🌐 Server available at: http://localhost:{LOCAL_PORT}")
    print(f"📚 API docs available at: http://localhost:{LOCAL_PORT}/docs")
    print(f"🔗 Connecting to Langflow at: {langflow_config.url}")
    print(f"♟️ Chess AI Platform v1.0.0")
    
    import uvicorn
    uvicorn.run("app:app", host=HOST, port=LOCAL_PORT, reload=DEBUG)