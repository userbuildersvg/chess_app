# Deploying Zugzwang

**Live:** frontend <https://chess-app-rho-swart.vercel.app>, backend
<https://zugzwang-api.onrender.com>. `ALLOWED_ORIGINS` on the Render service
must name that frontend origin — it is set, and verified live (a request
claiming `http://localhost:3001` is refused, so the allowlist is real rather
than falling through to the localhost default).

Backend on Render, frontend on Vercel, one origin in the browser.

Stockfish needs a long-lived process with real CPU, which is why the API
cannot be a serverless function and stays on Render. The frontend is a static
Vite bundle, which is exactly what a CDN is for.

**The browser only ever talks to Vercel.** `chess-frontend/vercel.json`
rewrites `/api/*` to the Render service, so every existing `fetch('/api/...')`
call keeps working untouched and there is no CORS involved at all. The
alternative — an absolute API base plus CORS headers — would have meant
editing every call site in `chessService.ts` and `sandboxService.ts` for a
worse security posture.

---

## 1. Render (do this first — Vercel needs the URL)

The repo has a `render.yaml` blueprint, so Render can create the service
itself.

1. Render → **New** → **Blueprint**
2. Connect `userbuildersvg/chess_app`, branch **`v4.5`**
3. Render reads `render.yaml` and proposes a service called **`zugzwang-api`**
4. It will prompt for **`GEMINI_API_KEY`** — paste it there. It is marked
   `sync: false` precisely so it never lives in the repo.
5. Deploy. First build takes a few minutes (Stockfish + Python deps).

Check the logs for these three lines. If they are missing, Gemini is not in
the loop and the app will silently fall back to engine-only play:

```
🤖 Move selection: Gemini direct (models: gemini-3.5-flash, ...)
🗣️ Sandbox narration: Gemini (models: gemini-3.1-flash-lite, ...)
🎬 Sandbox scenarios: Gemini (models: gemini-flash-lite-latest, ...)
```

Then note the service URL, e.g. `https://zugzwang-api.onrender.com`.

**If the URL is not exactly that**, edit `chess-frontend/vercel.json` and
replace the `destination` host, then commit and push. That value is a
placeholder guess at the name Render will assign.

### Why a separate `Dockerfile.backend`

The root `Dockerfile` is the all-in-one local image: it installs Node 20,
builds the frontend inside the image and runs nginx next to uvicorn. None of
that is wanted when Vercel serves the frontend, and RAM pressure on Render is
what forced Langflow out of the deployment in the first place.

| image | size |
|---|---|
| root `Dockerfile` (all-in-one) | 2.33 GB |
| `Dockerfile.backend` (API only) | 436 MB |

---

## 2. Vercel

1. Vercel → **Add New** → **Project** → import `userbuildersvg/chess_app`
2. **Root Directory: `chess-frontend`** — this is the one setting that is easy
   to miss and the build fails without it
3. Framework preset picks up Vite; `vercel.json` supplies the build command,
   the output directory and the rewrite
4. Branch: **`v4.5`**
5. Deploy

No environment variables are needed on Vercel. The API key stays on Render,
and the frontend never sees it.

---

## 3. Verify

- Open the Vercel URL; the board should render.
- Play a move. The **first** one will be slow — see cold starts below.
- Learner Mode → **Chat** → ask "why not 1.e4 here?". A reply that quotes a
  centipawn number proves the whole chain is live: Vercel → rewrite → Render →
  Stockfish → Gemini.

---

## Known limits of this deployment

None of these are silent; all are worth knowing before sharing the URL.

**The real game is no longer single-user.** This was the blocker; it is
fixed. `app.py`'s eleven module-level globals moved onto a `PlayerSession`
(`player_state.py`) keyed by an identity cookie (`identity.py`), so every
visitor gets their own board, difficulty, eval bar, chat transcript and
grading state. Sandbox sessions are now *owned* as well as isolated: a
session id belonging to somebody else answers 404. Verified live with two
cookie jars and covered by `test_accounts.py`.

**Free instances sleep.** After ~15 minutes idle, Render spins the service
down and takes the better part of a minute to wake. With the frontend on a
CDN this feels worse than it did as a single deployment: the page paints
instantly and then the first move hangs on a board that looks alive.

**The learning DB resets — and guests never touch it anyway.** Render's
filesystem is ephemeral, so `data/learning.db` is wiped on every deploy and
restart. It matters less than it did: while accounts are off, every visitor is
a guest, and a guest's learning layer is an in-memory database of their own
(`guest_learning.py`) that never touches the file. The shared database only
comes into play once accounts are switched on — at which point the ephemeral
disk becomes a real problem for `accounts.db` too, and a persistent disk (paid)
or a hosted Postgres is required before anyone is asked to create an account.

**One instance only.** Sandbox sessions live in memory. Scaling horizontally
breaks both the sandbox and the real game.

**CORS is now an allowlist, and it has to be.** `app.py` used to set
`allow_origins=["*"]` alongside `allow_credentials=True`. That pairing is not
permissive, it is broken: browsers reject a credentialed request whose response
echoes `*`, silently, so the identity cookie would simply never arrive and every
request would look like a new visitor. Origins are now explicit — set
`ALLOWED_ORIGINS` on Render to the Vercel URL (comma-separated if there is more
than one). Leaving it unset falls back to localhost only. The Vercel rewrite
means the browser usually sees one origin and never exercises CORS at all, but
the moment anything talks to the Render URL directly, this is what decides
whether it works.

**Cookie flags follow the deployment shape.** `COOKIE_SAMESITE` / `COOKIE_SECURE`
override the defaults in `identity.py`, which pick `Lax` locally and
`None; Secure` when `RENDER` or `PRODUCTION` is set. Getting these wrong does
not error — the cookie is just never returned and boards reset on refresh.

**The Gemini key has been rotated and this is not outstanding.** CLAUDE.md §1
records the rotation (2026-09-03, and the user has rotated again since); the
leaked value is dead and the httpx logging bug that exposed it was fixed before
that. This line previously claimed the opposite and stayed wrong after the fact,
which led to the user being told to rotate a key they had already replaced —
**check CLAUDE.md §1 before repeating any warning about this key.**


## Accounts

Accounts are **built and switched off**. `auth_service.py` and `auth_api.py`
are complete — PBKDF2-HMAC-SHA256 at 600k iterations with per-user salts,
opaque session tokens stored only as hashes, case-insensitive unique
usernames, tight rate limits on both routes. While `ACCOUNTS_ENABLED` is
anything but `true`:

* `POST /api/auth/signup` and `POST /api/auth/login` answer **503** with
  "Accounts aren't available yet - you're playing as a guest."
* No account resolver is wired into the identity middleware at all, so there
  is no code path that could turn a cookie into an account.
* The UI still shows **Sign in** and **Create account**; both open a notice
  explaining guest mode. The buttons are deliberately not hidden — the refusal
  is enforced on the server, so hiding them would add nothing but confusion.

### Before switching them on

Turning accounts on is setting `ACCOUNTS_ENABLED=true`. Do not do that until:

1. ~~**Storage is persistent.**~~ — **done.** Accounts and cross-game learning
   both live in Neon Postgres now (`db.py`, `schema.sql`), not in SQLite files
   on Render's ephemeral disk, so a redeploy no longer deletes every account.
   What this needs at deploy time: `DATABASE_URL` set on Render to the Neon
   **pooled** connection string, and `psycopg[binary,pool]` in
   `requirements.txt` (it is).
2. **There is a password reset.** There is none, deliberately - it was kept
   out of the storage migration as its own task. A forgotten password
   currently means a lost account with no recovery path, and an account
   created through Google has no password at all (which is fine: its recovery
   path is Google). This is now the FIRST blocker, and it needs email
   verification with it - see item 6.
3. **HTTPS is enforced end to end**, and `COOKIE_SECURE=true` is set.
4. ~~**The Gemini key is rotated**~~ — already done (CLAUDE.md §1). What is
   *not* done is a hard spend cap in Google AI Studio, which is the only thing
   that actually bounds the bill once strangers can spend it: the per-IP limits
   in `rate_limit.py` do nothing against a distributed caller.
6. **Email addresses are verified.** Signup accepts an email and never checks
   that the person owns it. Two consequences, both currently handled by
   refusing rather than by guessing: a Google sign-in whose email matches an
   existing password account is REFUSED rather than linked (auto-linking on an
   unverified address is how one person's account gets handed to another), and
   there is no address a password reset could trust. Both unlock together.
7. **`LANGFLOW_AUTO_LOGIN=true` is gone from `docker-compose.yml`** — it grants
   unauthenticated superuser access to Langflow (CLAUDE.md §8).

Deliberately not built, and each a real requirement for a public launch: email
verification, password reset, OAuth, account deletion, and any notion of roles.


## Backups and recovery (Neon)

Account-owned learning history is real user data now, so this is what protects
it and what does not.

### What Neon does automatically

Neon keeps a **write-ahead-log history** for the project and can create a
branch from any point inside that window - which is the restore mechanism:
you branch the database as it was at a timestamp, look at it, and either
promote it or copy rows out. There is no separate "backup file" to manage.

**The length of that window is a plan setting, and it is short on the free
plan.** Check the actual number before relying on it: Neon Console → the
project → Settings → *History retention*. Do not trust a number written here;
it is a setting that can be changed, and the whole point of this section is
not to discover the answer during an incident.

### What it does not protect

- Anything older than the retention window.
- The Neon account itself. If access to it is lost, so is everything in it.
- Deliberate deletion that is noticed late - our own retention sweep removes
  unclaimed guest history after `GUEST_RETENTION_DAYS`, and once it is past
  the WAL window it is gone. That is the intended behaviour, but it means the
  sweep is one of the few things here that destroys data on a timer.

### Manual logical backup

The database is small - games, moves, and a handful of account rows - so a
plain dump is entirely adequate and takes seconds:

```bash
# From the project root, with .env loaded. Uses the DIRECT endpoint, not the
# pooler: pg_dump wants a real session, and the pooler will not give it one.
set -a; . ./.env; set +a
DIRECT=$(python3 -c "import db; print(db.direct_dsn())")
pg_dump "$DIRECT" --no-owner --no-privileges -Fc -f "zugzwang-$(date +%F).dump"
```

Restore into a fresh Neon branch (never straight over a live database, so
that a bad dump cannot make things worse):

```bash
pg_restore --no-owner --no-privileges -d "<new-branch-direct-url>" zugzwang-2026-01-01.dump
```

### Recovery procedure

1. **Stop writing.** Unset `DATABASE_URL` on Render and redeploy, or scale to
   zero. The app keeps serving chess and records nothing, which is the
   designed degradation - see `db.py`. This matters: every minute of writes
   after a data-loss event is a minute of new data a restore would discard.
2. **Establish when.** Find the last known-good timestamp. `games.started_at`
   and `users.created_at` are the practical markers.
3. **Branch, do not overwrite.** Create a Neon branch from that timestamp, or
   a fresh branch and `pg_restore` a dump into it. Inspect it before trusting
   it: row counts in `games`, `users` and `claimed_guests`.
4. **Repoint.** Set `DATABASE_URL` to the recovered branch's pooled string and
   redeploy. Migrations run on boot and are idempotent, so a branch made
   before a migration lands catches up by itself.
5. **Write down what was lost.** Anything between the branch point and the
   incident is gone; claimed history that was re-claimed in that gap will need
   its `claimed_guests` row checked, since a restore can resurrect a guest
   identity that has already been claimed.


## Google sign-in: what is built, and what only you can do

The code is finished and switched off by the absence of configuration - the
same pattern as accounts themselves. `/api/auth/config` reports
`google: false`, the Google button is not drawn, and `/api/auth/google/*`
answers 503. Filling in two environment variables is the whole of enabling it.

**Nothing below asks for a secret in source, and none of it belongs in git.**

### 1. Google Cloud (you, once)

1. <https://console.cloud.google.com> → create or pick a project.
2. **APIs & Services → OAuth consent screen.** External. App name Zugzwang,
   your support email, your developer email. Scopes: leave the defaults -
   `openid`, `email`, `profile` are all this asks for and none of them needs
   verification. While the app is in *Testing*, add your own Google account
   under **Test users** or sign-in will refuse you.
3. **APIs & Services → Credentials → Create credentials → OAuth client ID →
   Web application.**
4. **Authorised redirect URIs** - add both, exactly, no trailing slash:

   ```
   http://localhost:8081/api/auth/google/callback
   https://zugzwang-api.onrender.com/api/auth/google/callback
   ```

   These point at the **API**, not the frontend. The browser goes to Google,
   Google returns it to the backend, and the backend redirects on to the app.
   A redirect URI pointing at Vercel would send the authorization code to a
   place that cannot exchange it.

5. Copy the **Client ID** and **Client secret**.

### 2. Local (you)

Append to `.env` - quoted, because `.env` is read by `set -a; . ./.env`:

```bash
cd /mnt/c/Users/David/Documents/chess-app-v3.9
cat >> .env <<'EOF'
GOOGLE_CLIENT_ID="paste-the-client-id"
GOOGLE_CLIENT_SECRET="paste-the-client-secret"
GOOGLE_REDIRECT_URI="http://localhost:8081/api/auth/google/callback"
FRONTEND_URL="http://localhost:3001"
EOF
```

Restart the backend. The Google button appears on `/signin` and `/signup` by
itself - the UI reads `/api/auth/config` and draws it only when the server
says the flow can work.

### 3. Render (you)

Dashboard → the service → **Environment**:

| Key | Value |
|---|---|
| `GOOGLE_CLIENT_ID` | the client id |
| `GOOGLE_CLIENT_SECRET` | the client secret |
| `GOOGLE_REDIRECT_URI` | `https://zugzwang-api.onrender.com/api/auth/google/callback` |
| `FRONTEND_URL` | your Vercel URL |

`render.yaml` carries these as commented-out `sync: false` entries so the
shape is recorded without the values.

### 4. Vercel

**Nothing.** The flow is entirely server-side; the frontend only links to
`/api/auth/google/start`, which `vercel.json` already rewrites to Render.

### What the code does with what comes back

- Accounts are linked on Google's **`sub`**, never the email address. Emails
  get changed and, inside a workspace, reassigned; linking on one is how an
  account is handed to a stranger.
- A sign-in whose email matches an **existing password account** is
  **refused**, not linked, because password signup does not verify email
  addresses. It unlocks with email verification.
- An account created this way has no password and none can sign into it. The
  refusal costs the same wall-clock time as a wrong password, so it cannot be
  timed.
- The callback checks a `state` cookie. Without it, someone can send your
  browser to the callback carrying *their* authorization code and you end up
  silently signed in to their account.

### What is not tested

The exchange with Google has never run against real Google. Everything after
Google answers is covered by `test_accounts_postgres.py` with the exchange
faked - state checking, account creation, linking, idempotency, CSRF. The
network call itself needs the credentials above and a browser.
