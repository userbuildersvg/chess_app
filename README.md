# Zugzwang

**The chess coach that remembers why you keep making the same mistakes.**

## Product

Zugzwang turns a player's own games into a learning loop. Players can play or
import games, review the decisions that mattered, explain what they intended,
and turn the gap between intention and position into a saved Correction Card.
Over time, those corrections form an Improvement Profile that shows which
mistakes are recurring and whether they are becoming less frequent.

The product separates book and forced moves from decisions worth judging. When
enough position evidence exists, it can also create or route the player to a
practice/re-test step. Practice is an evidence-dependent part of the loop, not
a promise attached to every correction.

## Why this exists

Most chess tools are good at saying which engine move was best. That does not
always explain why a player keeps making the same kind of mistake.

Zugzwang focuses on the part between engine output and behavior change:

- the player's intention;
- the decision pattern behind the move;
- similar mistakes across multiple games;
- a correction the player can understand, save, and revisit.

It is not a Stockfish wrapper with a chat box. The engine establishes the chess
facts; the coach connects those facts to the player's choices and history.

## Core loop

1. Play or import a game.
2. Analyze the game.
3. Identify meaningful decisions.
4. Ask what the player was trying to do.
5. Generate a Correction Card.
6. Save the correction to the Improvement Profile.
7. Practice or re-test when a suitable position is available.
8. Track recurrence in future games.

The long-term product metric is not how many engine lines are displayed. It is
whether the player's recurring mistakes decrease over time.

## Key features

- Play mode with AI opponent profiles and optional Guided Play prompts.
- Manual PGN review and public-game import from Chess.com and Lichess.
- A post-game report with full-move coverage, judged-move coverage, and clear
  book/forced-move exclusions.
- Engine-depth caveats where analysis confidence needs context.
- A Correct flow that captures the player's intention before explaining the
  gap between the idea and the position.
- Correction Cards saved to an account's Improvement Profile.
- Recurring-mistake evidence with direct handoff back to the source game.
- Review chat support for questions such as “Where did I start losing?”, with
  deterministic turning-point evidence and board navigation to the position.
- A Learn/Sandbox board where chat can explain a position and execute legal
  moves on the board.
- Account persistence, a closed-beta access gate, and admin invite/overview
  tools.
- Encryption for account-owned chess and correction data.

Zugzwang does not provide real-time assistance in rated games.

## Demo flow

For a focused product demo:

1. Sign in with a demo or burner account.
2. Open a prepared imported game.
3. Review the **Your biggest learning opportunity** card.
4. Choose **Work through this decision**.
5. Select what the player intended.
6. Generate and save the Correction Card.
7. Open the Improvement Profile and show its source-game evidence.
8. Return to Review and ask, “Where did I start losing?”
9. Show the board moving to the critical position while the coach explains it.

The best demo follows one clean correction story rather than trying to show
every feature.

## Architecture

| Layer | Current implementation |
| --- | --- |
| Frontend | React 19, TypeScript, and Vite |
| Backend | FastAPI on Python 3.11 |
| Chess rules | `python-chess` |
| Engine | Stockfish |
| AI | Gemini for bounded explanation, narration, and move choice where applicable |
| Persistence | PostgreSQL, deployed with Neon |
| Hosting | Vercel frontend and Render backend |
| Validation | Pytest backend suites and Playwright-based browser verifiers |

Authentication, account persistence, beta access, encrypted account-owned
data, and admin invites are handled by the backend. The browser uses the
frontend origin; Vercel rewrites `/api/*` requests to the Render service.

## AI/engine boundary

Zugzwang keeps chess facts and natural-language coaching separate:

- Stockfish and deterministic analysis provide legal moves, evaluations, best
  moves, centipawn loss, and turning-point evidence.
- Gemini explains those bounded facts in natural language and supplies
  narration or move choice only in the flows designed for it.
- Gemini is not the source of FENs, evaluations, or engine-best moves.
- Review turning-point detection is deterministic first and explanatory second.
- Correction Cards are grounded in the position and engine evidence supplied
  by the application.

This boundary is intentional: conversational output should make verified chess
evidence easier to understand, not replace it.

## Persistence, privacy, and encryption

Signed-in users can persist imported games, corrections, evidence, and profile
progress. Guest correction and review work is session-limited and should not be
treated as durable account history.

Account-owned PGNs, FENs, correction text, intent, evidence, and related
practice text are encrypted with per-account data keys. Those keys are wrapped
by an application master key. Account deletion is available in Settings.

Secrets belong in the deployment environment, never in the repository. If
multiple backend instances use the same production database, they must use the
same `APP_MASTER_KEY`; otherwise existing encrypted account data cannot be
read. For development, prefer a separate Neon branch or a separate database.

## Current limitations

- Practice/re-test is not guaranteed for every Correction Card; it depends on
  having a clean, supported position.
- Recurring profile patterns become useful only after enough games have been
  analyzed and corrections have been saved.
- Imported-game metadata is limited by the source PGN or provider response.
- Zugzwang is in closed beta and its workflows are still being polished.
- RevenueCat and real payment handling are not implemented.
- The product is not intended for real-time assistance in rated games.

## Local development

### Prerequisites

- Python 3.11
- Node.js and npm
- Stockfish available on the system path
- PostgreSQL for account, import, and profile persistence

### Backend

From the repository root:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
set -a
. ./.env
set +a
BETA_ACCESS_REQUIRED=false DISABLE_LANGFLOW=true \
  .venv/bin/uvicorn app:app --host 0.0.0.0 --port 8081 --no-proxy-headers
```

`--no-proxy-headers` is required by the current trust-boundary setup. The API
health check is available at `http://localhost:8081/api/health`.

### Frontend

In another terminal:

```bash
cd chess-frontend
npm install
VITE_PROXY_TARGET=http://localhost:8081 npm run dev -- --port 3001
```

The frontend scripts in `chess-frontend/package.json` also provide `build`,
`lint`, and `preview` commands.

### Docker

The repository also includes Docker Compose configuration:

```bash
docker compose up -d --build
```

## Environment variables

Use local or deployment secret storage. Variable names used by the account,
beta, AI, database, CORS, admin, and email flows include:

| Category | Variable names |
| --- | --- |
| Database and encryption | `DATABASE_URL`, `APP_MASTER_KEY` |
| Sessions and accounts | `SESSION_COOKIE_SECRET`, `ACCOUNTS_ENABLED` |
| AI | `GEMINI_API_KEY` |
| Beta access | `BETA_ACCESS_REQUIRED`, `BETA_CODE_PEPPER` |
| Browser and proxy trust | `ALLOWED_ORIGINS`, `FRONTEND_URL`, `TRUSTED_PROXY_HOPS` |
| Administration | `ADMIN_EMAILS`, `ADMIN_INVITE_SECRET`, `ADMIN_INVITE_CODE_HASHES` |
| Email | `MAILJET_API_KEY`, `MAILJET_SECRET_KEY`, `MAILJET_FROM_EMAIL` |

Never commit a real `.env` file or secret value. If multiple backends point to
the same production database, they must use the same `APP_MASTER_KEY`. For
development, prefer a separate Neon branch or separate database.

## Testing and verification

The repository uses targeted backend tests for the learning loop, review
imports, profiles, account security, sandbox chat moves, and deterministic
turning-point detection. Common commands include:

```bash
.venv/bin/python -m pytest test_learning_loop.py test_learning_loop_api.py
.venv/bin/python -m pytest test_review_import.py test_profile_mistakes.py test_imported_games.py
.venv/bin/python -m pytest test_account_security.py test_sandbox_chat_moves.py test_turning_point.py

cd chess-frontend
npm run build
npm run lint
```

Browser-level verifiers live in `tools/verify/`. Useful end-to-end checks
include:

```bash
node tools/verify/ui.mjs
node tools/verify/profile-loop.mjs
node tools/verify/external-import.mjs
node tools/verify/settings-imports.mjs
node tools/verify/turning-point.mjs
node tools/verify/correction-late-move.mjs
node tools/verify/loop.mjs
```

These are examples of the project's verification paths, not a claim that every
suite passes in every environment without its required services and fixtures.

## Deployment

- The frontend is built by Vercel from `chess-frontend/`.
- Vercel rewrites `/api/*` to the Render-hosted FastAPI backend.
- Render exposes `/api/health` for health checks.
- PostgreSQL/Neon is required for durable accounts, imports, and profiles.
- The backend container installs Stockfish for server-side analysis.
- A paid Render tier is recommended for live demos and judging to avoid
  free-tier cold starts.

See [DEPLOY.md](DEPLOY.md) for the deployment checklist and security-sensitive
configuration guidance. Do not copy production secret values into documentation
or source control.

## Shipaton status

Zugzwang is being prepared for Shipaton as a student-built AI chess learning
product. The current focus is a coherent correction loop and a polished,
reliable demo path.

RevenueCat is being evaluated for a future monetization layer, but it is not
integrated and the application does not currently process payments.

Suggested demo story:

> Most chess apps show the engine move. Zugzwang remembers why you keep making
> the same mistakes.

This repository does not claim Shipaton eligibility, placement, or award
status.

## License

License: not yet specified.
