# Chess AI Platform

A chess app where you play against an AI opponent that combines
**Stockfish** (for move strength) with **Google Gemini** (for move
selection and explanation), fully containerized so it runs anywhere with
Docker. It also grades every move chess.com style, has a mid-game AI
chat, and keeps a cross-game learning layer that adapts to how you play.

## How the AI works

Instead of Stockfish just playing its best move, or Gemini freely
inventing one (which risks illegal/hallucinated moves), the two are
combined:

1. Stockfish ranks **every legal move** in the current position, best to
   worst.
2. Moves that would simply undo the AI's own last move are sunk down the
   list (`deprioritize_reversal()` in `app.py`), so the AI doesn't
   shuffle a piece back and forth.
3. A 3-move "window" is sliced out of that ranked list based on the
   current **difficulty** (1-20) - see `select_candidates_by_difficulty()`
   in `app.py`. Difficulty 20 sits the window at the top of the ranked
   list (strongest); difficulty 1 sits it at the bottom (still 100%
   legal, just deliberately weak). Values in between slide the window
   linearly.
4. The cross-game learning layer may reweight that shortlist based on
   your past games (`reweight_candidates()` in `learning_service.py`).
5. Gemini (via a Langflow flow) is shown only that shortlist and picks
   one, with a short explanation.
6. The pick is validated against the shortlist. If Gemini fails, times
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

Click a piece, then click a highlighted square to move. The AI responds
automatically. Use the **AI Difficulty** slider (1-20) to control how
strong the AI plays - this can be changed mid-game. You can also switch
which colour you play (`POST /api/set-color`), and let the AI play itself
via the AI-vs-AI controls.

## Move quality

Every half-move - yours and the AI's - is graded chess.com style and shown
two ways: a colored badge on the square the move landed on, and the same
annotation glyph beside the move in the Moves list.

| Grade | Glyph | What it means |
|---|---|---|
| Brilliant | `!!` | A sound sacrifice - material lost on the exchange, yet still (essentially) the best move |
| Great | `!` | The only move that holds the position; everything else drops ~1.5 pawns |
| Best | `★` | The engine's top choice |
| Excellent | `✓` | Within 20 centipawns of best |
| Good | `○` | Within 50 centipawns |
| Book | `📖` | A known opening move |
| Inaccuracy | `?!` | Loses 50-100 centipawns |
| Mistake | `?` | Loses 100-250 centipawns |
| Blunder | `??` | Loses more than 250 centipawns |
| Miss | `✗` | A forced mate was available and went unplayed |
| Forced | `□` | The only legal move - counted, but deliberately not badged |

Toggle it with the **Grade** switch in the Moves panel header. Switching it
off doesn't just hide the badges - it stops the extra Stockfish search that
runs after every half-move, so it's also the setting to reach for if moves
feel sluggish on a slow machine.

Grades are computed in the background and land a moment after the move
itself, so a badge appearing a second late is expected, not a bug. Grading
uses the same search depth as move selection, so the AI can never play a
move its own selector rated best and then have it graded an inaccuracy.

### Review panel

The 🏅 **Review** tab in the icon rail shows **accuracy** for each side, a
breakdown of how many moves fell into each grade, and a **Grade missing
moves** button.

Accuracy is the mean of a per-move score derived from how much win
percentage the move gave up (a logistic curve over the centipawn eval, so a
100cp slip from a level position counts for far more than the same slip
when already winning). Book and forced moves are excluded - neither
reflects a decision made at the board.

**Grade missing moves** exists because grades are only produced as moves are
played: anything played while the toggle was off, or before the server last
restarted, would otherwise stay blank forever. Grades are persisted to
SQLite alongside the rest of the move log.

### Opening book

Openings are recognised from a book built at import time by replaying the
main lines in `OPENING_LINES` (`move_quality.py`) with python-chess - no
Polyglot file to ship. Add lines there to widen it.

A move is only *named* when exactly one line in the book plays it from that
position. Most early moves belong to many openings at once, so `1.e4` reads
as a plain **Book** and picks up a name (say, "Two Knights Defense") only
once the line is genuinely distinctive.

## Mid-game AI chat

Ask the AI about the position while you play. Handled by
`gemini_chat_service.py` and exposed at `POST /api/chat`. The chat is
given the live game context (position, move history, whose turn it is),
so it talks about the actual game rather than chess in the abstract.

## Cross-game learning

`learning_service.py` keeps a SQLite database at `data/learning.db`,
created automatically on first run. It records every game and move, then
builds a picture of both your tendencies and the AI's own results
(`get_opponent_summary()`, `get_ai_self_summary()`). That summary feeds
back into move selection via `reweight_candidates()`, and is readable at
`GET /api/learning/summary`.

The database holds your real game history. It is gitignored and should
not be shared or committed.

## Charcoal Press UI

The frontend is a React + TypeScript (Vite) app in the "Charcoal Press"
style. An icon rail switches the side canvas between four sections:

| Section | What it shows |
|---|---|
| Analysis | Stockfish evaluation, ranked candidate moves, the AI's reasoning for its last move |
| Learning | The cross-game learning summary - your patterns and the AI's record |
| Chat | Mid-game conversation with the AI about the current position |
| Board Theme | Board colours and piece sets (see `chess-frontend/src/pieceThemes.tsx`) |

## API endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/api/status` | Current board state, move history, difficulty, chat history |
| POST | `/api/move` | Submit a player move (UCI format, e.g. `e2e4`) |
| POST | `/api/ai-move` | Manually trigger the AI's move |
| GET | `/api/reset` | Reset the game to the starting position |
| POST | `/api/set-color` | Choose which colour you play |
| GET / POST | `/api/difficulty` | Get or set AI difficulty (1-20) |
| GET / POST | `/api/move-quality` | Get or toggle move-quality grading |
| POST | `/api/move-quality/regrade` | Re-grade the current game's moves |
| POST | `/api/chat` | Ask the AI about the current position |
| GET | `/api/learning/summary` | Cross-game learning summary |
| GET | `/api/langflow/initialize` | Force re-initialisation of the Langflow flow |
| POST | `/api/ai-vs-ai/start` | Start AI vs AI play |
| POST | `/api/ai-vs-ai/pause` | Pause AI vs AI play |
| POST | `/api/ai-vs-ai/resume` | Resume AI vs AI play |
| POST | `/api/ai-vs-ai/step` | Play a single AI vs AI move |
| POST | `/api/ai-vs-ai/exit` | Leave AI vs AI mode |

## Project structure

.
├── app.py # FastAPI backend, game/AI orchestration
├── game_logic.py # Chess rules/state via python-chess
├── stockfish_service.py # Ranks legal moves with Stockfish
├── langflow_service.py # Talks to Langflow/Gemini, picks from candidates
├── langflow_config.py # Langflow connection settings
├── gemini_chat_service.py # Mid-game AI chat
├── learning_service.py # Cross-game learning layer (SQLite)
├── move_quality.py # Chess.com-style move grading
├── config.py # Shared constants and messages
├── chess-frontend/ # React + TypeScript frontend (Vite)
├── flows/Chess.json # Langflow flow template (key placeholder only)
├── archive/patches/ # One-off migration scripts, kept for history
├── data/ # SQLite learning DB (gitignored, created at runtime)
├── Dockerfile # Builds frontend + backend into one image
├── docker-compose.yml # Orchestrates the app + Langflow containers
├── .env.example # Copy to .env and fill in your own secrets
└── .env # Your real secrets - never commit or share this


## Security notes

- `.env` holds your real Gemini API key and Langflow credentials. It is
  gitignored, so it never enters version control. **It is not
  automatically excluded from a zip or folder copy** - if you archive or
  share this directory by any means other than git, check that `.env`
  and `data/` are not inside it first.
- `data/learning.db` contains your real game history. Gitignored, and it
  should not be shared either.
- `flows/Chess.json` contains a `__GEMINI_API_KEY__` placeholder, never a
  real key. The real key is substituted in automatically, in-container,
  at startup from your `.env`.
- `docker-compose.yml` sets `LANGFLOW_AUTO_LOGIN=true`, which grants
  unauthenticated superuser access to the Langflow UI. That is fine on
  localhost, but it **must** be turned off before any public or hosted
  deployment.
- `DEBUG` is read from the environment and defaults to `false`, so
  uvicorn auto-reload cannot accidentally ship enabled.

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
