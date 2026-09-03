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

**The real game is single-user.** Module-level state in `app.py` means every
visitor shares one board — two tabs already do this locally (§8). Sandbox
sessions are properly isolated; normal play is not. This is the thing to fix
before the link goes anywhere, and `sandbox_state.py` is a working model for
how to do it.

**Free instances sleep.** After ~15 minutes idle, Render spins the service
down and takes the better part of a minute to wake. With the frontend on a
CDN this feels worse than it did as a single deployment: the page paints
instantly and then the first move hangs on a board that looks alive.

**The learning DB resets.** Render's filesystem is ephemeral, so
`data/learning.db` is wiped on every deploy and restart. The app degrades to
"no history yet" rather than failing. A persistent disk fixes it and is paid.

**One instance only.** Sandbox sessions live in memory. Scaling horizontally
breaks both the sandbox and the real game.

**CORS is wide open.** `app.py` sets `allow_origins=["*"]`. The rewrite means
the browser never needs it, so it can be narrowed to the Vercel domain — worth
doing once the URL is known, especially as the Gemini key that leaked into the
logs (§8) has still not been rotated.
