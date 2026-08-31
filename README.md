# Chess AI Platform

A chess app where you play as White against an AI opponent that combines
**Stockfish** (for move strength) with **Google Gemini** (for move
selection and explanation), fully containerized so it runs anywhere with
Docker.

## How the AI works

Instead of Stockfish just playing its best move, or Gemini freely
inventing one (which risks illegal/hallucinated moves), the two are
combined:

1. Stockfish ranks **every legal move** in the current position, best to
   worst.
2. A 5-move "window" is sliced out of that ranked list based on the
   current **difficulty** (1-10). Difficulty 10 uses the top 5 moves;
   difficulty 1 uses the bottom 5 (still 100% legal, just deliberately
   weak).
3. Gemini (via a Langflow flow) is shown only that shortlist and picks
   one, with a short explanation.
4. The pick is validated against the shortlist. If Gemini fails, times
   out, or picks something outside the list, the app falls back to the
   best move within that same difficulty window - so the game never
   stalls or plays an invalid move.

## Prerequisites

- Docker and Docker Compose installed
- A free Gemini API key: https://aistudio.google.com/apikey

Nothing else - Python, Node, and Stockfish are all bundled inside the
containers.

## Quick start

```bash
cp .env.example .env
```

Open `.env` and fill in your own key:

GEMINI_API_KEY=your-key-here
LANGFLOW_USERNAME=admin
LANGFLOW_PASSWORD=pick-any-password


Then build and run:

```bash
docker-compose up --build
```

First boot takes a minute or two - Langflow has to start up and the Chess
flow gets auto-imported with your API key substituted in. Every run after
that is faster.

Once it's up:

| What | Where |
|---|---|
| The chess app | http://localhost:3000 |
| Backend API docs | http://localhost:8080/docs |
| Langflow UI (optional, for inspecting/editing the flow) | http://localhost:7860 |

To stop everything:

```bash
docker-compose down
```

## Playing

You play White. Click a piece, then click a highlighted square to move.
The AI (Black) responds automatically. Use the **AI Difficulty** slider
(1-10) to control how strong the AI plays - this can be changed mid-game.

## Move quality

Every half-move - yours and the AI's - is graded chess.com style and shown
two ways: a colored badge on the square the move landed on, and the same
annotation glyph beside the move in the Moves list.

| Grade | Glyph | What it means |
|---|---|---|
| Brilliant | `!!` | A sound sacrifice - material offered, still (essentially) the best move |
| Great | `!` | The only move that holds the position; everything else drops ~1.5 pawns |
| Best | `★` | The engine's top choice |
| Excellent | `✓` | Within 20 centipawns of best |
| Good | `✓` | Within 50 centipawns |
| Book | `📖` | A known opening move (the opening is named in the tooltip) |
| Inaccuracy | `?!` | Loses 50-100 centipawns |
| Mistake | `?` | Loses 100-250 centipawns |
| Blunder | `??` | Loses more than 250 centipawns |
| Miss | `✗` | A forced mate was available and went unplayed |
| Forced | `□` | The only legal move |

Toggle it with the **Grade** switch in the Moves panel header. Switching it
off doesn't just hide the badges - it stops the extra Stockfish search that
runs after every half-move, so it's also the setting to reach for if moves
feel sluggish on a slow machine.

Grades are computed in the background and land a moment after the move
itself, so a badge appearing a second late is expected, not a bug. Openings
are recognised from a built-in book (see `OPENING_LINES` in
`move_quality.py`) rather than an external Polyglot file, so there's no
extra asset to ship - add lines there to widen the book.

## API endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/api/status` | Current board state, move history, difficulty |
| POST | `/api/move` | Submit a player move (UCI format, e.g. `e2e4`) |
| POST | `/api/ai-move` | Manually trigger the AI's move |
| GET | `/api/reset` | Reset the game to the starting position |
| GET / POST | `/api/difficulty` | Get or set AI difficulty (1-10) |

## Project structure

.
├── app.py # FastAPI backend, game/AI orchestration
├── game_logic.py # Chess rules/state via python-chess
├── stockfish_service.py # Ranks legal moves with Stockfish
├── langflow_service.py # Talks to Langflow/Gemini, picks from candidates
├── langflow_config.py # Langflow connection settings
├── chess-frontend/ # React + TypeScript frontend (Vite)
├── flows/Chess.json # Langflow flow template (key placeholder only)
├── Dockerfile # Builds frontend + backend into one image
├── docker-compose.yml # Orchestrates the app + Langflow containers
├── .env.example # Copy to .env and fill in your own secrets
└── .env # Your real secrets - never commit or share this


## Security notes

- `.env` holds your real Gemini API key and Langflow credentials. It's
  gitignored and is **not** included if you received this project as a
  zip - only `.env.example` (placeholders) ships with the code.
- `flows/Chess.json` contains a `__GEMINI_API_KEY__` placeholder, never a
  real key. The real key is substituted in automatically, in-container,
  at startup from your `.env`.

## Troubleshooting

- **First AI move takes a while:** normal on a fresh container - Langflow
  is still starting up and importing the flow. Subsequent moves are
  faster.
- **AI move fails / falls back to a "Stockfish-calculated move" message:**
  Gemini/Langflow didn't respond in time or picked something off the
  candidate list - the app automatically falls back to a strong legal
  move so the game keeps going. Check `docker-compose logs chess-langflow`
  if this happens repeatedly.
- **Port already in use:** something else on your machine is using 3000,
  8080, or 7860. Stop that process, or change the left-hand side of the
  port mappings in `docker-compose.yml` (e.g. `"3001:80"`).
