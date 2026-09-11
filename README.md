# Zugzwang

**A chess coach you play against.** Stockfish ranks the legal moves in a
position and hands over a short list of reasonable ones; Gemini picks one from
that list and explains why, in its own words. The engine keeps the moves honest,
and the model does the part an engine cannot: tell you what it was thinking.

**Status: Beta 1, closed.** The app is live at
<https://chess-app-rho-swart.vercel.app> behind an invitation gate. Every API
route answers 403 until an invitation code has been redeemed, and the browser
draws a landing page instead of the board. Codes are minted from a shell by
the maintainer; there is no self-service signup.

## How the AI plays

Instead of Stockfish playing its own top move, or Gemini inventing one (and
risking an illegal or hallucinated move), the two are combined:

1. Stockfish ranks **every legal move** in the position, best to worst, in a
   two-stage search (a shallow pass to order, a deeper pass to refine).
2. A 3-move window is sliced out of that ranked list according to the
   **difficulty** (1–20). Level 20 sits the window at the top of the list;
   level 1 sits it at the bottom - still 100% legal, just deliberately weak.
   The slider names five bands: Beginner, Casual, Club, Strong, Merciless.
3. The cross-game learning layer may reweight that shortlist based on how
   games against real players have gone.
4. Gemini is shown only that shortlist and picks one move, with a short
   explanation that becomes a turn in the chat.
5. The pick is validated against the shortlist. If Gemini fails, times out or
   answers off-list, the app plays the best move inside the same difficulty
   window - the game never stalls and never plays an invalid move.

A second model is asked in parallel if the first has not answered within
~1.4 s, so one hung model does not cost the whole timeout. Median AI-move
latency is under two seconds.

## The three modes

One header control switches between them. All three stay mounted, so
switching modes never loses the state of the other two.

| Mode | What it is |
|---|---|
| **Play** | A real game against the coach. Click or drag to move. Every half-move - yours and the AI's - is graded chess.com style (Brilliant, Great, Best, Excellent, Good, Book, Inaccuracy, Mistake, Blunder, Miss), with a vertical eval bar beside the board, and a chat about the position you are in. The AI's explanation of each of its moves is a message in that chat, so "why?" three moves later still has its subject. |
| **Learn** | A sandbox. Describe a position in plain language ("a rook endgame where White is a pawn up"), get a legal one, and try ideas out with a coach narrating and answering questions. Or paste a FEN or PGN. The AI can play both sides to demonstrate a line; taking over the board branches from that point, and every position explored stays reachable in the **Line** tab. Never touches the real game or the learning database. |
| **Review** | Bring a game you played somewhere else as a PGN (drag-and-drop, file picker, or paste). The whole game is scanned in the background on import; every ply is navigable and graded, the **Report** tab shows the eval curve, accuracy for each side and the turning points, and any position can be branched from to play what you wish you had played - the engine answers at full strength. The **Chat** tab is a coach holding the engine's evidence for the position on the board. |

Inside Review, the **Correct** tab (visible once a game is loaded and the board
is on a move) is the learning loop: say what you were trying to do, get an
evidence-grounded diagnosis filed under one of eight themes, play the better
move on the real board, then take one fresh certified position that tests the
same idea.

**Improvement Profile** (`/profile`, accounts only): import a library of PGNs,
let the server scan them one at a time, and get a profile of what keeps
happening across games - "you consistently…" with the evidence behind it,
rather than "in game 7". Reached from the link at the bottom of Review.

Every mode has a **Board** tab for piece set and colours, an **Actions** tab
for the occasional controls (reset, AI colour, AI-vs-AI, grading toggle,
board size), and a promotion picker. Light and dark themes follow the system
or a toggle.

## Accounts, guests and data

- **Guests** get their own board and their own history, keyed on an anonymous
  cookie. Unclaimed guest games are kept for 30 days and then swept.
- **Accounts** (email + password; Google sign-in is built but not switched on)
  own their games, moves, imported library and board preferences until the
  account is deleted, which removes all of it immediately. A guest who signs
  up takes their games with them.
- Passwords, sessions, reset links and invitation codes are stored **hashed
  only**.
- Corrections, sandbox sessions and Review workspaces live **in memory** on
  the server and vanish on a restart. The app says so on `/about` and
  `/settings` rather than implying more permanence than it has.
- Email addresses are collected but **not verified**. Password reset by email
  is built, and reports itself unavailable when the mail provider is off.

## Architecture

```
browser ──▶ Vercel (static React bundle)
              └── /api/* rewritten server-side to ──▶ Render (FastAPI, Docker)
                                                        ├── Stockfish (in the image)
                                                        ├── Gemini (one pooled HTTPS connection)
                                                        └── Neon Postgres (accounts, games, library)
```

The browser only ever talks to the Vercel origin, so cookies are first-party
and no CORS is involved in normal use.

- **Backend:** Python 3.11, FastAPI, python-chess, httpx, psycopg. Middleware
  stack: security headers, request-body ceiling, CSRF origin check, per-IP rate
  limits on every route that spends money or CPU, identity cookie, CORS, and
  the beta gate (deny by default - a new route is closed on the day it is
  written).
- **Frontend:** React 19 + TypeScript + Vite, React Router. Design tokens in
  `chess-frontend/src/styles/obsidian.css`, shared layout in `shell.css`.
- **Six Gemini model chains**, one per job (move, chat, narration, scenario,
  sandbox coach, review coach), each leading with a different model so one
  job's traffic cannot rate-limit another's.
- **Build id:** `/api/health` reports `version` (the deployed commit), and the
  account pages' footer shows the frontend's copy. Frontend and backend deploy
  separately; comparing the two is how to tell whether they are in step.

## Running it locally

Development happens on a **dev stack** (Vite on `:3001`, uvicorn on `:8081`,
live source with HMR). A **Docker stack** (`:3000` / `:8080`, source baked
into the image) exists to verify what will ship. They are independent.

### Prerequisites

- Python 3.11+, Node 20+, and Stockfish on the path the code expects
  (`/usr/games/stockfish` - `apt install stockfish`)
- A Gemini API key: <https://aistudio.google.com/apikey>
- A Postgres database (a free Neon project works) for accounts and cross-game
  learning. Without `DATABASE_URL` the app still plays chess; accounts and
  history have nowhere to go.

### Configure

```bash
cp .env.example .env
```

Fill in at least `GEMINI_API_KEY`, `DATABASE_URL` and `SESSION_COOKIE_SECRET`
(`openssl rand -hex 32`). `.env.example` documents every other variable.

**The gate is closed by default.** To work on the board locally without
minting a code, export `BETA_ACCESS_REQUIRED=false` in the backend's
environment. Only that literal value opens it, and the startup log says which
state it is in on every boot.

### Dev stack

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
set -a; . ./.env; set +a
BETA_ACCESS_REQUIRED=false DISABLE_LANGFLOW=true \
  .venv/bin/uvicorn app:app --host 0.0.0.0 --port 8081 --no-proxy-headers
```

```bash
cd chess-frontend && npm install
VITE_PROXY_TARGET=http://localhost:8081 npm run dev -- --port 3001
```

Open <http://localhost:3001>. If the source is on a Windows mount under WSL,
set `VITE_POLL=1` too - inotify does not fire for Windows-side writes.

### Docker stack

```bash
docker compose up -d --build        # http://localhost:3000, API on :8080
docker compose --profile langflow up -d   # optional; not used by the move path
```

Migrations run themselves on boot. `./data` is the only bind mount.

## Deployment

`master` is the deployed branch. A push to it deploys **both** halves
automatically, with no confirmation step:

| | |
|---|---|
| Frontend | Vercel, root directory `chess-frontend`, `vercel.json` rewrites `/api/*` to the backend |
| Backend | Render, `render.yaml` blueprint, `Dockerfile.backend` (Python + Stockfish only, ~440 MB), free plan, `/api/health` as the health check |
| Database | Neon Postgres, `DATABASE_URL` set in the Render dashboard |

`render.yaml` declares every environment variable the service needs and
explains each one. Secrets are `sync: false` and live only in the dashboard.
`DEPLOY.md` has the click-path, the known limits of the free tier (instances
sleep after ~15 min idle; in-memory state dies with them; one instance only),
backup and recovery, and what has to be configured by a human for Google
sign-in and transactional email.

### Invitation codes

```bash
set -a; . ./.env; set +a
python tools/beta_codes.py generate 5 --by you --days 30   # mint and print once
python tools/beta_codes.py list --available
python tools/beta_codes.py usage
python tools/beta_codes.py disable ZG-BETA-XXXX-XXXX
```

The database holds keyed hashes, not codes. `BETA_CODE_PEPPER` must be the
same value locally and on Render, and must never change - rotating it
invalidates every outstanding invitation irrecoverably. There is no admin
endpoint, and the CLI is deliberately not copied into the image.

## Tests

**1388 checks across 18 suites**, plus browser-driven invariant suites in
`tools/verify/` (UI, interaction, board state, chat, account lifecycle, the
beta gate, the profile workflow). Each suite is a plain script:

```bash
set -a; . ./.env; set +a
export DATABASE_SCHEMA="zwtest_$$"          # disposable schema, never public
python -u test_security.py                  # no engine, no database, seconds
DISABLE_LANGFLOW=true python -u test_sandbox_api.py
DISABLE_LANGFLOW=true python -u test_accounts_postgres.py
# ... see CLAUDE.md §6 for the full list
python -c 'import db; db.drop_schema()'     # not optional
```

Suites that drive the app set `BETA_ACCESS_REQUIRED=false` at the top; any
new one must too, or the gate answers 403 before the route under test runs.
Frontend typecheck: `cd chess-frontend && npx tsc -b --force`.

## Project structure

```
app.py                     FastAPI app, real-game endpoints, decide_ai_move()
player_state.py            per-player game state (pure)
identity.py                who is asking - the cookie-borne identity
stockfish_service.py       shared engine, staged search, ranking
move_quality.py            chess.com-style grading + the opening book
gemini_*.py                move selection, chat, narration; gemini_http.py pools the connection
engine_evidence.py         the engine's opinion, rendered for a model to quote
learning_service.py        cross-game learning
sandbox_*.py, scenario_service.py      Learn: move tree, sessions, NL -> legal position
postmortem_*.py            Review: PGN ingestion, whole-game scan, branches, API
learning_loop*.py, diagnosis_service.py, retest_bank.py   the Correct tab
pattern_detectors.py, profile_*.py     the Improvement Profile
auth_*.py, settings_service.py, email_service.py, google_oauth.py   accounts
beta_service.py, beta_gate.py, beta_api.py   the closed beta
security_headers.py, csrf.py, body_limit.py, rate_limit.py   request hygiene
db.py, migrations/         Postgres, migrated on boot
chess-frontend/            React + TypeScript (Vite)
  src/components/          ChessBoard, Sandbox, PostMortem*, BetaGate, ...
  src/pages/               About, Settings, sign-in/up, password reset, profile, beta landing
  src/styles/              obsidian.css (tokens), shell.css (layout)
tools/beta_codes.py        invitation admin CLI
tools/verify/*.mjs         browser invariant suites
test_*.py                  the 18 backend suites
Dockerfile.backend         what Render runs
Dockerfile, docker-compose.yml   the local all-in-one build
render.yaml                Render blueprint
DEPLOY.md                  deployment runbook
CLAUDE.md / AGENTS.md      the full engineering record; read before changing anything
OBSIDIAN_DESIGN.md         the design system; read before touching CSS
```

## Security notes

- `.env` holds real secrets and is gitignored. It is **not** excluded from a
  zip or folder copy - check before sharing the directory any other way.
- Never print `GEMINI_API_KEY` (and beware `${VAR:-...}` fallbacks, which
  print the value when you meant to test for it). It leaked into logs once via
  request logging; that is fixed and the key was rotated.
- Shipping CSP has no `unsafe-inline`; HSTS is production-only; cookies are
  `SameSite=Lax; Secure` in production; `/docs` is off in production.
- Rate limits count back `TRUSTED_PROXY_HOPS` entries from the right of
  `X-Forwarded-For`, and uvicorn runs with `--no-proxy-headers`, so a caller
  cannot pick their own bucket. `RATE_LIMITS_ENABLED=false` is dev-only.
- Imported PGNs are stored outside the tables that feed the AI's candidate
  pool, so nobody can steer what the coach plays against everyone else by
  importing games.

## Troubleshooting

- **The board will not load; the API answers 403.** The beta gate is doing
  its job. Redeem a code, or run the backend with `BETA_ACCESS_REQUIRED=false`.
- **The AI says "Stockfish-calculated move (no Gemini API key configured)".**
  Gemini is out of the loop - the key is missing or every model in the chain
  failed. That is a degraded build, not the product. Check the startup log.
- **First move hangs on the deployed site.** The free Render instance was
  asleep; it takes up to a minute to wake. Refresh.
- **Review says "Not Found" on import.** The frontend deployed and the
  backend did not, or vice versa. Compare `version` in
  `https://zugzwang-api.onrender.com/api/health` with the footer on `/about`.
- **Vite does not see edits.** Source on a Windows mount, dev server in WSL:
  set `VITE_POLL=1`.
