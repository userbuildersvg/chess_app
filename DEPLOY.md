# Deploying Zugzwang

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

**The Gemini key that leaked into the logs still has not been rotated** (§8 of
CLAUDE.md). Unchanged by this work and still worth doing.


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

1. **Storage is persistent.** `data/accounts.db` is on Render's ephemeral
   disk. As it stands, a redeploy deletes every account. This is the blocker.
2. **There is a password reset.** There is none. A forgotten password
   currently means a lost account with no recovery path.
3. **HTTPS is enforced end to end**, and `COOKIE_SECURE=true` is set.
4. **The Gemini key is rotated**, since accounts mean strangers spending it.
5. **`LANGFLOW_AUTO_LOGIN=true` is gone from `docker-compose.yml`** — it grants
   unauthenticated superuser access to Langflow (CLAUDE.md §8).

Deliberately not built, and each a real requirement for a public launch: email
verification, password reset, OAuth, account deletion, and any notion of roles.
