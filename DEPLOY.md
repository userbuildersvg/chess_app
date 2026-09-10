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
2. Connect `userbuildersvg/chess_app`, branch **`master`**
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
4. Branch: **`master`**
5. Set `VITE_CONTACT_EMAIL` to the address shown on the beta Contact and
   Request access pages
6. Deploy

The API key stays on Render and the frontend never sees it.

---

## 3. Verify

- Open the Vercel URL without cookies; the private-beta landing page should
  render and `/api/health` should report `"beta_required": true`.
- Redeem one invitation; the board should render immediately afterward.
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

**Cookie flags.** `COOKIE_SAMESITE` / `COOKIE_SECURE` override the defaults in
`identity.py`, which are now **`Lax` everywhere**, plus `Secure` whenever
`RENDER` or `PRODUCTION` is set. Getting these wrong does not error — the
cookie is just never returned and boards reset on refresh.

> ⚠️ **This changed.** Production used to send `SameSite=None`, on the belief
> that Vercel and Render are cross-site. They are not: `vercel.json` rewrites
> `/api` server-side, so the browser only ever sees the Vercel origin
> (verified live — `curl -sD- https://chess-app-rho-swart.vercel.app/api/health`
> answers from Vercel with `x-render-origin-server: uvicorn`). `None` bought
> nothing and cost real CSRF protection, because ~20 POST routes take no
> request body and could therefore be driven by a plain HTML form on any
> website. Setting `COOKIE_SAMESITE=none` re-opens that. See CLAUDE.md §28.

**Two new variables, and the deploy wants both set before the push.**

* **`BETA_CODE_PEPPER`** — now declared explicitly in `render.yaml` rather
  than left to fall back to `SESSION_COOKIE_SECRET`. Set the **same value**
  here and in `.env`. See the Closed beta section below for why this stopped
  being optional.
* **uvicorn runs with `--no-proxy-headers`.** Not a variable - it is in
  `Dockerfile.backend`'s CMD, and it must stay there. uvicorn otherwise
  rewrites the client address from `X-Forwarded-For`, which makes
  `request.client.host` caller-controlled and defeats every rate limit in the
  application. An independent audit reproduced unlimited password guessing
  through it. See CLAUDE.md §28.
* **`GOOGLE_REDIRECT_URI`** must be set explicitly if Google sign-in is ever
  switched on. Because of the flag above, the code's fallback now builds an
  `http://` URI behind Render's TLS terminator, and Google refuses a
  `redirect_uri` that does not match what is registered.
* **`TRUSTED_PROXY_HOPS`** — `1` on Render, and declared there. It is how many
  proxies in front of this process append to `X-Forwarded-For`; Render's edge
  is the only one, because `Dockerfile.backend` runs uvicorn directly with no
  nginx of its own. `rate_limit.client_ip()` counts back from the right of
  that header by this many hops. It used to read the **leftmost** entry, which
  is the one the caller writes — so every rate limit in the app, including the
  Gemini-spend, login and beta-redeem buckets, could be sidestepped with one
  header. Raise this only if another appending proxy is genuinely added.

  > ⚠️ **Verify this once, after the first deploy that carries it.** `1` is
  > correct *provided* Render's edge appends exactly one entry, which could not
  > be confirmed from a developer machine - the currently deployed build reads
  > the leftmost entry, so probing production tells you nothing about its hop
  > count. The check is two runs against the deployed API: N+1 failed logins
  > with a FIXED `X-Forwarded-For`, then the same again with a DIFFERENT value
  > each time. **Both must end in 429.** If the rotating run never does, this
  > number is too high for Render and every IP-keyed limit is open. Until that
  > check is run, treat the hop count as declared rather than confirmed.

**The Gemini key has been rotated and this is not outstanding.** CLAUDE.md §1
records the rotation (2026-09-03, and the user has rotated again since); the
leaked value is dead and the httpx logging bug that exposed it was fixed before
that. This line previously claimed the opposite and stayed wrong after the fact,
which led to the user being told to rotate a key they had already replaced —
**check CLAUDE.md §1 before repeating any warning about this key.**


## Closed beta

`render.yaml` sets `BETA_ACCESS_REQUIRED=true`, and the code also defaults to
closed when the variable is absent. Only the literal value `false` makes the
app public. The gate needs the same persistent Neon database as accounts and a
stable HMAC key.

**`BETA_CODE_PEPPER` is required, and this paragraph used to say it was
optional.** The code does still fall back to `SESSION_COOKIE_SECRET`, and
relying on that fallback **cost a deploy**: the two copies of
`SESSION_COOKIE_SECRET` — the one in `.env` and the one in the Render
dashboard — turned out to differ, so every code minted locally was refused in
production and nothing in either place said why. An implicit secret is one
nobody thinks to compare. So set `BETA_CODE_PEPPER` explicitly, set the same
value in both places, and never change it: rotating it invalidates every
outstanding invitation irrecoverably, because the database holds keyed hashes
and not codes.

There is no web admin endpoint. Before pushing to `master` — which starts both
deployments — load the production-equivalent local environment and generate
the invitations that must work on day one:

```bash
cd /mnt/c/Users/David/Documents/chess-app-v3.9
set -a; . ./.env; set +a
/tmp/chessapp/bin/python tools/beta_codes.py generate 50 --by david --out ~/codes.txt
/tmp/chessapp/bin/python tools/beta_codes.py list
/tmp/chessapp/bin/python tools/beta_codes.py usage
```

The database stores keyed hashes, so the generated file is the only copy of
the complete codes and is written with mode `0600`. `disable` prevents a code
from being redeemed again; `revoke` removes an existing tester's grant. They
are separate operations by design.

After deployment, confirm the gate from outside before sharing a code:

```bash
curl -s https://zugzwang-api.onrender.com/api/health
```

The response must contain `"beta_required": true`. A visitor without a grant
must receive 403 from a guarded route such as `/api/status`. Password signup
is guarded until redemption. Password and Google sign-in remain reachable for
returning testers; an unknown Google subject cannot create an account until
the current guest has redeemed an invitation.


## Accounts

Accounts are **on**. `render.yaml` sets `ACCOUNTS_ENABLED=true`, which is the
whole switch, and this release is the account launch. `auth_service.py` and
`auth_api.py` are complete — PBKDF2-HMAC-SHA256 at 600k iterations with
per-user salts, opaque session tokens stored only as hashes,
case-insensitive unique usernames, tight rate limits on both routes.

Two environment variables stop being optional the moment this is true, and
both are already declared in `render.yaml`:

* **`DATABASE_URL`** — accounts have nowhere to go without it. The app still
  boots and still plays chess, but every signup fails.
* **`SESSION_COOKIE_SECRET`** — and it must be *stable*. Change it and every
  guest cookie is invalidated at once, so every visitor becomes a new guest
  and any history waiting to be claimed becomes unclaimable.

For reference, the behaviour when `ACCOUNTS_ENABLED` is anything but `true`
(the state this release leaves behind, and still what the test suite
`test_accounts.py` proves):

* `POST /api/auth/signup` and `POST /api/auth/login` answer **503** with
  "Accounts aren't available yet - you're playing as a guest."
* No account resolver is wired into the identity middleware at all, so there
  is no code path that could turn a cookie into an account.
* The UI still shows **Sign in** and **Create account**; both open a notice
  explaining guest mode. The buttons are deliberately not hidden — the refusal
  is enforced on the server, so hiding them would add nothing but confusion.

### The pre-conditions, and where each one stands

This list was written as "do not switch accounts on until". Accounts are now
on, so it is a status board rather than a gate. Items 3–7 are the honest
statement of what is shipping unmet; see *What is still open with accounts on*
below for the short version.

1. ~~**Storage is persistent.**~~ — **done.** Accounts and cross-game learning
   both live in Neon Postgres now (`db.py`, `schema.sql`), not in SQLite files
   on Render's ephemeral disk, so a redeploy no longer deletes every account.
   What this needs at deploy time: `DATABASE_URL` set on Render to the Neon
   **pooled** connection string, and `psycopg[binary,pool]` in
   `requirements.txt` (it is).
2. ~~**There is a password reset.**~~ — **built.** Mailjet-backed, single-use
   hashed tokens, 45-minute expiry, identical response whether or not the
   address exists, and every session ended on success. What it still needs
   before it works in production is configuration, not code: see *Mailjet*
   below. An account created through Google has no password and is told so by
   email rather than given a reset link.
3. ~~**HTTPS is enforced end to end**, and `COOKIE_SECURE=true` is set.~~ —
   **done.** Vercel and Render both serve HTTPS only, and `COOKIE_SECURE=true`
   is now declared explicitly in `render.yaml` rather than left to
   `identity.py` inferring it. A security flag nobody can confirm by reading
   the blueprint is one that gets changed by accident - and that inference has
   since stopped being available anyway: cookies are `SameSite=Lax` now
   (CLAUDE.md §28), so `Secure` is derived from `is_production()` rather than
   from SameSite, and the explicit declaration is what makes it certain.
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

### What is still open with accounts on

Shipping with these unresolved is a decision, not an oversight. None of them
stops an account from being created, used, or deleted; all of them are
listed here so nobody has to rediscover them from the code.

* **Email addresses are unverified.** Signup takes an address and never checks
  that the person owns it. Two things are refused rather than guessed as a
  result: a Google sign-in whose email matches a password account is refused
  rather than linked, and no password reset can fully trust an address. Both
  unlock together, with verification.
* **Password recovery is unavailable.** The flow is built and correct, but
  Mailjet has the sending account blocked at their end (`mj-0001`), so
  `EMAIL_ENABLED=false` is the intended setting. The endpoint says so, in the
  same words for every address — that uniformity is what stops it becoming a
  way to check whether somebody has an account here, and it must not be
  "improved" into a message shown only when a send was attempted.
* **No hard Gemini spend cap.** `rate_limit.py` caps per IP, which does
  nothing against a distributed caller. The cap that actually bounds the bill
  is the one set in Google AI Studio.
* **`LANGFLOW_AUTO_LOGIN=true` is still in `docker-compose.yml`.** It grants
  unauthenticated superuser access to Langflow. It is behind a compose profile
  that nothing starts by default and Langflow is not deployed to Render at
  all, so it is not exposed by this release — but it is still there.
* **One instance only.** Live game state, sandbox sessions and Post-Mortem
  reviews are in memory. Do not scale the service horizontally.

Deliberately not built, and each a real requirement for a larger launch: email
verification, OAuth against real Google (the code exists and has never run
against it), and any notion of roles.


## The Improvement Profile

Multi-game import and background analysis (CLAUDE.md section 24). What a
deploy needs to know about it:

* **No new environment variables, and no new dependency.** The scan depth is
  the `POSTMORTEM_SCAN_DEPTH` that already exists, and the worker uses the
  Stockfish and Postgres this service already has.
* **Migration `006_improvement_profile.sql`** adds `imported_games` and
  `game_findings`. It runs itself on boot, like the other five.
* **It costs engine time in the background.** One game at a time, yielding
  between every ply so live play is never starved - a move queues behind at
  most one position, never behind somebody's whole library. On a free instance
  a 40-move game is roughly 40 seconds of that background time.
* **It survives the instance sleeping.** Job state is rows, not memory, and
  anything left mid-analysis by a stopped process is requeued at the next boot.
* **It needs an account**, and is the only surface in the app that does. See
  CLAUDE.md section 24 for why that is a data-safety decision rather than a
  business rule.

There is nothing to switch on: it is live wherever `ACCOUNTS_ENABLED` is true
and `DATABASE_URL` is set.


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


## Mailjet: what only you can configure

The password reset flow is finished and switched off by the absence of
credentials - the same pattern as accounts and Google. **With Mailjet
unconfigured, `/api/auth/forgot-password` answers exactly as it does with it
and sends nothing.** That is deliberate (any difference is a way to test
whether an address is registered) and it has a consequence worth stating
plainly: **a missing or wrong credential is a silently broken reset, not a
visible error.** Nothing on screen will tell you. Check the backend log for
`Could not reach Mailjet`, `Mailjet refused the message`, or `accepted the
request but not the message`, and send yourself a test.

### 1. In Mailjet (you, once)

1. **Get the API key pair.** Mailjet → *Account Settings* → **API Key
   Management**. You need **both halves**: the API key (public) and the secret
   key. Mailjet authenticates with HTTP Basic - key as username, secret as
   password - which is why this is two variables rather than one token. The
   secret is shown once; regenerate it if you lose it.

2. **Validate the sender.** Mailjet → *Account Settings* → **Sender domains &
   addresses** → Add. Two options:

   - **A single address** - Mailjet emails it a confirmation link. Fastest,
     and enough to test the whole flow.
   - **A whole domain** - Mailjet gives you SPF and DKIM DNS records to add at
     your registrar. Slower, and what you want for real users: mail from a
     validated domain is far less likely to be filtered, and you can then send
     from any address on it.

   **Mailjet has no shared sandbox sender.** Unlike some providers there is no
   built-in test address to fall back on, so nothing sends at all until this
   step is done. Do it before wiring anything up.

3. Note the exact address you validated. It has to match `MAILJET_FROM_EMAIL`
   character for character, or every send is refused.

### 2. Local (you)

The credentials must not go through a chat window, a commit, or a command
someone else can read out of your shell history. Type them into the file:

```bash
cd /mnt/c/Users/David/Documents/chess-app-v3.9
cat >> .env <<'EOF'
MAILJET_FROM_EMAIL="no-reply@your-validated-domain.com"
MAILJET_FROM_NAME="Zugzwang"
FRONTEND_URL="http://localhost:3001"
RESET_LINK_TO_LOG=true
EOF

# Opens the file so you can paste the pair on their own lines, quoted:
#     MAILJET_API_KEY="..."
#     MAILJET_SECRET_KEY="..."
${EDITOR:-nano} .env
```

Quote every value - `.env` is read by `set -a; . ./.env`, and an unquoted
value containing `&` breaks the whole file. Then restart the backend.

`RESET_LINK_TO_LOG=true` prints the reset link to the backend log so the flow
can be walked without sending anything. It is ignored in production and
guarded by `is_production()`. **Turn it off when you are done** - a reset link
in a log is an account takeover for anyone who reads that log.

### 3. Render (you)

Dashboard → the service → **Environment**:

| Key | Value |
|---|---|
| `MAILJET_API_KEY` | the API key |
| `MAILJET_SECRET_KEY` | the secret key |
| `MAILJET_FROM_EMAIL` | the validated sender address |
| `MAILJET_FROM_NAME` | `Zugzwang` |
| `FRONTEND_URL` | your Vercel URL — the reset link points at the **frontend** |

Do **not** set `RESET_LINK_TO_LOG` in production. It is ignored there anyway,
and setting it says the wrong thing to whoever reads the config next.

### 4. Vercel

**Nothing.** The reset link is a frontend route (`/reset-password?token=…`),
already covered by the catch-all rewrite in `vercel.json`.

### Testing it

**Locally, without sending anything** - `RESET_LINK_TO_LOG=true` is enough:

```bash
curl -s -X POST http://localhost:3001/api/auth/forgot-password \
  -H 'Content-Type: application/json' -d '{"email":"you@example.com"}'
grep "password reset link" /tmp/zw-backend.log | tail -1
```

**Locally, with real delivery** - create an account whose email is an address
you can read, request a reset, and watch for it. Then check the backend log:
silence means Mailjet accepted the message; a warning names which of the three
failure shapes it was.

**In production** - the same, against the Vercel URL. Mailjet's dashboard has
a **Statistics → Messages** view showing every message and its delivery state,
which is the fastest way to tell "we never sent it" from "we sent it and it
bounced".

### When Mailjet is unavailable — and why that must not block anything

Password reset by email is the **only** thing that stops working. Accounts,
sign-in, Google, the guest claim, settings, ownership and every other part of
the account system are completely unaffected, and this is deliberate: an
external provider being unhappy is not a reason to hold back the account
system.

Three states, all handled:

| State | `EMAIL_ENABLED` | Credentials | What happens |
|---|---|---|---|
| Not set up yet | unset | missing | `/api/health` → `email: "not_configured"`, startup logs a warning, reset endpoint says reset-by-email is unavailable |
| Deliberately off | `false` | present | `email: "disabled_by_config"`, same user-facing message, **no API call attempted** |
| Working | unset | present | `email: "ok"`, links are sent |

**If Mailjet blocks or suspends the account**, which is routine for new
accounts under review, set `EMAIL_ENABLED=false` and redeploy. Without it
every reset request spends up to ten seconds waiting for a provider that is
going to refuse, and the person is told to check an inbox that will receive
nothing. With it, they are told the truth immediately.

A 401 from the send endpoint is logged as an ERROR naming both possibilities,
because **the account API answering 200 does not rule out a blocked account** —
that is exactly how it presented here: credentials verified fine, senders
listed as Active, and `/v3.1/send` returned
`mj-0001: "Your account has been temporarily blocked."`

The user-facing message when email is off is honest *and* still uniform: every
address gets it, registered or not, because it depends on this deployment's
configuration rather than on the address. What must never be built is the
reverse — an "email is down" message shown only when a send was actually
attempted, which would be precisely the enumeration oracle the generic message
exists to close.

### What the flow does

- `secrets.token_urlsafe(32)` — 256 bits. **Only its SHA-256 is stored**, so a
  database dump hands over no working links.
- 45 minutes (`RESET_TTL_SECONDS`).
- **Single use**, and asking again invalidates the earlier link, so two live
  links for one account never exist.
- Unknown, expired and already-used tokens all get **one message** — telling
  them apart tells someone probing which guesses were once real.
- On success: password changed, token spent, and **every session for the
  account ended**. Someone resetting a password usually thinks another person
  is in their account.
- The reset does **not** sign you in. The token arrived by email, and email is
  not a confidential channel.
- Two rate limits: per caller, and **per target address**, because the per-IP
  one does nothing against a distributed caller pointed at one person's inbox.
  The per-address refusal answers 200, not 429 — a 429 would confirm the
  address is worth hammering.
