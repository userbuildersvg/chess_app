---
title: Chess AI Platform
emoji: ♟️
colorFrom: blue
colorTo: purple
---

# Chess AI Platform

A modern chess game where you play against a **Large Language Model** through **Langflow**. This project demonstrates how LLMs can play chess using smart prompting instead of traditional chess engines.

> **Can an AI that has only learned from texts on the internet really play chess?** This project answers that question by leveraging the knowledge that LLMs already gained during training - including chess games and master analyses.

## 🚀 Features

### Frontend (React + TypeScript + Vite)
- **Modern React 18** with TypeScript for type safety
- **Interactive chess board** with drag & drop (ChessboardJSX)
- **Real-time game state** management
- **Responsive design** for all devices
- **Hot Module Replacement** for instant development updates

### Backend & AI
- **FastAPI backend** with comprehensive chess logic
- **Langflow integration** for AI move generation
- **Smart prompting system** that prevents illegal moves
- **Real-time move validation** and game state tracking
- **FEN + UCI format** for precise position communication
- **Docker containerization** for easy deployment
- **Health checks** and monitoring

### 🧠 LLM Chess Approach
- **No traditional chess engine** - pure LLM-based gameplay
- **Constrained prompting** with legal moves list
- **Move explanations** from the AI opponent
- **Rule-based framework** prevents hallucinations
- **Knowledge from training data** (chess games, analyses)

## 🤖 AI Models & Integration

### Supported AI Providers
- **OpenAI**: GPT-3.5, GPT-4, GPT-4 Turbo
- **Anthropic**: Claude-3 Haiku, Sonnet, Opus
- **Google**: Gemini Pro, Gemini Ultra
- **Hugging Face**: Various open-source models
- **Local Models**: Ollama, LM Studio integration

### Chess-Specific Features
- **Position Analysis**: Evaluate board positions and suggest improvements
- **Move Generation**: AI-powered move suggestions with explanations
- **Opening Theory**: Database-driven opening recommendations
- **Tactical Patterns**: Recognition of tactical motifs and combinations
- **Endgame Knowledge**: Advanced endgame technique suggestions

### 🎯 Smart Prompting System
The key to making LLMs play chess is **constrained prompting**:

```
You are playing chess as the black pieces.
Follow the rules of chess strictly.
Here is the current position in FEN: rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1
Legal moves (in UCI format): a2a3 a2a4 b2b3 b2b4 c2c3 c2c4 d2d3 d2d4 e2e3 e2e4 f2f3 f2f4 g2g3 g2g4 h2h3 h2h4

Instruction: From the provided list, choose the single best move in UCI format (e.g., "e2e4").
Explain in 1–2 sentences why this move is strategically reasonable.
Do not invent moves outside the provided list.
```

**Why this works:**
- ✅ No illegal moves (model can only choose from legal list)
- ✅ No rule violations (moves are pre-validated)
- ✅ Strategic reasoning (model must explain its choice)
- ✅ No hallucinations (constrained to valid options)

### Performance Tracking
- **Accuracy Metrics**: Track AI move quality and success rates
- **Response Times**: Monitor AI response latency
- **Generation Attempts**: Count successful vs failed AI generations
- **User Feedback**: Collect and analyze user satisfaction

## 🚀 Quick Start

### Prerequisites
- **Docker** and **Docker Compose**
- **Langflow** running locally on port 7860

### Local Development Setup

1. **Clone the repository**
   ```bash
   git clone <repository-url>
   cd chess-ai-platform
   ```

2. **Start Langflow** (in separate terminal)
   ```bash
   langflow run --host 127.0.0.1 --port 7860
   ```

3. **Build and run with Docker**
   ```bash
   docker-compose up --build
   ```

4. **Access the application**
   - Application: http://localhost:3000
   - Backend API: http://localhost:8080
   - API Docs: http://localhost:8080/docs
   - Langflow: http://localhost:7860

## 🔄 Langflow Integration

### Prerequisites & Setup
1. **Install Langflow**: `pip install langflow`
2. **Configure API Keys**: Set up your AI provider API keys in Langflow
   - **OpenAI**: Get API key from [OpenAI Platform](https://platform.openai.com/api-keys)
   - **Anthropic**: Get API key from [Anthropic Console](https://console.anthropic.com/)
   - **Google**: Get API key from [Google AI Studio](https://aistudio.google.com/app/apikey)
   - **Hugging Face**: Get API key from [Hugging Face Settings](https://huggingface.co/settings/tokens)

3. **Start Langflow** (in separate terminal):
   ```bash
   langflow run --host 127.0.0.1 --port 7860
   ```

4. **Configure Chess Flows**: Import chess-specific flows in Langflow interface
5. **Start the application**: `docker-compose up --build`
6. **Open Chess App**: http://localhost:3000

### Available Flow Types
- **Chess Analyzer** - General position analysis and move evaluation
- **Opening Expert** - Specialized opening knowledge and theory
- **Tactical Solver** - Find tactical combinations and patterns
- **Position Evaluator** - Evaluate chess positions and suggest improvements
- **Endgame Specialist** - Advanced endgame knowledge and techniques

## API Endpoints

### Chess Game
- `GET /` - Main chess game interface
- `POST /api/move` - Make a chess move
- `POST /api/ai-move` - Get AI move
- `GET /api/status` - Get current game state
- `GET /api/reset` - Reset game

### Langflow Integration
- `GET /api/langflow/initialize` - Initialize Langflow connection
- `POST /api/langflow/chess-move` - Generate chess move using Langflow

## 🔄 How It Works: Step-by-Step Flow

### 1. Player Makes a Move
```typescript
// Frontend: Player clicks piece from e2 to e4
const makePlayerMove = async (from: string, to: string) => {
  const response = await fetch('/api/move', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ move: `${from}${to}` }) // "e2e4"
  });
  return await response.json();
};
```

### 2. Backend Validation & Processing
```python
# Backend: Validate move and prepare AI response
@app.post("/api/move")
async def make_move(request: MoveRequest):
    result = game.make_player_move(request.move)  # Validate with python-chess
    
    if game.board.turn == chess.BLACK:  # AI's turn
        ai_move = await langflow_manager.get_best_move(
            game.get_fen(), game.get_legal_moves_uci()
        )
        return {"success": True, "ai_move": ai_move}
    
    return result
```

### 3. Langflow → LLM Processing
```
FEN: rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1
Legal moves: e7e5 c7c5 g8f6 ...
Task: Pick the best move (UCI format) and explain why.

LLM Response:
Move: e7e5
Reasoning: This move balances control over the center and opens lines for the bishop and queen.
```

### 4. Response Back to Player
```json
{
  "success": true,
  "player_move": "e2e4",
  "ai_move": "e7e5",
  "explanation": "This move balances control over the center and opens lines for the bishop and queen."
}
```

## 🏗️ Project Structure

### Frontend Components (`chess-frontend/src/`)
- **`App.tsx`** - Main application component with game state management
- **`components/ChessBoard.tsx`** - Interactive chess board with drag & drop
- **`components/ChessBoard.css`** - Chess board styling and animations
- **`services/chessService.ts`** - API communication service
- **`types/chess.ts`** - TypeScript type definitions for chess data
- **`main.tsx`** - Application entry point

### Backend Components (`/`)
- **`app.py`** - FastAPI application with chess game endpoints
- **`game_logic.py`** - Core chess game logic and move validation
- **`langflow_service.py`** - Langflow AI integration service
- **`langflow_config.py`** - Langflow configuration and flow management
- **`config.py`** - Application configuration and settings
- **`utils.py`** - Utility functions for API responses
- **`flows/`** - Langflow chess-specific flow definitions

### Configuration Files
- **`docker-compose.yml`** - Multi-service Docker orchestration
- **`Dockerfile`** - Multi-stage build for frontend + backend
- **`requirements.txt`** - Python dependencies
- **`dev.sh`** - Development automation script

## Architecture

```
┌─────────────────┐    ┌─────────────────┐
│ Chess AI Platform│    │    Langflow     │
│   (Port 3000)   │◄──►│   (Port 7860)   │
│                 │    │                 │
│ - FastAPI       │    │ - AI Workflows  │
│ - Chess Logic   │    │ - Model Hub      │
│ - React Frontend│    │ - Visual Builder│
│ - Nginx Proxy   │    │                 │
└─────────────────┘    └─────────────────┘
```

## 🛠️ Development Commands

```bash
# Start the application
./dev.sh start

# Stop the application
./dev.sh stop

# View logs
./dev.sh logs

# Rebuild and restart
./dev.sh rebuild

# Clean all Docker resources
./dev.sh clean
```

## 📚 Additional Resources

### Documentation Links
- **FastAPI**: [Official Documentation](https://fastapi.tiangolo.com/)
- **React**: [React Documentation](https://react.dev/)
- **TypeScript**: [TypeScript Handbook](https://www.typescriptlang.org/docs/)
- **Chess.js**: [Chess.js Library](https://github.com/jhlywa/chess.js)
- **React Chessboard**: [ChessboardJSX](https://github.com/Clariity/react-chessboard)
- **Langflow**: [Langflow Documentation](https://docs.langflow.org/)

### Chess Resources
- **Chess Notation**: [Algebraic Notation Guide](https://en.wikipedia.org/wiki/Algebraic_notation_(chess))
- **FEN Format**: [Forsyth-Edwards Notation](https://en.wikipedia.org/wiki/Forsyth%E2%80%93Edwards_Notation)
- **UCI Protocol**: [Universal Chess Interface](https://en.wikipedia.org/wiki/Universal_Chess_Interface)

## 🎯 Current Limitations & Future Roadmap

### Current Limitations
- **Move Quality**: LLM moves are legal but not always optimal
- **Complex Positions**: Performance varies in complex middlegames
- **Reliability**: Sometimes LLM fails to return legal moves (retry mechanism)
- **Strategy Depth**: Lacks deeper strategic understanding

### 🚀 Future Enhancements
- **Stockfish Validation**: Compare LLM moves with engine analysis
- **Master Styles**: Emulate playing styles of Capablanca, Fischer, Kasparov
- **Opening Selection**: AI sticks to chosen opening plans
- **RAG Integration**: Connect to database of games and analyses
- **Agent Systems**: Background agents for move validation and correction
- **Chess Lessons**: AI explains moves and teaches strategy
- **A/B Testing**: Compare different prompts and models

### 🎮 Why Chess is Perfect for LLM Testing
- **Clear Rules**: Instant feedback on illegal moves
- **Instant Validation**: Immediate detection of logical errors
- **Endless Scenarios**: Infinite variety of positions
- **Strategic Depth**: Tests reasoning and planning abilities

## 🐛 Troubleshooting

### Common Issues
1. **Langflow Connection Failed**: Ensure Langflow is running on port 7860
2. **API Key Errors**: Verify your AI provider API keys in Langflow
3. **Port Conflicts**: Check if ports 3000/8080 are available
4. **Docker Issues**: Ensure Docker Desktop is running and updated
5. **LLM No Response**: Check Langflow logs, may need to retry

### Debug Commands
```bash
# Check Docker status
docker info

# View container logs
docker-compose logs -f

# Check port usage
lsof -i :3000
lsof -i :8080
lsof -i :7860

# Test Langflow connection
curl http://localhost:7860/health
```

## 🎯 TL;DR - Chess with LLMs

- **Language models CAN play chess** - but only up to a certain level
- **Smart prompting is key** - FEN + legal moves list prevents hallucinations
- **Architecture**: Frontend (React) + Backend (FastAPI) + Langflow + LLM
- **Goal**: Not grandmaster-level, but a playful prototype for teaching and analysis
- **Future**: Master styles, RAG integration, agent systems for higher reliability