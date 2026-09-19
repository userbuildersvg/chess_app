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
- Zugzwang Pro: a RevenueCat Web Billing subscription (sandbox) with one
  server-enforced gate on the Improvement Profile. See
  [Subscription](#subscription-zugzwang-pro).

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
10. Optionally, open **Settings → Subscription → See plans**, complete a Stripe
    sandbox checkout with a test card, and return to the Improvement Profile
    to show every recurring pattern unlocked.

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

## Subscription (Zugzwang Pro)

Billing is implemented with RevenueCat Web Billing, currently in **sandbox**:

- A Free, signed-in account sees an **Upgrade** link in the header, a
  **See plans** button under Settings → Subscription, and an **Upgrade to Pro**
  card on the Improvement Profile. Each opens RevenueCat's hosted paywall with
  the configured monthly, yearly and lifetime plans; checkout is Stripe's,
  and completes in sandbox with a test card and a valid test identity.
- The backend (`billing_api.py`, `GET /api/billing/status`) verifies the
  `zugzwang_pro` entitlement against RevenueCat's REST API with the secret
  key. The browser's copy of the entitlement is never trusted. A lookup that
  fails reads as Free and is labelled unverified rather than unlocked.
- **One gate is enforced, server-side:** a Free account's Improvement Profile
  returns its strongest recurring pattern in full and the others as locked
  previews (label and counts, no evidence, no practice). Pro returns all of
  them. Nothing is deleted or rewritten; the rows come back whole the moment
  the entitlement does.
- After a purchase the UI reads **Pro active** and offers **Manage
  subscription** (RevenueCat's management URL).

Not done, deliberately: no RevenueCat webhooks (the server caches status for
60 seconds and re-asks after a purchase), and no enforced monthly quotas - the
review, correction and practice counts shown on the plan comparison describe
what each plan includes, and nothing counts against them yet. Privacy,
account deletion and data export are not behind the paywall. Billing is
frozen unless a new bug appears.

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
- Billing runs against RevenueCat's sandbox and Stripe test cards; no live
  payments are taken. There are no webhooks and no enforced usage quotas.
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
| Billing (backend) | `REVENUECAT_ENABLED`, `REVENUECAT_SECRET_KEY`, `REVENUECAT_ENTITLEMENT_ID`, `REVENUECAT_OFFERING_ID` |
| Billing (frontend, `chess-frontend/.env`) | `VITE_REVENUECAT_ENABLED`, `VITE_REVENUECAT_PUBLIC_API_KEY`, `VITE_REVENUECAT_ENTITLEMENT_ID`, `VITE_REVENUECAT_OFFERING_ID` |

Never commit a real `.env` file or secret value. The RevenueCat secret key
belongs to the backend only; the frontend gets the public Web Billing key.
If multiple backends point to the same production database, they must use the
same `APP_MASTER_KEY`. For development, prefer a separate Neon branch or
separate database. `.env.example` and `chess-frontend/.env.example` list the
names.

## Testing and verification

The backend suites are self-contained scripts (each prints a pass count and
exits non-zero on failure) for the learning loop, review imports, profiles,
billing, account security, sandbox chat moves, and deterministic turning-point
detection. Suites that write to the database refuse to run against the
`public` schema; point `DATABASE_SCHEMA` at a disposable name first. Common
commands include:

```bash
.venv/bin/python test_learning_loop.py
.venv/bin/python test_learning_loop_api.py
.venv/bin/python test_billing_api.py
DATABASE_SCHEMA=zwtest_$$ .venv/bin/python test_review_import.py
DATABASE_SCHEMA=zwtest_$$ .venv/bin/python test_profile_mistakes.py
DATABASE_SCHEMA=zwtest_$$ .venv/bin/python test_account_security.py
.venv/bin/python test_sandbox_chat_moves.py
.venv/bin/python test_turning_point.py

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
node tools/verify/billing.mjs   # makes a real sandbox purchase on a throwaway account
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

RevenueCat Web Billing is integrated in sandbox - hosted paywall, Stripe test
checkout, server-side entitlement verification and one enforced Pro gate on
the Improvement Profile - as described under
[Subscription](#subscription-zugzwang-pro). No live payments are taken.

Suggested demo story:

> Most chess apps show the engine move. Zugzwang remembers why you keep making
> the same mistakes.

This repository does not claim Shipaton eligibility, placement, or award
status.

## License

MIT - see [LICENSE](LICENSE).
