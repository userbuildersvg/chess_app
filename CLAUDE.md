# Zugzwang — Chess AI Platform (V4.5, deployed)

> # 🛑 STOP — DO THIS BEFORE ANYTHING ELSE
>
> ## You MUST ask the user about the post-mortem analytics feature.
>
> **This is not optional, and it is not a suggestion you may weigh against
> whatever the user has just asked for.** Before you start on the task in
> front of you — before planning, before reading further, before the first
> tool call — put this question to the user:
>
> > *"Before we start: do you want to work on the post-mortem analytics
> > feature for Zugzwang? You asked to be reminded about it."*
>
> The user asked, explicitly and emphatically, to be reminded of this every
> time. Their words: **"MANDATORY ask me about adding a post-mortem analytics
> feature to zugzwang, THATS VERY IMPORTANT THAT I GET REMINDED."**
>
> ### Rules for this reminder
>
> - **Ask every session, until the user says it is done or dropped.** It stays
>   here until they tell you to remove it. Do not quietly decide it has been
>   handled.
> - **Ask even if the user's request seems unrelated or urgent.** Asking costs
>   one sentence; not asking is the failure they asked you to prevent.
> - **Do not design it, scope it, or start building it.** Nothing about what
>   this feature *is* has been specified — not what it analyses, not when it
>   runs, not what it shows. Assume nothing. Ask what they want, then work
>   from their answer.
> - **Do not treat their answer as the whole brief.** If they say yes, use
>   `superpowers:brainstorming` before writing code; if they say not now,
>   acknowledge it in one line and get on with their actual request.
>
> **Remove this block only when the user says so, in their own words.**

---

Human-vs-LLM chess coach. **Stockfish proposes, Gemini decides.** Stockfish
ranks legal moves and slices a 3-move window by difficulty; Gemini picks one
from that window and explains it in its own voice. Every half-move is graded
chess.com style, there is a mid-game chat about the position, a Learner Mode
sandbox with its own coach chat, and a SQLite cross-game learning layer.

If you change one thing about how this app works, do not break that
sentence: **Gemini choosing from Stockfish's shortlist is the product.** A
build where Stockfish just plays its own top move is a regression even if
every test passes. It has a visible signature — `source: "stockfish_fallback"`
and the string *"Stockfish-calculated move (no Gemini API key configured)"*.

**State of play:** `master` is **deployed and live** — backend on Render at
<https://zugzwang-api.onrender.com>, frontend on Vercel at
<https://chess-app-rho-swart.vercel.app>. The `impeccable-ui-pass` work is
merged and shipped; there is no undeployed work in flight. Read §0 for what
landed and what is deliberately switched off.

---

## 0. HANDOFF — where the work is right now

Step 1 (wiring the endpoints onto `PlayerSession`) is **done**, and guest mode
and the account UI landed with it. This section is the handoff; everything
below it is reference.

**What now works:** every visitor gets their own board from an identity cookie;
a guest can do everything and saves nothing; accounts are built and answer 503
until `ACCOUNTS_ENABLED=true`. Driven live, not just typechecked.

**On Clerk:** the accounts under the switch are a self-contained
username/password service (`auth_service.py`), not Clerk. **The user was asked
and chose to keep it** — Clerk stays possible, not planned. Don't swap it out
without being asked.

| | |
|---|---|
| **Branch to work on** | `impeccable-ui-pass` |
| **Deployed branch** | `master` — the branch is **9 commits ahead** and **unpushed** |
| **Tests** | **353 across 8 suites, all passing** (§6) + **31/31 UI invariants** (§10) |
| **Docker** | rebuilt from this branch, :3000 / :8080 (§3) |

> ⚠️ **Do not push, merge to master, or deploy without asking.** Master is what
> Render and Vercel serve. The user's plan is "one final big push to Render and
> Vercel" *after* accounts land. Local commits on the branch are expected;
> anything leaving this machine is not.

### The 7 commits, oldest first

`git show` any of these before touching that area — each carries its reasoning.

1. `7bc2fd8` **ui: elevation, coordinate contrast, touch targets, debug
   output.** `--cp-bg` is the page ground and was being used as the fill for
   raised controls *and* sunken wells, inverting the system's one structural
   rule; nine classes measured as painted in exactly the page colour while
   carrying an elevation shadow. Board coordinates were the app's only text
   below AA (3.05:1 dark, 2.35:1 light). `--text-muted` was calibrated only
   against `--surface` and failed on every other surface. 71 `console.log`
   calls removed. 16 touch targets fixed behind `@media (pointer: coarse)`.
2. `85ccb92` **sandbox: one composer that asks and builds** (§8.5).
3. `1995aad` **sandbox: column alignment, a Board tab, reload restores** (§11).
4. `c67c911` **sandbox: the coach's mate lines, one AI control, eval bar,
   alerts.** Contains the most important fix in the batch — §8.6.
5. `8f5e0a0` **sandbox: the eval toggle no longer resizes the layout** (§11).
6. `29ac398` **sandbox: cap the panel's grid track, not the panel** (§11).
7. `2327160` **accounts: each player gets their own game.** `player_state.py`
   + 41 tests. Nothing in `app.py` was wired to it *by this commit*; commit 8
   is what wired it. Design notes: §13.
8. `PENDING` **accounts: guest mode, the identity seam, and accounts behind a
   switch.** Step 1 of the table below, plus the parts the user asked for on
   top of it. `app.py`'s globals are gone; `identity.py` decides who is asking;
   `guest_learning.py` keeps a guest's history off disk; `auth_service.py` /
   `auth_api.py` are real accounts that answer 503 while switched off; the
   header has a working account UI whose entry points explain guest mode.
   CORS narrowed (the old `["*"]` + credentials pairing silently breaks
   cookies). 74 new tests. Details: §13.

### Next, in order

**Before any of this: ask about the post-mortem analytics feature** — the stop
block at the top of this file. It is unscoped and unstarted, and the user wants
to be asked about it every session, ahead of whatever else is queued here.

The user chose all four of these. They are decisions, not suggestions.

> ⚠️ **Clerk was dropped, deliberately.** The accounts behind
> `ACCOUNTS_ENABLED` are a self-contained username/password service written in
> this repo. It was built that way because the Clerk keys had not arrived and
> the ask was for something real that could be switched on later; the user was
> then asked directly and **chose to keep it**. So the Clerk row below is
> history, not a plan.
>
> If it is ever revisited, the swap is small: `identity.py` takes a
> `resolve_account(token) -> "user:<id>" | None` callable and knows nothing
> else, so it means replacing that one function and the sign-in form. Nothing
> else in the app would change.

- **Auth:** Clerk (hosted, native Vercel Marketplace integration).
- **Database:** Neon Postgres via the Vercel Marketplace.
- **Day-one scope:** an account owns its own game and its own history. Not
  saved sandbox positions, not sharing.
- **Gemini cost:** per-user daily caps on the project's own key.

| step | work | blocked on |
|---|---|---|
| ~~1~~ | ~~Rewire the 18 endpoints in `app.py` onto `PlayerSession`~~ | **done** |
| 2 | Port `learning_service.py` to Postgres, add `user_id`, migrate | `DATABASE_URL` |
| 3 | Clerk: verify their JWT server-side, swap cookie identity → user id | Clerk keys |
| 4 | Per-user rate limits and daily Gemini caps | step 3 |

**Step 1 is done.** Step 2 (Postgres) is now the blocker for anything real:
accounts and the learning DB both sit on Render's ephemeral disk.

### What the user still owes you

Asked for, not yet delivered. Don't try to do these for them — they need their
accounts.

```bash
cd chess-frontend && npx vercel link      # interactive login
npx vercel integration add neon           # -> DATABASE_URL
# dashboard.clerk.com -> CLERK_PUBLISHABLE_KEY + CLERK_SECRET_KEY
```

---

## 1. Where you are, and the one thing to know about this copy

- **Working dir:** `C:\Users\David\Documents\chess-app-v3.9` — **still the old
  V3.9 name on disk.** Every path in this file is written against that name,
  which is *true right now*.

  > The folder rename is still the one step left, and it cannot be done from
  > inside a Claude session or while the servers are up — both hold the
  > directory open and Windows refuses with "used by another process". To
  > finish it: close the session, stop both servers, then from PowerShell:
  >
  > ```
  > Rename-Item C:\Users\David\Documents\chess-app-v3.9 chess_app_V4.5
  > ```
  >
  > Then search-and-replace `chess-app-v3.9` → `chess_app_V4.5` across this
  > file and the two runner scripts in §12. Docker renames its compose project
  > on the next `up`; the `./data` bind mount follows the folder, so the
  > learning DB survives.

- **This IS a git repo now.** It was not before — V4.5 was a bare directory
  with no history. `git init` was run here and the tree pushed to the user's
  own repo:

  | | |
  |---|---|
  | remote | `https://github.com/userbuildersvg/chess_app` (private) |
  | `master` | `1559bbe` — the merge commit, **this is what deploys** |
  | `v4.5` | the branch the work landed on first |
  | tag `v3.5-master-pre-merge` | `6a2e48c`, the pre-merge master, recoverable |

  The two histories had no common ancestor, so the merge was built with
  `git commit-tree` recording both parents and V4.5's tree as the result.
  **`~/ai-challenges` is a different, older checkout whose `origin` belongs to
  someone else — never push chess app work there.**

- **`.env` is not in git and must not be.** It holds the live
  `GEMINI_API_KEY`. `.gitignore` and `.dockerignore` both exclude it;
  `.env.example` ships instead. **The key was rotated on 2026-09-03** after
  being echoed into a terminal — the previously leaked one is dead. Never
  print it: no `echo $GEMINI_API_KEY`, and beware `${VAR:-...}` fallbacks,
  which print the value when you meant to test for it.

### File map

| file | role |
|---|---|
| `app.py` | FastAPI app, real-game state, `decide_ai_move()` |
| `game_logic.py` | `ChessGame`: board + flat `game_history` for the real game |
| `stockfish_service.py` | shared engine, staged search, ranking/refining |
| `move_quality.py` | chess.com-style grading + the `OPENING_LINES` book |
| `learning_service.py` | SQLite cross-game learning |
| `rate_limit.py` | per-IP limits on every endpoint that spends money or CPU |
| `gemini_move_service.py` | Gemini picks a move from the shortlist |
| `gemini_chat_service.py` | mid-game chat **and** the sandbox coach chat |
| `gemini_narration_service.py` | sandbox coach narration |
| `scenario_service.py` | NL → validated legal position |
| `sandbox_state.py` | sandbox move tree + sessions (pure) |
| `player_state.py` | **per-player game state (pure)** — one player's board, §13 |
| `identity.py` | **who is asking**, as one opaque cookie-borne string |
| `auth_service.py` | accounts: PBKDF2 hashing, sessions, SQLite. Built, switched off |
| `auth_api.py` | `/api/auth/*`, and the 503 that makes accounts unavailable |
| `guest_learning.py` | a guest's learning DB — in memory, never touches disk |
| `sandbox_api.py` | `/api/sandbox/*` router |
| `chess-frontend/src/styles/obsidian.css` | **the design system — token source of truth** |
| `chess-frontend/src/hooks/useTheme.ts` | light/dark/system preference |
| `chess-frontend/src/hooks/useBoardSize.ts` | how wide the board would *like* to be |
| `chess-frontend/src/hooks/useFittedBoardSize.ts` | how tall it is *allowed* to be — measures, §11 |
| `chess-frontend/src/formatText.tsx` | renders the `**bold**` Gemini emits, both chats |
| `chess-frontend/src/components/ChessBoard.tsx` | the real-game UI |
| `chess-frontend/src/components/Sandbox.tsx` | Learner Mode |
| `chess-frontend/src/components/ThemeToggle.tsx` | the theme switch |
| `chess-frontend/src/components/AccountMenu.tsx` | the account UI + the unavailable notice |
| `chess-frontend/src/services/authService.ts` | client for `/api/auth/*` |
| `chess-frontend/src/services/http.ts` | `apiFetch` — carries the identity cookie, §13 |
| `chess-frontend/src/components/EmptyState.tsx` | empty/loading panel states |
| `OBSIDIAN_DESIGN.md` | **read before touching any CSS** |
| `DEPLOY.md` | Render + Vercel click-path and known limits |
| `tools/verify/ui.mjs` | **31 frontend invariants against the running app** (§10) |

---

## 2. Deployment (live)

| | |
|---|---|
| Frontend | **https://chess-app-rho-swart.vercel.app** — Vercel, root directory **`chess-frontend`**, branch `master` |
| Backend | Render, `zugzwang-api.onrender.com`, Docker, `plan: free` |
| Blueprint | `render.yaml` |
| Backend image | `Dockerfile.backend` — **436 MB**, vs 2.33 GB for the all-in-one |

**The browser only ever talks to Vercel.** `chess-frontend/vercel.json`
rewrites `/api/*` to the Render service, so every existing relative
`fetch('/api/...')` keeps working and **there is no CORS involved**. Do not
"fix" this by introducing an absolute API base — that means editing ~20 call
sites in `chessService.ts` and `sandboxService.ts` for a worse security
posture.

`Dockerfile.backend` exists because the root `Dockerfile` installs Node 20,
builds the frontend and runs nginx. None of that belongs on Render when Vercel
serves the frontend, and RAM pressure is what forced Langflow out of the
deployment in the first place.

### What "live" does and does not mean

- **Render free instances sleep** after ~15 min idle and take ~a minute to
  wake. The page paints instantly from Vercel's CDN and then the first move
  hangs on a board that looks alive. Warm it before a demo.
- **Every sleep wipes state.** The filesystem is ephemeral, so
  `data/learning.db` resets and every in-memory sandbox session dies.
- **One instance only.** Sandbox sessions are in-process. Do not scale
  horizontally without moving session state out.
- **`GEMINI_API_KEY` is set in the Render dashboard**, marked `sync: false` in
  `render.yaml` so it never enters the repo. It is easy to skip at Blueprint
  creation — that happened once, and the app silently degraded to
  Stockfish-only. Check the startup lines in §4.

---

## 3. Run it locally

Everything runs in **WSL Ubuntu** (no Python or Node on the Windows side).
Stockfish is at `/usr/games/stockfish`, the path the code expects.

There are **two** local stacks and they are independent:

| | dev | docker |
|---|---|---|
| frontend | `:3001` (Vite) | `:3000` (nginx in container) |
| backend | `:8081` (uvicorn on host) | `:8080` (in container) |
| source | live host files, HMR | **baked into the image** |
| use for | all development | verifying the shipped build |

```bash
# dev stack
setsid nohup /tmp/run_backend.sh > /tmp/backend.log 2>&1 < /dev/null & disown; sleep 8; pgrep -af 'port 8081'
setsid nohup /tmp/run_frontend.sh > /tmp/frontend.log 2>&1 < /dev/null & disown; sleep 12; curl -s -o /dev/null -w '%{http_code}\n' http://localhost:3001/

# docker stack (image zugzwang:v4.5)
docker compose up -d --build
```

**Ports are non-default on purpose.** 8080 is held by the container and 3000
was taken. Don't "fix" this by killing Docker.

Python venv: `/tmp/chessapp`. Logs: `/tmp/backend.log`, `/tmp/frontend.log`.

### Rebuilding after a WSL restart

**`/tmp` is wiped when WSL restarts** — venv, both runner scripts and both
servers vanish at once.

```bash
cd /mnt/c/Users/David/Documents/chess-app-v3.9 && python3 -m venv /tmp/chessapp && /tmp/chessapp/bin/pip install -q -r requirements.txt
```

then recreate the runner scripts (§12). Stockfish is a system package and
survives.

### Docker compose, as it now stands

- `chess-ai-platform` runs the **baked image**. The old `./:/app` bind mount
  and `uvicorn --reload` were removed: with them the container never actually
  ran its own image — backend from the host, frontend baked at build time,
  free to drift apart. **Only `./data` is mounted**, so the learning DB stays
  a real host file that survives `docker rm`.
- **Langflow is behind a compose profile**, not deleted. V4.5 does not use it,
  and `LANGFLOW_AUTO_LOGIN=true` grants unauthenticated superuser access.
  Start it deliberately: `docker compose --profile langflow up -d`.

---

## 4. Traps that will waste your time (every one has actually bitten)

1. **Vite does not see your edits.** Source is on `/mnt/c`, the dev server is
   in WSL, and inotify does not fire for Windows-side writes. The runner sets
   `VITE_POLL=1`. Verify with
   `curl -s http://localhost:3001/src/components/Sandbox.css | grep <selector>`.
2. **Never put `pkill -f 'uvicorn app:app'` in a `bash -lc "..."` string.** The
   pattern matches the parent shell's own command line. Keep the kill inside
   the script file, or kill by explicit PID.
3. **Background jobs die with the session** unless launched with
   `setsid nohup ... & disown` — and even then the launching shell can exit
   before `setsid` forks. Append `; sleep 8`, then assert with `pgrep`.
4. **A restart that didn't restart looks like a working server.** If the old
   uvicorn survives, `> /tmp/backend.log` truncates while the old process
   holds an fd at its previous offset, re-inflating the file with NUL bytes.
   Two symptoms mean exactly this: `grep` reporting "binary file matches", and
   startup lines missing from a growing log. Use `grep -a`.
5. **Heredocs written from the Windows side get CRLF.** Write scripts inside
   WSL, or `sed -i 's/\r$//'` first.
6. **`import app` at a REPL hangs forever.** python-chess runs the engine on a
   non-daemon thread, closed by a FastAPI shutdown hook a plain script never
   fires. Use `python -m py_compile` for a syntax check, or the test suite.
   Any test importing `app` and touching Stockfish must end with
   `app.stockfish_service.close()`.
7. **CSS load order: ChessBoard.css always wins ties.** `Sandbox.css` is
   imported by `Sandbox.tsx`, which `App.tsx` imports *above* its own
   `ChessBoard.css` line, so ChessBoard.css lands last. Any sandbox rule that
   must beat an `.action-btn` rule needs a **two-class selector**. This has
   bitten twice, most recently rendering the coach chat's Ask button grey
   instead of ice.
8. **The board column must be pinned to the board's width.** Left `auto`, the
   widest child wins — and that is the four-button transport row, whose nowrap
   labels come to 631px against a 564px board. The row then sticks out past
   the board's right edge and shoves the analysis panel away, which reads as
   "a massive gap between the board and the tabs". Both
   `.sandbox-board-column` and `.sandbox-board-wrapper` derive their width
   from `--board-size`.
9. **`dcg` blocks several git and filesystem operations.** `rm -rf` outside
   `/tmp`, `mv` touching home paths, and `git checkout <ref> -- <path>` are
   all refused. For a tree-replacing merge, build the commit with
   `git commit-tree` instead of touching the working tree.
10. **A browser tab open across a long session goes stale.** HMR sockets drop,
    and the user then sees none of your changes and reasonably reports that
    nothing changed. Before debugging, confirm what Vite is actually serving
    with `curl`, then ask for a hard refresh.

### Startup lines that tell you the LLM is actually in the loop

Check after every restart, locally and on Render. Gemini's absence once
surfaced only as a move explanation reading "Langflow unavailable".

```
🤖 Move selection: Gemini direct (models: gemini-3.5-flash, ...)
🗣️ Sandbox narration: Gemini (models: gemini-3.1-flash-lite, ...)
🎬 Sandbox scenarios: Gemini (models: gemini-flash-lite-latest, ...)
```

A stronger check, because it exercises the whole chain — create a sandbox
session and POST to its `/chat`; a reply quoting a centipawn number proves
Stockfish and Gemini are both live. On a real AI move, `source: "ai"` with a
move explained in its own words is the proof; `source: "stockfish_fallback"`
is the failure.

---

## 5. Tuning knobs (all env vars, no code change)

| var | default | what it does |
|---|---|---|
| `GEMINI_API_KEY` | — | required; without it the app is engine-only |
| `GEMINI_MOVE_MODELS` | chain 1 | move selection |
| `GEMINI_CHAT_MODELS` | chain 2 | real-game chat |
| `GEMINI_NARRATION_MODELS` | chain 3 | sandbox narration |
| `GEMINI_SCENARIO_MODELS` | chain 4 | scenario generation |
| `GEMINI_SANDBOX_CHAT_MODELS` | chain 5 | **the sandbox coach chat** |
| `GEMINI_*_TIMEOUT` | 6–20 | seconds per model, per service |
| `GEMINI_NARRATION_CONCURRENCY` | 2 | max narration calls in flight |
| `STOCKFISH_DEPTH` | 15 (12 on Render) | full depth |
| `STOCKFISH_RANK_DEPTH` | 10 (8 on Render) | shallow stage-1 ordering |
| `DISABLE_LANGFLOW` | true in both images | skip the Langflow path entirely |
| `ACCOUNTS_ENABLED` | false | accounts refuse with 503 until this is true (§13) |
| `ALLOWED_ORIGINS` | localhost | comma-separated CORS allowlist; **required in production** |
| `COOKIE_SAMESITE` / `COOKIE_SECURE` | lax/none by host | identity cookie flags, §13 |
| `ENABLE_DOCS` | on locally, off in production | serve `/docs` and `/openapi.json` |
| `VITE_PROXY_TARGET` / `VITE_POLL` | — | dev server backend + watcher |

**All five model chains lead with a different model on purpose.** They share
one API key, and when moves and chat both led with `gemini-3.5-flash`, chat
immediately got HTTP 429 from move traffic. `test_scenario.py` asserts the
five leads stay distinct — if you retune, keep that true. `gemini-3.6-flash`
is last in every chain: it does not refuse, it *hangs*, so leading with it
spends the full timeout on every request.

---

## 6. Tests — 353/353

| file | what | needs |
|---|---|---|
| `test_gemini_move.py` | 9, mocked HTTP | — |
| `test_sandbox_state.py` | 41, move tree + sessions, pure | — |
| `test_player_state.py` | **41, per-player isolation, pure** | — |
| `test_scenario.py` | 61, incl. a 480-position legality fuzz | — |
| `test_decide_integration.py` | 6, real Stockfish + faked Gemini | Stockfish |
| `test_sandbox_api.py` | **87**, `/api/sandbox/*` end to end | Stockfish |
| `test_sandbox_narration.py` | 34, narration + parallel wiring | Stockfish |
| `test_accounts.py` | **74, guest mode + accounts-off + auth internals** | Stockfish |

```bash
cd /mnt/c/Users/David/Documents/chess-app-v3.9
/tmp/chessapp/bin/python -u test_gemini_move.py && \
/tmp/chessapp/bin/python -u test_sandbox_state.py && \
/tmp/chessapp/bin/python -u test_player_state.py && \
/tmp/chessapp/bin/python -u test_scenario.py && \
DISABLE_LANGFLOW=true /tmp/chessapp/bin/python -u test_decide_integration.py && \
DISABLE_LANGFLOW=true /tmp/chessapp/bin/python -u test_sandbox_api.py && \
DISABLE_LANGFLOW=true /tmp/chessapp/bin/python -u test_sandbox_narration.py && \
DISABLE_LANGFLOW=true /tmp/chessapp/bin/python -u test_accounts.py
```

Spell the eight out — a `for t in ...` loop inside `bash -lc "..."` has its
`$t` mangled and every suite runs as an empty name.

`test_accounts.py` sets `ACCOUNTS_ENABLED=false` **before importing app**, on
purpose: `app.py` wires the account resolver into the identity middleware at
import time, and the claim being tested is that with the flag off there is no
code path that could resolve an account at all.

**Do not assert an exact engine line.** The shared engine keeps its hash
between searches, so near-equal moves reorder between runs. A test pinning the
mating line to `"Qa1+ Kg8 Qg7#"` failed about one run in three on a position
that has more than one mate in two — both answers correct. Assert the property
(it mates; it is as long as the mate it claims), never the string.

**Check `chess.Board(fen).is_valid()` before handing a constructed FEN to
Stockfish.** An unreachable position does not come back as an error — it hangs
or segfaults the shared engine, as `move_quality.py` documents. A test FEN with
two kings on adjacent squares took out a whole run this way.

**`TestClient` must be a context manager** (`with TestClient(app) as client:`)
in any test exercising a detached background task. Used bare it builds a fresh
portal per request, so a task detached by one request dies the instant that
request returns, and narration sits at `pending` forever.

The reused engine keeps its hash between searches, so **analysing the same
position twice can reorder near-equal moves.** Don't assume determinism.

Frontend: `npx tsc -b --force` in `chess-frontend`. **A typecheck proves
nothing about layout** — see §10.

---

## 7. Backend, settled — don't redo

### Performance: 10.08s → 1.97s per half-move (5.1×)

Root cause was **not** the LLM. `get_ranked_moves` analysed every legal move
at full depth, and three entry points each spawned a fresh Stockfish process.
Fixed by a **staged search**: stage 1 orders all legal moves at shallow
`RANK_DEPTH` (ordering is all it is for — those scores are never shown);
stage 2 re-analyses **only the selected window** at full `SEARCH_DEPTH` via
`root_moves`. The engine is opened once and reused under a lock.

> A benchmark that disproved the obvious fix is preserved in the module
> docstring: **MultiPV over all legal moves is NOT faster** (0.8–1.3×),
> because asking for N principal variations kills alpha-beta pruning.
> **Don't "optimise" this back.**

**Stage-1 scores are sort-only and must not be shown.** Anything handing
scores to Gemini or the UI passes them through `refine_candidates()` first.
The sandbox coach chat does exactly this before quoting centipawns.

### Model chains — ordered by reliability, measured not assumed

`gemini-2.5-*` **404 on this key** ("no longer available to new users"). The
user asked for 2.5 specifically; it is not available and no code change fixes
that. Measured: `gemini-3.5-flash` 3.09s reliable, best explanations;
`gemini-3.5-flash-lite` 0.88s but ReadTimeouts in real play;
`gemini-3.1-flash-lite` 6.97s works; `gemini-3.6-flash` 7.89s and hangs.
Order by **reliability, not benchmark speed**, and all services **try the last
model that worked first**.

### API key leaking into the logs (fixed)

`httpx` logs every request at INFO, and the Gemini REST URL carries the key as
`?key=...`. `app.py` sets the `httpx` logger to WARNING. **If you re-enable
httpx INFO logging, you re-open this.**

### Rate limiting — `rate_limit.py`

Per-IP fixed-window limits. The risk is not key theft, it is key *usage*:
every call spends quota billed to the server's key. `/api/status` is
deliberately unlimited — the frontend polls it about once a second.

| endpoint | limit |
|---|---|
| `/api/move`, `/api/ai-move` | 30/min |
| `/api/chat` | 10/min |
| `/api/move-quality/regrade` | 6/min |
| `/api/sandbox/scenario` | 6/min |
| sandbox `/move`, `/ai-move` | 20/min |
| sandbox `/chat` | 10/min |
| sandbox `/alternatives` | 20/min |

Sandbox limits are tighter on purpose: a demonstration is watched, not played,
and nothing else throttles it since you can reach it without playing a game.
`/alternatives` takes the shared engine lock, so abusing it stalls move
selection for everyone.

> This file existed only on the old `master` and was **absent from the V4.5
> tree**. Merging V4.5 in would have deleted it, removing protection at the
> exact moment the API went public. If you ever rebase or re-import the tree,
> check it survived.

---

## 8. Sandbox Learner Mode

**What it is:** the AI plays both sides to demonstrate tactical lines,
openings or custom scenarios, with synchronized narration. The user types a
natural-language prompt to generate a position, and can rewind, ask the coach
about it, or take over the board.

**User's decisions — decisions, not suggestions:**

1. **A separate mode**, not bolted onto normal play. Must not touch the real
   game state or the learning DB.
2. **Taking over the board branches permanently** from that point.
3. **Scenario generation is natural language only — no buttons.** The prompt
   bar at the top is where you ask for a position; the coach chat is where you
   ask *about* it. Keep those two jobs separate.
4. **Lean on Gemini.** (Quota warning in §14.)
5. **Design for session isolation from the start** — this goes public.
6. **Match the existing UI**, treated as context, not reinvented.

### The API

| endpoint | does |
|---|---|
| `POST /scenario` | NL prompt → validated position **+ opens a session** |
| `POST /session` | session from explicit `start_fen`, or standard position |
| `GET \| DELETE /session/{id}` | full state / drop it |
| `POST /session/{id}/ai-move` | AI plays one half-move |
| `POST /session/{id}/move` | user plays one half-move |
| `POST /session/{id}/{goto,back,forward,reset}` | navigate / restart |
| `GET /session/{id}/alternatives` | Stockfish ranking + what's explored |
| `GET /session/{id}/narration[/{node_id}]` | poll narration |
| **`POST /session/{id}/chat`** | **the coach chat (§8.4)** |
| `GET /session/{id}/chat` | the transcript — used on reload to restore it |
| **`POST /classify`** | **is this message a question or a build request (§8.5)** |
| **`GET /session/{id}/eval`** | **the position's eval, for the eval bar** |

**Two field-name traps:** the session id field is **`session_id`, not `id`**;
a node carries **`narration_status`** while the narration poll endpoint returns
**`status`**. Both spellings are correct in their own place.

**`/reset` takes a `ResetRequest` and every field is optional.** An omitted
field keeps what the session has, and an omitted `start_fen` means *this
session's own root*, not the standard opening. It is also the only way to
change a session's strength. Reset drops the tree — that is what restart
means — which is why the UI makes it a separate, labelled click.

`ResetRequest` also takes **`title`**, and the UI's single Reset button sends
it along with the standard-opening FEN. Without it a session opened by
`/scenario` kept that scenario's name, so the heading went on calling a plain
starting position "Hard Rook Endgame".

**`/eval` is its own endpoint on purpose.** It is a full-depth search sharing
one engine lock with move selection, so putting it on every state response
would slow every demonstration for a number that is off by default. It is
fetched only while the bar is showing, and it answers in **White's absolute
frame** — a bar that flipped meaning with the side to move would be unreadable.
Note that the ranked-move scores use the opposite convention (side to move),
which is correct for *those* and is why the two must not be mixed up.

**`/classify` never fails.** See §8.5 — an unreachable Gemini returns `ask`
with a 200, because the asymmetry of the two wrong answers is the whole point.

### Architecture, and the invariants that hold it together

**`sandbox_state.py` is pure** — no FastAPI, Stockfish, Gemini or SQLite.

- `MoveTree` — nodes with a parent and ordered children. **Nothing is ever
  deleted**, so an abandoned branch is still there to rewind into. Each node
  stores **its own FEN**, so `board_at()` is O(1).
- `SandboxSession` holds **no** learning-service handle or reference to the
  module-level `game`. It *cannot* write to the learning DB. A test asserts
  it. It also holds `chat_history` and `scenario_description` (§8.4).
- `SessionStore` — TTL sweep (1h idle) + hard cap (50), per decision 5.

**The sandbox reuses `app.decide_ai_move` rather than forking a second
selection path** — a demo mode that selected moves differently would be
demonstrating something the app doesn't do. It gained three keyword args, all
defaulting to today's behaviour: `difficulty`, `last_move` (an `_UNSET`
sentinel, because `None` is itself meaningful), and `use_learning` (sandbox
passes **False** — a demonstration must not bias the player's real history).

**The dependency is one-way.** `app.py` imports `sandbox_api` to mount the
router, so `sandbox_api` cannot import back — `app.py` calls
`sandbox_api.configure(decide_ai_move)` instead.

#### Narration: a different voice, run in parallel

`gemini_move_service` returns the *player* justifying its own choice.
Narration is the *coach* telling a student what the move accomplishes.
**Don't collapse the two** — the sandbox shows both.

`_start_narration()` creates a detached task and never awaits it. Three things
hold that up, all load-bearing:

- Tasks live in a **strong** reference set. asyncio keeps only weak ones, and
  a task nobody holds can be GC'd mid-flight.
- Narration **leads with a model that measured slower**, on purpose: it never
  blocks a user-visible response.
- **Narration is addressed by node id, never ply index.** Node ids are minted
  once and never reused. Real-game move grading *does* use ply indices, which
  is exactly why it needs the `game_epoch` guard. Don't switch this.

#### Scenario generation: Gemini never emits a FEN

Asked for one it produces two black kings or pawns on the first rank, and
Stockfish **segfaults** on an unreachable position rather than rejecting it —
taking down the engine the real game shares. So Gemini returns **structured
constraints**, python-chess builds and validates the board. A FEN in the
model's reply is ignored; a test fires a rogue one to prove it can't get
through.

Three ways a position gets built: a **named opening** resolved against
`OPENING_LINES` (legal by construction), an **explicit SAN line** parsed move
by move, or **constructed material** for endgames. `test_scenario.py` fuzzes
480 constructed positions. **If you change placement, keep that fuzz test** —
it is the only thing between a sloppy constraint and a segfaulted engine.

Failures are deliberately honest: an unknown opening → **400 with the
reason**; generic words (`gambit`, `defense`) excluded from name matching;
material that can only draw → 400; absurd material **clamped**, not refused.

> One result looks like a blunder and isn't: an AI line playing Re8+ Kxe8,
> apparently dropping a rook, is a deflection — the b-pawn queens next move.
> Don't "fix" it.

### 8.4 The coach chat (replaced the "Why not?" panel)

`POST /api/sandbox/session/{id}/chat`. The user called the old panel useless;
its data is now reachable conversationally instead. The removal cost nothing:
**Take over** still lets you play any legal move and branch, which is what
"Try it" did.

**It is deliberately not `/api/chat`.** That endpoint reads the module-level
`game` and appends to a module-level transcript — routing sandbox questions
through it would mix the two conversations and hand the coach the wrong
position. The transcript lives on `SandboxSession`.

Three things make it a coach rather than a second opponent:

1. **Its own persona.** `_build_sandbox_instruction()` in
   `gemini_chat_service.py`. The game's instruction casts the model as your
   opponent and tells it to withhold a winning move it hasn't played. That
   rule is **absent** here, not softened — withholding the answer is the
   opposite of teaching.
2. **It sees the engine.** Every question is answered with Stockfish's ranked
   moves in context, `refine_candidates`-corrected first. That is what makes
   "why not Nf3?" answerable against the engine rather than the model's
   recollection.
3. **Its own model chain**, `GEMINI_SANDBOX_CHAT_MODELS` — a fifth caller on
   one key, so it needs its own lead for the same reason the other four do.

**Transcript lifetime:** cleared on a new scenario (which opens a new session,
so the server side clears itself by construction), **kept** across moves,
rewinds, branching and Restart line.

---

### 8.5 One composer that asks and builds

The scenario prompt bar and the coach chat were two inputs doing one job in two
places. They are one composer now, in the Chat panel, and the top strip they
shared is gone — worth ~90px of board.

`POST /api/sandbox/classify` decides which job a message means, because only
reading the sentence separates *"give me something easier"* from *"why was that
easier for white?"*. It has its own system instruction
(`INTENT_INSTRUCTION` in `gemini_chat_service.py`) that permits exactly two
tokens.

**Every failure mode lands on the harmless side, and that is the design.** The
instruction is biased toward ASK, an unparseable verdict is ASK, and an
unreachable Gemini returns `{"intent": "ask", "classified": false}` with **200,
not an error**. Answering a build request as a question costs an odd reply;
treating a question as a build request offers to destroy the line the student
is studying, and sandbox sessions have no undo.

- On an **untouched board** a build runs immediately — nothing to lose.
- With **moves played** it is *proposed*: an amber coach bubble with
  **Build it** / **Never mind**.
- **The transcript survives a rebuild**, separated by a rule carrying the new
  position's description. Clearing it would delete the message that asked for
  the position. The server's own history *does* start fresh, deliberately: its
  copy is replayed to Gemini, and replaying questions about a position no
  longer on the board would make the coach worse.
- It is held as **one state object** (`frozen` / `live` / `absorbed`). As three
  separate pieces, `freezeTranscript` read stale values out of a render closure
  and duplicated every turn above the rule.

Parsing the verdict, keep this: `**BUILD**` is a plausible model reply, and a
parser that stripped a leading asterisk but not a trailing one read it as ASK.
Keep only the letters of the first token.

### 8.6 The coach used to invent mating lines — fixed, understand why

**A position built and verified as mate in two produced a coach that named the
right first move and then a second move that did not mate.** Both causes were
ours, not the model's:

- `_analyse_moves` puts a forced mate in `mate_in` and leaves `score` `None`,
  and the chat context printed the score — so on a mating position the coach
  was told every candidate was worth **"None"**. The single most important fact
  about the position was the one thing withheld.
- **Only `pv[0]` survived the ranking.** Stockfish had computed the entire
  mating line to produce that score and it was thrown away, leaving a language
  model to calculate a forced sequence — the thing it is least able to do.

Now `_analyse_moves` keeps `pv` (capped at `PV_LENGTH`), `_score_text` renders
`"mate in 2"` / `"+6.32"` / `"mate in 3 against"`, and `_line_text` walks the
PV into SAN. The coach is handed the line with an instruction to answer **from
it** rather than calculate, and to say so when the line runs out.

**If you touch the chat context, keep the line.** The general lesson is worth
more than the fix: when Stockfish already knows something, give it to the model
instead of asking the model to work it out.

## 9. The frontend

**Read `OBSIDIAN_DESIGN.md` before touching any CSS.** It carries the
reasoning a stylesheet cannot. The short version:

- **`styles/obsidian.css` is the token source of truth.** Scales are
  theme-independent; dark is `:root`, light is `[data-theme="light"]` plus a
  guarded `prefers-color-scheme` branch.
- **Light mode is a design, not an inversion** — a cool paper ground with
  white surfaces raised out of it. **Raised is lighter in both themes**, which
  is why one set of elevation tokens serves both.
- **A semantic colour keeps its meaning across themes but not its value.** Ice
  is `#7fd4ff` on near-black and `#0b6f96` on white. Every semantic colour
  ships as four tokens (`--x`, `--x-soft`, `--x-border`, `--x-contrast`).
- **Ice is the AI's voice and nothing else may use it.** Amber = thinking.
  Red = destructive or failed. Green = verified. A status colour must match
  the status: the unread dot was red, so a panel that had merely updated read
  as one that had failed.
- **The `--cp-*` names are a migration seam**, aliasing the semantic tokens so
  existing component CSS became theme-aware without being rewritten. New CSS
  uses the semantic names.
- **A component states its appearance; its container states its layout.**
- **No uppercase labels, no tracking above 0.02em.**
- **Never dim a token with `opacity`** — the muted tier is already checked
  against its ground, and stacking opacity on it is how the only contrast
  failure in the app got in.

### Fixed — do not reintroduce

- **`--cp-t-base` was `var(--cp-t-base)`** — self-referential, resolving to
  nothing and silently killing 13 transitions. Now `0.22s`.
- **The negative-SVG console spam** was `window.innerWidth` reporting 0, and
  an unclamped `width - 100` reaching `<svg width="-12.5">`. `useBoardSize`
  clamps both ends.
- **`.action-btn` declared `width: 100%` and `flex`**, which collapsed the
  chat input to 28px and later the sandbox prompt to 30px. The class is
  appearance-only now and containers own layout, so it cannot recur. The three
  defensive two-class overrides became positive layout statements.
- **`prefers-reduced-motion` was scoped to `.chess-container`**, so the entire
  sandbox ignored it. Global now.
- The mode control was labelled with its *destination* ("Learner Mode" /
  "Back to game"), so the mode you were in never appeared on screen. Now a
  segmented control with `aria-current`.
- **`Sandbox` is hidden, not unmounted**, on mode switch — the same treatment
  `ChessBoard` always had. Unmounting destroyed the scenario, move tree and
  conversation. It mounts lazily on first use so a visitor who never opens
  Learner Mode doesn't burn a session slot.

### Accessibility

**0 axe violations** (WCAG 2.0/2.1 A + AA) across both themes and both modes.
Keep it there. Arrow keys navigate the sandbox line (`←`/`→`/`Home`), bound at
the document but ignoring inputs, contenteditable and modified keys — `Cmd/
Ctrl+←` stays "go back".

---

## 10. Verifying UI work

**The rendered application is the judge.** Every UI bug found in this project
was found by driving the live app; all of them typechecked cleanly.

### Run the invariants first

```bash
node tools/verify/ui.mjs                        # dev server on :3001
node tools/verify/ui.mjs http://localhost:3000  # or the container
node tools/verify/ui.mjs http://localhost:3001 --shots out/
```

**31 checks, all currently passing**: console errors and overflow in both modes
and both themes; AA contrast on every text style; 44px touch targets under a
coarse pointer; and the Learner Mode layout invariants — board and tab row on
one line, the eval toggle moving nothing, the layout centred. Each of those
was a real bug on this branch, so the file is a regression net rather than a
checklist. Run it before claiming any UI work is done.

Board coordinates are deliberately excluded from the contrast check: they are
ink on a halo, and a ratio measured against the bare square cannot see the
halo. Judge those by eye.

The Chrome DevTools and Playwright MCP servers are **both broken here** —
Playwright's is pinned to `/opt/google/chrome`, which does not exist. Drive
the browser directly instead. `playwright-core` plus the bundled Chromium
works, and its system libraries are now installed:

```js
import { chromium } from 'playwright-core';
const EXE = '/home/david111/.cache/ms-playwright/chromium-1234/chrome-linux64/chrome';
const b = await chromium.launch({ executablePath: EXE, args: ['--no-sandbox'] });
```

Set the theme with `localStorage.setItem('zugzwang-theme', 'light'|'dark')`
then reload. For an accessibility pass, `npm i axe-core` and inject
`axe.min.js`.

- **Playwright `has-text` is a substring match.** `button:has-text("Line")`
  also matches "Play line" and "Restart line". Use `:text-is("Line")`.
- **Scope selectors to `.sandbox`** — the game pane stays mounted and hidden,
  so its buttons still match.
- **Wait out `animationDuration={300}`** before asserting on board DOM, or you
  read the *previous* position and think the move didn't apply.
- **Measure, don't eyeball.** The board-vs-panel misalignment in trap 8 was
  invisible in three rounds of screenshots and obvious the moment element
  bounding boxes were printed side by side.

---

## 11. UI state as it stands

Board is the anchor and sizes with the viewport (`useBoardSize`, 460–644px,
height-capped by a per-mode chrome allowance — 300 for the game, 360 for the
sandbox, both measured against the running app). At 1920/1440/1280 **nothing
scrolls**.

Both sandbox columns are pinned to the board's width, and the panel can never
be wider than the board. **The cap lives on the grid TRACK, not on the panel.**
Capping the element while its track stayed `1fr` meant the track claimed the
width and the element declined it — 292px of dead space to the right of the
tabs at 1280×800, with the whole layout 80px from the left edge and 352px from
the right.

Three more layout rules on the sandbox, each of which was a bug:

- **The heading sits above the BOARD, in its own grid row.** Inside the right
  column it pushed the tab row down while the board started at the top, so the
  title came to rest level with the board's top edge and the two columns began
  at different heights.
- **The eval row is always in the layout**, hidden with `visibility` when off.
  Un-rendering it shrank the board by 19px → narrowed the column → wrapped the
  display row (+43px) → shrank again. A 19px strip cost 70px of board and
  dragged the panel and tabs with it, because both size from `--board-size`.
- **`useFittedBoardSize` measures rather than models.** The chrome under the
  board is not a fixed height — the control row wraps differently at different
  widths — so `useBoardSize`'s hand-tuned constant (340 → 372 → 360 → 297) was
  right at exactly one viewport each time. It corrects by the page's actual
  overflow, so the next row added under the board needs no retuning.

One segmented-control treatment is shared by the header (`Play`/`Learn`), the
game's analysis rail, and the sandbox tabs (`Chat`/`Coach`/`Line`/`Board`).
They all answer "which view am I looking at" and used to be three designs.

**Learner Mode's controls, as they now stand.** Below the board: an
always-present alert strip (check / checkmate / stalemate / draw, the same
three colours the real game uses), the always-present eval row, then
**AI move / Take over / Back / Forward**, then the display row
(`Eval bar`, `Coach my moves`, the difficulty select, **Reset board**).

- **AI move toggles** — it starts the line playing and stops it, and says which
  in its own label. "Play line" is gone; single-stepping went with it.
- **Take over** sits in the transport row because the thing worth discovering
  there is that you can play too.
- **One reset**, always to the standard opening. It carries a staged difficulty
  ("Reset at 18 — Merciless").
- The difficulty select has **no visible label** — it reads "12 — Club", which
  is the label and the value in one. Removing that one redundant word was the
  9px that let the display row fit one line, which is worth 43px of board.

**What persists** (all `localStorage`): `chess-mode` (Play vs Learn),
`sandbox-panel`, `sandbox-piece-theme`, `sandbox-eval-bar`, `sandbox-session`,
plus the game's `chess-active-section`, `chess-piece-theme`,
`chess-engine-numbers`, `chess-coordinates`, `chess-move-quality`, and
`zugzwang-theme`. A reload returns to the same mode, panel and sandbox session
— board, tree and transcript included. A session miss is a 404 and opens a
fresh one, which is what always used to happen.

**Learner Mode has its own piece set**, separate from the game's but defaulting
to it on first use. Its swatches draw the real pieces, because `getBoardColors`
returns the same pair for every theme and a board-colour chip cannot tell the
four apart.

---

## 12. Runner script contents (recreate if `/tmp` is wiped)

Write these **inside WSL** (trap 5); `chmod +x` both.

`/tmp/run_backend.sh`:
```bash
#!/bin/bash
pkill -f 'uvicorn app:app'
sleep 1
cd /mnt/c/Users/David/Documents/chess-app-v3.9
if [ -f .env ]; then set -a; . ./.env; set +a; fi
export DISABLE_LANGFLOW=true
export STOCKFISH_RANK_DEPTH=10
export STOCKFISH_DEPTH=15
exec /tmp/chessapp/bin/python -u -m uvicorn app:app --host 0.0.0.0 --port 8081
```

`/tmp/run_frontend.sh`:
```bash
#!/bin/bash
pkill -f 'vite --host'
sleep 1
cd /mnt/c/Users/David/Documents/chess-app-v3.9/chess-frontend
export VITE_PROXY_TARGET=http://localhost:8081
export VITE_POLL=1
exec npx vite --host 0.0.0.0 --port 3001
```

Note: `node`/`npx` live in `~/.local/bin`, which is **not** on the PATH of a
fresh login shell. Use full paths under `sudo`.

---

## 13. Accounts, identity and guest mode — built

Three files and one switch. Read this before touching any of them.

### The seam: `identity.py`

Every request resolves to **one opaque string**, and nothing downstream parses
it:

```
guest:8f2a1c…   an anonymous visitor. Nothing is written to disk.
user:42         a signed-in account.
```

`player_state.py` keys a player's game on it, `sandbox_api.py` keys session
ownership on it, and neither knows what it means. That is the whole point:
**turning accounts on changes which string arrives and nothing else.** The
prefixes are for humans reading logs; exactly one place branches on them
(`is_guest`, which decides whether play may touch the learning database).

It is a **middleware, not a dependency**, because a dependency can read a
cookie but cannot set one — only a response can. So identity lands on
`request.state` for every route (including the separately-mounted sandbox
router) and the `Set-Cookie` is attached on the way out, in one place.

Two cookies, both HttpOnly: `zw_guest` (minted on first sight, one year) and
`zw_session` (only once signed in). HttpOnly because the identity string *is*
the key to a player's game — a value JavaScript can read is one an XSS can
steal, and a value the client can choose is one visitor assuming another's
session.

### `app.py` — the eleven globals are gone

Endpoints call `session_for(request)` and work on that. Also moved onto the
session and previously shared by everyone: `current_eval`, `regrade_progress`
and the AI-move lock. That last one mattered more than it looks — a single
process-wide lock meant one player thinking blocked every other player's move,
and "AI is already thinking" could be a stranger's AI.

`decide_ai_move` gained a `learning=` argument. It no longer reaches for a
module-level learning service, because it must not be the thing that decides
whose data it is touching.

### Guest mode — `guest_learning.py`

The requirement was "everything works, nothing is saved". Those conflict in
exactly one place: the cross-game learning layer both writes to
`data/learning.db` and reads it back to reweight the AI's candidates.

Two obvious answers are both wrong. Keep writing to the shared DB and a guest's
play is saved — worse, every anonymous visitor shares one pool and silently
biases each other's AI. Switch learning off for guests and the panel reads zeros
forever and the AI stops adapting, which is a visible change from how the build
works.

So each guest gets a **complete learning service whose database is in memory**.
It is `LearningService` subclassed with only `_connect` changed, so every query
and the reweighting rule are the real implementation — a guest's learning cannot
drift from an account's, because there is no second implementation to drift.

> The one subtlety: a plain `:memory:` database is private to one connection,
> and `_connect()` opens a new one per call — so every call would get a fresh
> empty database and nothing would appear to record. It uses SQLite's
> shared-cache URI (`file:<name>?mode=memory&cache=shared`) and holds an
> `_anchor` connection open, because the database is freed the moment the last
> connection closes. **Don't remove the anchor.** `PlayerStore._release()`
> closes it when the session is swept.

### Accounts — real, and switched off

`auth_service.py` is finished: PBKDF2-HMAC-SHA256 at 600k iterations with a
per-user salt, the iteration count stored **per row** so raising it later
rehashes users on sign-in instead of locking them out; opaque session tokens
stored only as SHA-256 hashes, so a table dump hands over no live sessions;
case-insensitive unique usernames enforced by a UNIQUE index rather than a
check-then-insert two threads can both pass; one error message for "no such
user" and "wrong password", because telling them apart is a username oracle.

While `ACCOUNTS_ENABLED` is not `true` — the default, and what ships:

- `/api/auth/signup` and `/api/auth/login` answer **503**, not 404. 404 says
  "no such thing" and invites the client to treat it as a bug; 503 says "this
  exists and is switched off", which is the truth and is temporary by
  definition.
- **No account resolver is wired into the middleware at all**, so the disabled
  state is enforced by what is *wired*, not only by what the routes say.
- `/api/auth/config` and `/api/auth/me` always answer, so the UI can ask who it
  is talking to without getting an error for its trouble.

The refusal is deliberately **server-side**. Hiding a button in React still
ships the button; anyone with devtools finds the route.

Signing in does **not** migrate a guest's in-progress game. The identity
changes, so `player_state` hands back that account's game — a clean switch
rather than a half-transfer whose failure mode (two identities, one board) is
what `player_state.py` exists to prevent.

### Frontend

`AccountMenu.tsx` renders the real UI — a Guest chip, Sign in, Create account —
and while accounts are off both entry points open a notice. **Not hidden and
not disabled:** a hidden control leaves the user unaware accounts are coming, a
greyed-out one gives them nothing to click and no explanation. The wording comes
from the server (`unavailable_message`) so what a user is told cannot drift from
what the backend does.

`http.ts` is not optional plumbing — it does two load-bearing things:

1. **`credentials: 'include'` on every call.** Without it a request may not
   carry the identity cookie, and the server then mints a fresh guest and hands
   back an empty board. That failure is silent: no error, just a board that
   keeps forgetting.
2. **It establishes identity once before any other call.** A brand-new visitor
   opens the app with several requests in flight at once, all with no cookie —
   so the server mints a *separate* identity for each and the browser keeps
   whichever `Set-Cookie` landed last. Everything created under the others is
   orphaned. This was found live: it surfaced as a 404 on the sandbox's own
   StrictMode cleanup DELETE, because the session had been created under one
   identity and deleted under another. **Don't remove the bootstrap.**

### CORS — the fix that is not cosmetic

`app.py` used to set `allow_origins=["*"]` with `allow_credentials=True`. That
pairing is not permissive, it is **broken**: the spec forbids it, so browsers
reject a credentialed cross-origin request whose response echoes `*` — silently.
No error in our log; the cookie simply never arrives. It went unnoticed only
because nothing depended on a cookie before. Origins are now explicit
(`ALLOWED_ORIGINS`, localhost by default).

### Sandbox sessions are now owned

They were always isolated from each other; they were not *owned*, so anyone
holding an id could drive that session. `_require()` now checks the caller's
identity and answers a foreign session with **the same 404 a missing session
gets** — distinguishing them would confirm to a stranger that a guessed id is
real. A session with `owner=None` stays reachable, which is what keeps the pure
tests meaningful without weakening the deployed path.

### If you switch accounts on

`ACCOUNTS_ENABLED=true` is the whole switch. Do not, until DEPLOY.md's "Before
switching them on" list is done — first among them that `data/accounts.db` is on
Render's ephemeral disk, so a redeploy would delete every account, and there is
no password reset.

These accounts are self-contained rather than Clerk, and that was put to the
user directly — they chose to keep it (§0).

---

## 14. Known open issues

- ~~**The real game is single-user.**~~ **Fixed** (§13). Every visitor gets
  their own board, difficulty, eval, transcript and grading, keyed by an
  identity cookie. Sandbox sessions are owned as well as isolated. Verified
  live with two cookie jars; `test_accounts.py` covers it.
- ~~**`allow_origins=["*"]`**~~ **Fixed** (§13) — and it was a real bug, not
  tidying: `*` plus credentials is rejected by browsers, so the identity cookie
  would never have arrived. **Set `ALLOWED_ORIGINS` to the Vercel URL on
  Render**, or CORS falls back to localhost only.
- **Accounts are built and switched off**, and must stay off until DEPLOY.md's
  checklist is done — `data/accounts.db` is on Render's ephemeral disk, so a
  redeploy would delete every account, and there is no password reset. They are
  deliberately **not Clerk** — the user was asked and chose to keep the
  self-contained service (§0).
- **A guest's game dies with the process.** By design — it is what "nothing is
  saved" means — but it is also why a Render restart drops every board, not
  just sandbox sessions.
- **API quota contention.** Five Gemini paths share one key. Mitigations: five
  distinct chain leads, `GEMINI_NARRATION_CONCURRENCY`, and `rate_limit.py`.
- **The learning DB resets on Render** (ephemeral disk). A persistent disk is
  paid.
- **Sandbox sessions die with the process** — by design, no persistence layer.
  Note the frontend now *remembers the session id* across a reload, so a
  refresh resumes the same board when the server still has it; a miss is a 404
  and opens a fresh one.
- **`LANGFLOW_AUTO_LOGIN=true`** grants unauthenticated superuser access.
  Mitigated by the compose profile (it doesn't run), not fixed.
- **The folder is still named `chess-app-v3.9`** (§1).

---

## 15. How the user works

- Wants **evidence, not claims** — measure and show the numbers. Several
  hypotheses have been disproved by benchmarking (MultiPV, narration
  contention); every time, the measurement was the useful output.
- **Concise replies, solution first.** Lead with the command or the fix, not
  with context or a recap of what you investigated.
- **Ask when uncertain — never execute blind.** This holds even mid-way
  through a long autonomous task: a standing "keep going" covers executing
  decisions already made, not guessing at an undecided one. Group the open
  questions and ask them together so the work blocks once.
- Reacts badly to unexplained behaviour changes. If something degrades —
  especially the LLM dropping out of the loop — **say so loudly and up front**
  rather than burying it.
- Asks to be consulted on scope before big builds.
- Verify in the **running app**, not just tests.
