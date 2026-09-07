# Zugzwang — Chess AI Platform (V4.5, deployed)

Human-vs-LLM chess coach. **Stockfish proposes, Gemini decides.** Stockfish
ranks legal moves and slices a 3-move window by difficulty; Gemini picks one
from that window and explains it in its own voice. Every half-move is graded
chess.com style, there is a mid-game chat about the position, a Learner Mode
sandbox with its own coach chat, a Post-Mortem review for games you bring
yourself, and a SQLite cross-game learning layer.

**Three modes, and they are peers.** `Play` is the real game, `Learn` is the
sandbox, `Review` is Post-Mortem (§14). One header control switches between
them; all three are mounted at once and hidden rather than unmounted, because
each holds state the user would be furious to lose by glancing at another.

If you change one thing about how this app works, do not break that
sentence: **Gemini choosing from Stockfish's shortlist is the product.** A
build where Stockfish just plays its own top move is a regression even if
every test passes. It has a visible signature — `source: "stockfish_fallback"`
and the string *"Stockfish-calculated move (no Gemini API key configured)"*.

**State of play:** `master` is **deployed and live** — backend on Render at
<https://zugzwang-api.onrender.com>, frontend on Vercel at
<https://chess-app-rho-swart.vercel.app>. The work in flight is the
`postmortem` branch, which is committed locally and **not pushed**. Read §0 for
what landed, what is deliberately switched off, and what is queued.

---

## Working alongside another agent — read before your first write

**More than one AI agent works in this repository.** Claude Code and Codex are
both expected here, sometimes in the same stretch of work, and neither is the
owner. This file is the shared source of truth for both: `AGENTS.md` in the repo
root is a short pointer to it rather than a second copy, because two documents
describing one codebase drift the moment either is edited, and the drift is
discovered by whichever agent trusted the wrong one.

If you change how the app works, **change this file in the same commit.** An
agent that arrives after you has no access to your reasoning except what is
written down here, and "I would have explained it if asked" is not available to
a session that has already ended.

### Four things that are genuinely shared, and will bite

1. **One working tree, no locks.** Two agents editing the same file at the same
   time is a lost edit, not a merge conflict — nothing here is doing three-way
   merging on an uncommitted file. So: **commit early and often**, work on a
   branch rather than in a long uncommitted state, and before a large edit run
   `git status` and `git log --oneline -3`. A dirty tree you did not dirty means
   somebody else is mid-task; say so rather than committing on top of it.
2. **One Stockfish process, one engine lock.** Every search in the app queues
   behind every other one (`stockfish_service.py`). A Post-Mortem scan is ~80
   searches, the test suites run hundreds, and a second agent doing either makes
   the first one's work slow rather than broken. If a search-heavy thing is
   suddenly taking minutes, check whether the other agent is running the suite
   before you start optimising anything.
3. **One Gemini key, six model chains.** The chains lead with six different
   models precisely so callers do not compete for one model's quota (§5), and
   that budget assumes one app. Two agents driving the live UI at once can push
   the shared key into 429s that look exactly like a code bug. Prefer the test
   suites, which fake the model, over driving the real thing repeatedly.
4. **One dev stack, on fixed ports.** `:3001` and `:8081` for dev, `:3000` and
   `:8080` for Docker (§3), and the runner scripts in §12 begin with a `pkill`.
   **Restarting "your" backend kills the other agent's.** Before restarting,
   check whether one is already up (`pgrep -af 'port 8081'`, `curl -s -o
   /dev/null -w '%{http_code}' http://localhost:8081/api/status`) and reuse it if
   it is. Trap 4 in §4 is the failure this causes when it goes half-wrong.

### What is not shared

`/tmp/chessapp` (the venv) is shared and rebuilt the same way by both agents, so
either may recreate it — see §3. `.env` is shared and **must never be printed by
either of you**, for the reason §1 gives. Nothing outside this directory is in
scope for either agent.

### Handing over

When you stop mid-task, leave §0 telling the truth: which branch, what is
committed, what is verified and what is only typechecked. The next agent will
believe it. That is the whole point of it, and it is also why §0 is a handoff
rather than a changelog — it says where the work *is*, not what was done.

---

## 0. HANDOFF — where the work is right now

**Post-Mortem (`Review`) is built.** It is the third mode: drop a PGN on an
empty canvas, walk the game, see every move graded, branch off any position to
play what you wish you had played, and ask a coach about it that is holding the
engine's evidence. §14 is the whole story; the acceptance list it was built
against is in `~/Downloads/Claude Code — Build Post-Mortem Analytics Mode.md`.

| | |
|---|---|
| **Branch to work on** | `postgres-storage`, branched off `master` and **not merged, not pushed**. `master` is untouched and still what Render and Vercel serve. `interaction-gamestate-pass` is kept as a landmark with nothing unmerged on it. |
| **What is on that branch** | **The account/storage foundation** — Neon Postgres, versioned migrations, owner scoping, guest claiming and retention, a signed guest cookie, email on accounts, and Google sign-in built-but-unconfigured. See §13. `ACCOUNTS_ENABLED` is **still off**, deliberately: this is an implementation-and-proof milestone, not the account launch. |
| **Browser-verified** | Yes, on a separate QA stack (`:3002` frontend → `:8092` backend → a disposable Neon schema) rather than `:3001`, because the dev backend on `:8081` was running older code and belonged to another session. Guest plays → rows land under `guest:…` → signup claims them (`claimed_games: 1`, ownership rewritten, ledger row written, moves followed) → logout → login by email → history still there → a second account sees none of it. |
| **In flight** | **Postgres.** `data/accounts.db` and `data/learning.db` were both on Render's ephemeral disk, so a redeploy deleted every account and all cross-game history — DEPLOY.md's blocker 1. Both now live in one Neon database (`db.py`, `schema.sql`). Guest history became **claimable** in the same work, which inverted `guest_learning.py` (deleted) — §13 has the whole story, including the three guardrails that replaced it. Needs `DATABASE_URL` on Render before this is deployed, and adds `psycopg[binary,pool]` — the first new dependency and first new required env var in a while. |
| **What just landed** | Post-Mortem AND the UI overhaul AND the QA fixes, in one merge (`ec47f5e`). Neither feature had ever been deployed. |
| **Then** | **The interaction and game-state pass** (§19). Drag-to-move added to Play alongside click-to-move; one shared reading of check/checkmate/stalemate/draw (`boardState.ts`); the checked king's square marked red on all three boards; a translucent end-state layer over the board with the mode's own reset under it. Three real bugs fixed on the way — see §19. |
| **After that** | **A full product audit pass** (§20). Three confirmed findings, all fixed: Learn's `Forward` and Review's `Next` offered a step where there provably was none, and Review's empty canvas made a privacy claim the coach contradicts. Everything else checked came back clean or already correct — §20 lists what was checked and found to need nothing, which is the half of an audit that is worth writing down. |
| **After that** | **The learning loop, v0** (§21). Review has a fourth tab: state what you were trying to do, get an evidence-grounded diagnosis filed under one of eight controlled themes, play the better move on the real board, and take one certified fresh position testing the same idea. Corrections and practice accumulate per guest **in memory** - §13's "nothing is saved" contract is intact, and §21 says exactly what that means for how long a card lasts. |
| **Fixed before that** | **Play Mode's eval bar.** It stood beside the board, inside a column sized to exactly the board's width, so switching *Engine numbers* on pushed the board frame ~50px past its own column — under the coaching tab strip and over the moves list. It is now a horizontal strip on `.game-strip` under the board, the shape Learn already used (`.sandbox-eval`), reserved with `visibility` so toggling moves nothing. Verified on :3001 and on :3000. |
| **Deployed branch** | `master` — pushed to origin (`ce4b69b`, 2026-09-06), and **Render auto-deployed it**. The learning loop DOES change the backend (a new router, three new rate-limit buckets, five new modules) — but still **no new dependency and no new required environment variable**: `GEMINI_DIAGNOSIS_MODELS` and `GEMINI_DIAGNOSIS_TIMEOUT` are optional with built-in defaults, and `requirements.txt`, `package.json`, `render.yaml` and both Dockerfiles are untouched. |
| **Deploy state** | **In step, and Render deploys itself.** Probed 2026-09-06 against `zugzwang-api.onrender.com`: `/api/postmortem/game/xxx` answers *"That review is no longer open"* (the route working on a missing game — an absent route answers `{"detail":"Not Found"}`, which is how to tell them apart) and `/api/learning-loop/themes` returns the full taxonomy. **Render auto-deploys on a push to `master`; it does not need a manual redeploy.** The earlier "SKEWED" row in this table was true on 2026-09-05 and was then repeated for a day without being re-probed — see the warning below. |
| **Tests** | **827 across 14 suites, all passing** (§6) — the new one is `test_accounts_postgres.py` (94), which runs with accounts ON. Storage-touching suites need `DATABASE_URL` as well as Stockfish, and a run takes a disposable schema (`DATABASE_SCHEMA`) so it neither writes to the live database nor collides with another agent's run + **73/73 UI invariants** + **119/119 interaction invariants** + **22/22 board-state cases** (§10), all re-run in a browser against this branch |
| **Driven live** | yes, on :3001 — import, navigate, branch, engine reply, scan, coach, both themes; and for §19, drag and click in all three modes, mouse and touch, six viewports, 0 axe violations |
| **Playtested** | yes — full-service QA pass, 2026-09-05. Verdict **READY WITH MINOR ISSUES** (§16) |
| **Docker build (:3000)** | **rebuilt from `master` (`8eef622`, the learning loop) on 2026-09-06** — container healthy, **73/73 UI and 119/119 interaction invariants pass against `:3000`**, and the whole learning loop was driven through the shipped build in a browser (26/26) against the real Gemini path. Newest rollback point: `zugzwang:v4.5-pre-learning-loop`. Previously rebuilt on 2026-09-06 from `9e7dff4` — image `zugzwang:v4.5` carries the interaction pass and the audit fixes; container healthy, **73/73 UI and 119/119 interaction invariants pass against `:3000`**, and the startup lines confirm Gemini on all three paths. Newest rollback point: `zugzwang:v4.5-pre-interaction`. Previously rebuilt from `ui-overhaul` on 2026-09-05 — image `zugzwang:v4.5` **carries the overhaul and the QA fixes**. 58/58 invariants pass against :3000; the mate and figurine fixes verified inside the container. Rollback points: `zugzwang:v4.5-pre-ui-overhaul` (the Post-Mortem build) and `zugzwang:v4.5-pre-postmortem`. No git move was made; `master` is untouched. |

> ⚠️ **Do not push, merge to master, or deploy without asking.** Master is what
> Render and Vercel serve. The one merge and push that has happened
> (`ec47f5e`, 2026-09-05) was asked for explicitly; that was permission for
> that push, not a standing licence. Local commits on a branch are expected;
> anything leaving this machine is not.

> ⚠️ **Re-probe the deploy state before repeating it.** The row above said
> "SKEWED" for a day after it stopped being true, and it was relayed to the
> user three times on the strength of this file rather than a request. Two
> commands settle it, and a stale claim here sends someone to redeploy
> something that is already live:
>
> ```bash
> curl -s https://zugzwang-api.onrender.com/api/postmortem/game/xxx
> curl -s https://zugzwang-api.onrender.com/api/learning-loop/themes | head -c 120
> ```
>
> `{"detail":"Not Found"}` means the route is genuinely absent. Any other
> `detail` means the route is there and answering.

### Deploying `ec47f5e` — what it needs

**No new environment variables.** Checked against `render.yaml` and every
`os.environ` read in the backend:

- `GEMINI_POSTMORTEM_CHAT_MODELS` is new but **optional** - it falls back to a
  built-in six-model chain.
- No new Python or npm dependencies. `requirements.txt`, `package.json`,
  `render.yaml` and both Dockerfiles are byte-identical to what was deployed.
- The identity cookie is `SameSite=None; Secure` in production: Render sets
  `RENDER`, `is_production()` reads it, and the flags follow. Nothing to set.

  **Correction to what this file used to say.** It described the deployment as
  "the Vercel-to-Render origin split", implying the browser talks to Render
  directly. It does not: `chess-frontend/vercel.json` rewrites `/api/:path*`
  to the Render URL, which is a server-side proxy, so the browser only ever
  sees the Vercel origin and the cookies are first-party. `SameSite=None;
  Secure` remains correct and harmless there, but it is belt-and-braces rather
  than the thing holding the deployment together. Worth knowing before
  debugging a cookie problem on the wrong assumption. **Not verified against
  the live site** - the rewrite is read from config, not observed.
- `ALLOWED_ORIGINS` must list the Vercel URL, as it already did.

**What to watch after the redeploy**, none of it blocking:

- Post-Mortem's whole-game scan runs Stockfish over every ply at
  `POSTMORTEM_SCAN_DEPTH` (default 12). It is the heaviest thing this backend
  does, and a free instance will feel it. `POSTMORTEM_SCAN_DEPTH` is the knob.
- Reviews live in memory (capped at 20, one hour idle) and the learning DB is
  on ephemeral disk, so both reset when the instance sleeps. Known, unchanged
  by this work, and the reason §0 lists Postgres as the real blocker.
- Post-Mortem is a fifth caller on one Gemini key. `rate_limit.py` caps it.

### What landed before this

`master` carries the merged `impeccable-ui-pass` work: the UI pass, guest mode,
per-player state (`player_state.py`, §13) and accounts built behind
`ACCOUNTS_ENABLED`. `git log` those commits before touching that area - each
carries its reasoning.

### Next, in order

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

**Step 2 (Postgres) is the blocker for anything real:** accounts, the learning
DB *and* every imported review sit on Render's ephemeral disk.

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
| `postmortem_state.py` | **an imported game and the branches off it (pure)** — §14 |
| `postmortem_analysis.py` | **the engine's evidence packets and the whole-game scan** |
| `postmortem_api.py` | `/api/postmortem/*` router |
| `chess-frontend/src/styles/obsidian.css` | **the design system — token source of truth** |
| `chess-frontend/src/styles/shell.css` | **the layout all three modes share** — §11 |
| `chess-frontend/src/difficulty.ts` | the five named strength bands, shared by Play and Learn |
| `chess-frontend/src/hooks/useStacked.ts` | whether the layout is one column — the fitter needs to know |
| `chess-frontend/src/hooks/useTheme.ts` | light/dark/system preference |
| `chess-frontend/src/hooks/useBoardSize.ts` | how wide the board would *like* to be |
| `chess-frontend/src/hooks/useFittedBoardSize.ts` | how tall it is *allowed* to be — measures, §11 |
| `chess-frontend/src/formatText.tsx` | renders the `**bold**` Gemini emits, both chats |
| `chess-frontend/src/components/ChessBoard.tsx` | the real-game UI |
| `chess-frontend/src/components/Sandbox.tsx` | Learner Mode |
| `chess-frontend/src/components/PostMortem.tsx` | **Review — the orchestrator** |
| `chess-frontend/src/components/PostMortemDropzone.tsx` | the empty canvas and PGN ingestion |
| `chess-frontend/src/components/PostMortemMoveList.tsx` | the scoresheet, every ply clickable |
| `chess-frontend/src/components/PostMortemReport.tsx` | eval curve, accuracy, turning points |
| `chess-frontend/src/components/PostMortemChat.tsx` | the review conversation |
| `chess-frontend/src/moveQuality.ts` | **the grade palette, shared by Play and Review** |
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

### Frontend and backend deploy SEPARATELY — and skew is silent

Vercel builds from a push; Render is a separate service that has to deploy
too. Ship one without the other and the app looks broken in a way that blames
the user.

That has already happened once, on 2026-09-05: `master` was pushed with
Post-Mortem in it, Vercel picked it up, Render did not. The frontend then
offered the Review upload box, asked Render for `/api/postmortem/import`, got
a 404 because the route did not exist there yet, and rendered it as

> **Not Found — Try another file**

which reads as "your PGN is bad" and is nothing of the sort. The file never
reached a parser.

**Diagnose it in one line** rather than guessing. Same request, both backends:

```bash
curl -s -o /dev/null -w '%{http_code}\n' -X POST \
  https://zugzwang-api.onrender.com/api/postmortem/import \
  -H 'Content-Type: application/json' -d '{"pgn":"1. e4 e5","source_name":"t.pgn"}'
```

- `200` — Render has the current code.
- `404` — Render is behind. Dashboard -> **zugzwang-api** -> Manual Deploy ->
  Deploy latest commit, and check **Settings -> Branch** is `master` with
  Auto-Deploy on.

A route that predates the skew (`POST /api/sandbox/session`) answering 200
while the new one 404s is the signature: the backend is **up and healthy**,
just old. Do not go looking for a bug in the PGN parser, the Vercel rewrite,
or CORS - all three were fine, and all three were checked before the cause was
found.

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
10. **A server you did not start is not yours to kill.** Both runner scripts
    in §12 open with `pkill`, so restarting the backend takes down whatever
    another agent had running against it - mid-scan, mid-test, mid-demo. Check
    first (`pgrep -af 'port 8081'`) and reuse a healthy server rather than
    replacing it. See the section above this numbered list.
11. **A column sized to exactly the board cannot hold anything beside the
    board.** `.board-column` is `width: var(--ws-board-track)` and
    `.chess-board-wrapper` is pinned to that same track (shell.css §The board
    column), which is what makes the transport and meta rows line up with the
    board's edges. Anything else placed in `.board-row` therefore pushes the
    board frame straight out of its own column — Play's vertical eval bar did
    exactly that, and the board came to rest under the coaching tab strip and
    over the moves list. Indicators that flank the board belong on the strip
    UNDER it, reserved, which is what Learn and Review already do.
12. **`customSquareStyles` land on the square's INNER div**, not on the
    element carrying `data-square`. react-chessboard renders
    `<div data-square="e4" style="background-color: var(--board-light)">` and
    then a child div that gets your style. Reading the outer one back in a
    test returns the board colour for all 64 squares, so a working highlight
    looks exactly like a feature that was never wired up. Both verify tools
    read `el.firstElementChild`.
13. **react-chessboard opens its own promotion dialog on a drag.** Every
    promotion path in this app auto-queens deliberately (Play's
    `makePlayerMove`, Learn's and Review's `uciFor`), and without
    `autoPromoteToQueen` on the board the same move would ask a question when
    dragged and not when clicked. All three boards set it.
14. **A browser tab open across a long session goes stale.** HMR sockets drop,
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
| `GEMINI_POSTMORTEM_CHAT_MODELS` | chain 6 | **the review coach (§14)** |
| `POSTMORTEM_SCAN_DEPTH` | 12 | depth for the whole-game scan — one search per position |
| `POSTMORTEM_PROBE_DEPTH` | `STOCKFISH_DEPTH` | depth for one position the user asked about |
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

**All six model chains lead with a different model on purpose.** They share
one API key, and when moves and chat both led with `gemini-3.5-flash`, chat
immediately got HTTP 429 from move traffic. `test_scenario.py` asserts the
six leads stay distinct — if you retune, keep that true. `gemini-3.6-flash`
is last in every chain: it does not refuse, it *hangs*, so leading with it
spends the full timeout on every request.

---


## 6. Tests — 893/893

| file | what | needs |
|---|---|---|
| `test_gemini_move.py` | 9, mocked HTTP | — |
| `test_sandbox_state.py` | 41, move tree + sessions, pure | — |
| `test_player_state.py` | **41, per-player isolation, pure** | — |
| `test_scenario.py` | **89**, incl. a 480-position legality fuzz and mate-request verification | — |
| `test_decide_integration.py` | 6, real Stockfish + faked Gemini | Stockfish |
| `test_sandbox_api.py` | **87**, `/api/sandbox/*` end to end | Stockfish |
| `test_sandbox_narration.py` | 34, narration + parallel wiring | Stockfish |
| `test_accounts.py` | **85, guest mode + accounts-off + auth internals** | Stockfish + `DATABASE_URL` |
| `test_postmortem_state.py` | **65, PGN ingestion (incl. figurine notation) + the immutable game, pure** | — |
| `test_postmortem_api.py` | **72, `/api/postmortem/*` end to end** | Stockfish |
| `test_learning_loop.py` | **87, the store, the diagnosis validator, the event sink, pure** | — |
| `test_retest_bank.py` | **34, every re-test position re-certified at depth 20** | Stockfish |
| `test_learning_loop_api.py` | **83, `/api/learning-loop/*` end to end, coach faked** | Stockfish |
| `test_accounts_postgres.py` | **125, accounts ON: migrations, ownership, claiming, cross-account isolation, live-session isolation, the global AI boundary, retention, the account area (profile, preferences, password, deletion), password reset, rate limiting, security probes** | Stockfish + `DATABASE_URL` |

The suites that touch storage need `DATABASE_URL`, and they should be pointed
at a **disposable schema** rather than at `public`. They drive the real app
through TestClient, and guests write real rows now, so a run against `public`
leaves a scatter of guest games in the live database. `DATABASE_SCHEMA` is
read by `db.py`; `apply_schema()` creates it and `drop_schema()` deletes it,
which is also why `drop_schema()` refuses to touch `public`.

```bash
cd /mnt/c/Users/David/Documents/chess-app-v3.9
set -a; . ./.env; set +a
export DATABASE_SCHEMA="zwtest_$$"
/tmp/chessapp/bin/python -u test_gemini_move.py && \
/tmp/chessapp/bin/python -u test_sandbox_state.py && \
/tmp/chessapp/bin/python -u test_player_state.py && \
/tmp/chessapp/bin/python -u test_scenario.py && \
DISABLE_LANGFLOW=true /tmp/chessapp/bin/python -u test_decide_integration.py && \
DISABLE_LANGFLOW=true /tmp/chessapp/bin/python -u test_sandbox_api.py && \
DISABLE_LANGFLOW=true /tmp/chessapp/bin/python -u test_sandbox_narration.py && \
DISABLE_LANGFLOW=true /tmp/chessapp/bin/python -u test_accounts.py && \
/tmp/chessapp/bin/python -u test_postmortem_state.py && \
DISABLE_LANGFLOW=true /tmp/chessapp/bin/python -u test_postmortem_api.py && \
/tmp/chessapp/bin/python -u test_learning_loop.py && \
/tmp/chessapp/bin/python -u test_retest_bank.py && \
DISABLE_LANGFLOW=true /tmp/chessapp/bin/python -u test_learning_loop_api.py && \
DISABLE_LANGFLOW=true /tmp/chessapp/bin/python -u test_accounts_postgres.py
/tmp/chessapp/bin/python -c 'import db; db.drop_schema()'
```

The last line is not optional housekeeping. Leave it out and every run
accumulates another schema in the Neon project, and the free plan's storage
is finite.

Spell the thirteen out — a `for t in ...` loop inside `bash -lc "..."` has its
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
| `/api/postmortem/import` | 10/min |
| `/api/postmortem/*/analyse` | 6/min |
| post-mortem `/branch`, `/ai-move`, per-move analysis | 20/min |
| post-mortem `/chat` | 10/min |

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
4. **Lean on Gemini.** (Quota warning in §15.)
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

### `npx tsc --noEmit` is NOT the typecheck

Use `npm run build`. The root `tsconfig.json` is a solution file - `"files":
[]` and two project references - so a bare `npx tsc --noEmit` type-checks
**nothing at all** and exits 0 no matter what is broken. The real check is
`tsc -b`, which is what `npm run build` runs.

This is not hypothetical: a whole session's work was verified with
`npx tsc --noEmit` returning 0, and the Docker build then failed on two type
errors that had been there the entire time (a handler still typed
`ChangeEvent<HTMLInputElement>` after its control became a `<select>`, and a
field used in a component but never declared on its interface). A green bare
`tsc` means the command found no project to check.

```bash
cd chess-frontend && npm run build     # tsc -b + vite build; this is the truth
```

### Run the invariants first

```bash
node tools/verify/boardstate.mjs                # no browser, no server, ~1s
node tools/verify/ui.mjs                        # layout; dev server on :3001
node tools/verify/interaction.mjs               # drag, check, the endings
node tools/verify/ui.mjs http://localhost:3000  # or the container
node tools/verify/ui.mjs http://localhost:3001 --shots out/
```

There are three tools and they own different things. **`ui.mjs` owns layout**
— where things are and whether anything overflows. **`interaction.mjs` owns
what the board lets you do and what it says about itself** — that a drag only
ever offers legal squares, that the checked king is marked, that the three
endings raise the right layer and freeze the board, in all three modes, on
mouse and on touch, at six viewports. **`boardstate.mjs` owns the chess**:
22 positions through `boardState.ts` with no browser at all, which is the
cheap half of the pair and the one to run while iterating.

`interaction.mjs` is **119 checks** and `boardstate.mjs` **22**, both
currently passing. The last 12 are the transport's own ends (§20): the only
controls in Learn and Review that could be pressed with provably nothing to
do. The 22 include every distinction the two endings turn on:
mate with a block available, mate with a capture available, double check,
smothered mate, stalemate with the king boxed in, stalemate where another
piece can still move, and a king with no square that is not in check.

**ui.mjs is 73 checks, all currently passing**: console errors and overflow in both modes
and both themes; AA contrast on every text style; 44px touch targets under a
coarse pointer; and the Learner Mode layout invariants — board and tab row on
one line, the eval toggle moving nothing, the layout centred. Each of those
was a real bug on this branch, so the file is a regression net rather than a
checklist. Run it before claiming any UI work is done.

Play has its own section now, and the reason it needed one is the argument for
the whole file: Play was the only mode with no layout invariants, and Play is
the mode that shipped a broken layout. Its checks are that the board stays
inside its column, that it never reaches the coaching column, and that toggling
*Engine numbers* moves nothing — all three fail on the code that shipped, and
all three pass now.

Post-Mortem is covered by the same sweeps plus its own section: the three
Learner Mode layout claims repeated for `Review` (it has the same two-column
shape and would fail them the same ways), that stepping through a game resizes
nothing, and that the empty canvas is a real button large enough to drop a file
on. The tool imports a PGN itself — Morphy's Opera Game, inline in the file —
because an empty canvas exercises almost none of the mode.

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
- **...but `:text-is` matches an element's OWN text, not a child's.** Play's
  coaching tabs put their label in a `<span class="rail-label">`, so
  `.rail-stack button:text-is("Review")` matches *nothing* while
  `.rail-stack >> text=Review` matches. Costing half an hour once is what
  earns a line here: a zero-match locator looks exactly like a broken feature.
- **A raw `fetch` to `/api/postmortem/import` does not start the scan.**
  `postmortemService.importPgn` starts it; the endpoint alone does not. Import
  that way in a test and the Report tab correctly shows "Not analysed yet"
  forever, which reads as a bug in the Report tab.
- **Scope selectors to `.sandbox`** — the game pane stays mounted and hidden,
  so its buttons still match.
- **Wait out `animationDuration={300}`** before asserting on board DOM, or you
  read the *previous* position and think the move didn't apply.
- **Measure, don't eyeball.** The board-vs-panel misalignment in trap 8 was
  invisible in three rounds of screenshots and obvious the moment element
  bounding boxes were printed side by side.

---

## 11. UI state as it stands

**All three modes are one layout.** `styles/shell.css` owns the workspace, the
two-column grid, the identity row, the tab strip, the panel and the transport;
a mode's own stylesheet owns only what is genuinely different about that mode.
Before it there were three container widths (1320 / 1160 / none), three
stacking breakpoints (none / 980 / 1100) and three copies of the same grid
under three prefixes, kept in step by hand — which is to say, not kept in step.

**The board is the wide column.** The panel is capped at 420px rather than
being allowed half the row, and the panel takes its height FROM the board
column (`height: 0; min-height: 100%`) rather than contributing its own. That
is what keeps the two columns ending level and what stops a long explanation
growing the row.

Board sizing is `useBoardSize` for what the board would *like* (from the
viewport) and `useFittedBoardSize` for what it is *allowed* — the latter
measures the page's real overflow rather than modelling the layout, because
every hand-tuned chrome constant this project has had was wrong again the next
time a row was added under the board. All three modes use it now. It is
switched **off** while the layout is stacked (`useStacked`): stacked, the panel
below the board is what overflows, so correcting the board removes none of it
and the loop runs the board to its floor — Review measured a 236px board at
1024 before this. At 1920/1440/1280 **nothing scrolls**.

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

## 13. Accounts, identity and guest mode — built, on Postgres

Everything here is real and tested with `ACCOUNTS_ENABLED=true`. It ships with
that flag **off**. `test_accounts.py` proves the shipping configuration
(accounts refused, everyone a guest); `test_accounts_postgres.py` proves the
one we are not shipping yet, because "it is switched off" is not an argument
that the code beneath it works.

### The three lifetimes, which are not the same thing

Conflating these is how the guest contract gets misdescribed, so they are
named separately:

| | how long | ends when |
|---|---|---|
| **Guest id** | 1 year (`GUEST_COOKIE_MAX_AGE`) | the cookie expires, is cleared, or the signing secret changes |
| **Guest live session** | 2h idle / LRU cap (`PlayerStore`) | they stop playing — the board, the AI lock, the difficulty all die with it |
| **Guest retained data** | `GUEST_RETENTION_DAYS` (30) | the sweep deletes it, unless an account claimed it first |
| **Account data** | forever | the account is deleted |

So: **a guest's live session is ephemeral, their history is not.** The board
they were on is gone the moment they leave. The games they finished sit in
Postgres under their guest identity for thirty days, waiting to be claimed.

### The seam: `identity.py`

Every request resolves to **one opaque string**, and nothing downstream parses
it:

```
guest:8f2a1c…   an anonymous visitor.
user:42         a signed-in account.
```

`player_state.py` keys a player's game on it, `sandbox_api.py` and
`postmortem_api.py` key ownership on it, `learning_service.py` stores it
verbatim in `games.owner`. None of them knows what it means. That is what
makes turning accounts on a switch: it changes which string arrives.

It is a **middleware, not a dependency**, because a dependency can read a
cookie but cannot set one — only a response can.

#### HttpOnly is not authenticity

Both cookies are HttpOnly, which stops page scripts reading them and says
nothing about what the server accepts. The server used to accept any
`zw_guest` value beginning with `guest:`. Nobody could *guess* another
visitor's 128-bit id, but anyone could invent unlimited identities by editing
one cookie — harmless when a guest's data lived in a per-session in-memory
database, not harmless once guest history persists and forged identities
become rows.

So `zw_guest` is now carried as `<id>.<mac>`, HMAC-SHA256 under
`SESSION_COOKIE_SECRET`, compared with `compare_digest`. An id the server did
not mint does not verify and is replaced. **`SESSION_COOKIE_SECRET` must be
set and stable in a deployment** — change it and every guest cookie becomes
invalid, every visitor becomes a new guest, and any history waiting to be
claimed becomes unclaimable.

### Storage — `db.py`, `migrations/`

Postgres on Neon, one pool, opened lazily. Previously two SQLite files on
Render's ephemeral disk, which a redeploy deleted.

- **Migrations are numbered files in `migrations/`**, applied in filename
  order, recorded in `schema_migrations`. Each runs in its own transaction
  with the row that records it, so a failure applies nothing and records
  nothing. They only ever ADD — no DROP, no destructive ALTER. **Keep it that
  way**; a deploy hook nobody is watching runs these against real user data.
- **They run on boot** (`db.migrate()` in the startup hook) because a deploy
  step that can be forgotten will be. Idempotent, so it is a no-op normally.
- **The pool uses the DIRECT endpoint, never `-pooler`.** `DATABASE_URL` may
  be given as either; `direct_dsn()` derives it. Not a preference — see the
  trap below.
- **No database is not fatal.** Play still works, learning writes degrade to
  recording nothing, startup logs an ERROR, and `/api/health` reports
  `database: "not_configured"` or `"unreachable"`. Refusing to boot would turn
  a storage outage into a total outage.

> ⚠️ **Never let this app talk to Neon's pooler.** The pooler multiplexes many
> clients onto few server connections and **session state travels with them**,
> so a `SET search_path` from anything else against the same project — a test
> run, a psql session — is handed to whoever borrows that connection next.
>
> This bit twice. First loudly: a test run left pooled connections pointed at a
> schema it had already dropped and every query failed with
> `relation "users" does not exist` while the tables sat untouched in `public`.
> Then quietly, which was worse: an account was created, answered a login, and
> then could not be found — it had been written into a **test schema** by a
> process configured for `public`. Nothing failed. `/api/health` said `ok`,
> because `SELECT 1` works in any schema.
>
> Measured here: six fresh pooled connections, six leaking; six direct, all
> clean. Neon's pooler also refuses a startup `options=-c search_path=…`, so
> pinning at connect time is not available. **The fix is the direct endpoint**
> — `db.py` uses it for the pool, and `temporary_schema()` for the same
> reason. The per-connection `SET` is what makes `DATABASE_SCHEMA` work, not
> what makes it safe.

> ⚠️ **Never schema-qualify in a migration.** `on public.games` ignores
> `search_path` and builds the index on whatever `public` holds — so migrating
> a disposable test schema reached into the live one, and the test schema
> silently had no owner index. Write `on games`.

### Ownership

`games.owner` holds the identity string. Ownership lives **only** on `games`;
`moves` inherit through `game_id ON DELETE CASCADE`, which is what makes both
claiming and the retention sweep single-table operations that cannot leave
orphans. `owner` is `NOT NULL` — a row with no owner is not a state the
database will hold.

Two categories only, guest-owned and account-owned, distinguished by the
prefix already in the string.

### Guest mode — owned rows, not a private database

**`guest_learning.py` is gone.** A guest writes to the same tables as anyone
else, under their own owner, and isolation is the owner column on every query
rather than a separate database. Same class, same queries, same reweighting
rule for everybody — the property the old design cared about most was that
there was no second implementation to drift, and that survives.

Two guardrails came with persisting guests:

- **`purge_unclaimed_guest_games()`** deletes guest-owned games older than
  `GUEST_RETENTION_DAYS`, run by a daily loop in the app. An asyncio task, not
  a scheduler: what has to happen is one DELETE, and missing a day means data
  lives a day longer. Claimed games no longer match, having been rewritten to
  `user:…`.
- **`reweight_candidates()` reads account-owned games only** (`owner LIKE
  'user:%'`). Guests benefit from the global pool and do not feed it until
  they sign up. Otherwise anyone who can open the URL could steer what the AI
  plays against everyone else, repeatedly and anonymously.

### Claiming — `LearningService.claim_guest_games()`

Runs on signup, login, and the Google callback. One transaction:
check `claimed_guests`, rewrite `games.owner`, insert the ledger row.

- **Transactional** — no half-migrated ownership.
- **Idempotent** — the second call for a guest finds it claimed and moves
  nothing, so a retried request or a duplicate callback is harmless.
- **Race-safe** — two simultaneous claims total one claim; tested with real
  threads.
- **Strictly scoped** — the guest identity comes from `identity_of(request)`,
  never from anything the caller sent. There is no endpoint that takes a guest
  id, and there is no "claim unowned games" path.

Signing in does **not** carry over the in-progress board. The identity
changes, `player_state` hands back that account's game, and that clean switch
is deliberate — the alternative's failure mode is two identities on one board.

### Accounts — real, and switched off

`auth_service.py`: PBKDF2-HMAC-SHA256 at 600k iterations, per-row iteration
counts so raising the cost rehashes on sign-in instead of locking people out,
session tokens stored only as SHA-256 hashes, case-insensitive uniqueness on
username **and** email by index rather than check-then-insert, one error
message for "no such user" and "wrong password".

Sign-in accepts **username or email**. Email is optional on the model
(existing rows predate it) and unverified — see the two consequences in
DEPLOY.md.

**Google sign-in** (`google_oauth.py`) is built and unconfigured, the same
pattern as accounts themselves: no `GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET`
means the routes answer 503 and `/api/auth/config` reports `google: false`.
Server-side authorization-code flow — the browser never holds a credential.
Accounts linked on Google's immutable `sub`, never on the email, because
emails get reassigned. An account created through Google has `has_password =
false` and cannot be signed into with any password, and refusing that costs
the same wall-clock time as a wrong password so it cannot be timed.

A Google sign-in whose email matches an existing password account is
**refused, not linked** — auto-linking on an address nobody verified is how
one person's account gets handed to another. That unlocks with email
verification.

While `ACCOUNTS_ENABLED` is not `true`: signup and login answer **503**, not
404, and **no account resolver is wired into the middleware at all**, so the
disabled state is enforced by what is wired and not only by what routes say.

### If you switch accounts on

`ACCOUNTS_ENABLED=true` is the whole switch. Do not, until DEPLOY.md's list is
done. **Storage is no longer on it.** Still open: password reset, email
verification, `COOKIE_SECURE`, a hard Gemini spend cap, and
`LANGFLOW_AUTO_LOGIN`.

### Frontend

`AccountMenu.tsx` renders the real UI and, while accounts are off, a notice
rather than a hidden or greyed-out control. The wording comes from the server
(`unavailable_message`) so what a user is told cannot drift from what the
backend does. **It no longer says "nothing is saved", because that is no
longer true** — it says the history is tied to this browser and will come with
them when they sign up. If you change one, change the other.

`http.ts` does two load-bearing things: `credentials: 'include'` on every
call, and establishing identity once before any other call. Without the
second, a new visitor's parallel first requests each mint a separate identity
and everything created under the losers is orphaned. **Don't remove the
bootstrap.**

### The account surfaces — `/signin`, `/signup`, `/settings`

Real routes, added with `react-router-dom` in `main.tsx`. `App.tsx` is
untouched as the three-mode shell and the account pages are its siblings,
not something it has to know about.

They are pages rather than dropdown panels because signing in is the moment a
person's history stops belonging to one browser, and because a URL can be
linked to, bookmarked, and **returned to after a Google round trip** - which a
dropdown cannot. The header now says who you are and links; the only thing
left in a dropdown is the accounts-are-off notice, which is an explanation
rather than a task.

The in-header sign-in form is **deleted**, not hidden. Two signup forms is two
places to add a field, and the one that gets forgotten is the one someone is
looking at.

> ⚠️ **Client-side routes need a server-side fallback or they 404 on
> refresh.** nginx already had `try_files $uri $uri/ /index.html`. Vercel
> needed an explicit catch-all rewrite added to `vercel.json`, ordered AFTER
> the `/api` proxy - reverse them and every API call returns the HTML shell.

### Account settings — `settings_service.py`, `user_settings`

The five preferences (piece set, coordinates, engine numbers, move grading,
open rail panel) were `localStorage` keys read directly by `ChessBoard.tsx`.
Right for a guest; wrong for an account, once everything else follows a person
between devices and only their board does not.

`services/preferences.ts` is now the accessor. `localStorage` is still what
the app reads synchronously during render - no loading state, no flicker - and
when signed in it is additionally a cache: pulled down at sign-in
(`hydrateFromAccount`), pushed up on change. Signed out, the push is skipped
and behaviour is exactly what it was.

- **Signing UP seeds the account from this device** rather than pulling empty
  defaults down over it. Someone who spent an evening as a guest choosing a
  board keeps it.
- **Signing IN pulls the account's values down, then reloads.** The reload is
  not laziness: these are read during render, and React state already mounted
  would not see them change.
- The column is JSONB and the database constrains nothing in it, so
  `settings_service.ALLOWED` is the constraint. **Add a preference there when
  you add one to the hook, or it is silently dropped.**

### Password reset — `email_service.py`, `password_resets`

Mailjet-backed, and off unless `MAILJET_API_KEY`, `MAILJET_SECRET_KEY` and
`MAILJET_FROM_EMAIL` are all present - a partial configuration counts as off,
because a half-configured mail system looks exactly like a working one from
outside. Mailjet's Send API answers **200 with a per-message status**, so
`send()` checks that status rather than the HTTP code; a rejected recipient or
an unvalidated sender otherwise arrives looking like an accepted request.

Mailjet has **no shared sandbox sender** - the From address must be validated
in their dashboard before anything sends at all.

The one invariant to protect
if any of it is edited: **`/forgot-password` answers identically whether the
address is registered, unregistered, malformed, a Google-only account, or the
mail provider is down.** Every one of those differences is a free way to check
whether somebody has an account here, and three of them are easy to
reintroduce by "improving" an error message.

- `secrets.token_urlsafe(32)`, **SHA-256 stored, never the token**.
- 45 minutes, single use, and requesting again kills the earlier link.
- Unknown / expired / used all answer with one message.
- On success every session for the account is ended, and the reset does not
  sign the caller in - the token came by email.
- A Google-only account gets an explanatory **email**, never a different HTTP
  response.
- Two buckets: per IP, and per target address (`forgot_by_email`), because the
  per-IP one does nothing against a distributed caller aimed at one inbox. The
  per-address refusal answers **200**, not 429.

**Email being unavailable blocks nothing but password recovery.** Three
states, all tested: never configured, `EMAIL_ENABLED=false` (credentials
present, sending deliberately off), and working. `/api/health` reports which,
and startup says it in words. When email is off the reset endpoint says so —
honest *and* still uniform across every address, because it depends on
configuration rather than on the address. **Do not "improve" that into a
message shown only when a send was attempted**; that is the enumeration oracle
the generic message exists to close.

> ⚠️ **A 401 from Mailjet's send endpoint does not mean the key is wrong.** A
> suspended or under-review account answers 401 to `/v3.1/send` while the
> account REST API still answers 200 and lists senders as Active. That is how
> it presented here. Set `EMAIL_ENABLED=false` until it is resolved rather
> than paying a ten-second timeout per request.

> ⚠️ **Never log a reset link.** `email_service.log_reset_link_locally()`
> exists for local development and is guarded twice - `RESET_LINK_TO_LOG=true`
> AND not production. Logs get shipped, tailed and pasted into chat, and a
> reset link in one is an account takeover.

### The rate limiter has a test seam

`rate_limit()` exposes its bucket as `dependency.limiter`, and `RateLimiter`
has `reset()`. Nothing in the app touches either. It exists because the
account suite signs in dozens of times from one address and was filling the
login bucket partway through, so every later sign-in failed with a 429 that
read exactly like a broken password. The limits are proven deliberately in
that suite's rate-limiting section rather than assumed.

### What is NOT persisted, deliberately

The learning loop's corrections and practice attempts, sandbox sessions and
Post-Mortem reviews are all still **in memory**, keyed by identity, swept on a
TTL. They are not claimed at signup and do not survive a restart. That was a
scoped decision for this milestone, not an oversight: `CorrectionStore` is one
class with the same seam `learning_service` had, so persisting it later is a
contained change. Until then, do not tell a user their corrections will
follow them into an account.

---

## 14. Post-Mortem — bring your own game

**What it is:** the third mode. An empty canvas takes a PGN by drag-and-drop or
file picker; the server replays it move by move and hands back a workspace where
every ply is navigable, every move is graded, any position can be branched from
to play something else, and a coach answers questions about the position on the
board using the engine's own evidence.

**User's decisions — decisions, not suggestions:**

1. **The whole game is scanned in the background on import.** Not on demand.
   One Stockfish search per position, at `POSTMORTEM_SCAN_DEPTH` (12), so the
   grades and the eval curve are there when the user goes looking.
2. **A branch is answered automatically, at full strength.** "What would have
   happened if I had played this" is a question about best play, and making the
   user click again to hear the answer puts a step between the question and the
   point of asking it.
3. **The mode is called `Review` in the header.** Post-Mortem is the internal
   name.
4. **A review lives server-side**, in a store with the sandbox's sweep-and-cap
   semantics, and its id is remembered in `localStorage` so a reload resumes.

### Where the chess state lives, and why it is the sandbox's tree

`postmortem_state.py` is pure — python-chess and nothing else — and an imported
game **is a `MoveTree`** (the sandbox's, imported directly). A flat list of
plies cannot represent two continuations from one position, which is exactly
what "play a different move" is. The tree never deletes anything, so branching
cannot damage the import.

`PostMortemGame.mainline` is the list of node ids written once at replay time.
It is the definition of "the game as played": every "am I on the game or on a
what-if" question in the API and the UI is a set-membership test against it, and
nothing mutates it. **This is the claim the mode rests on**, and
`test_postmortem_state.py` asserts it from several directions.

Two consequences worth knowing:

- **Replaying the game by hand does not branch.** `MoveTree.play` reuses an
  existing child, so stepping through with the board rather than the buttons
  walks the game forward instead of creating a phantom variation.
- **`/forward` is not `MoveTree.forward()`.** The tree follows the most
  recently created child, which is right for the sandbox and wrong here: after
  branching at move 17, stepping forward from move 16 must walk the *game*.

### The analysis layer

`postmortem_analysis.py` is the only place in the mode that talks to Stockfish.
It produces one **evidence packet** per half-move — ply, SAN, both FENs, both
evaluations in White's absolute frame, the engine's preferred move and its own
principal variation, the centipawn loss, the grade, and **the depth that
produced all of it**. The UI and the coach both receive that dict and neither
can state a number that is not in it. A field the engine did not produce is
null, and the interface says so rather than filling it in.

**The scan costs one search per position, not two per ply.** Grading each move
with `move_quality.classify_move` searches the position before the move and
then the position after it — and the second is the position the next ply will
search again. Walking the game as a sequence of positions instead, each result
is used twice: as "what was available before move i" and as "what move i-1
achieved". 81 searches instead of ~160 for a 40-move game, with the eval curve
falling out of the same pass.

**The grading rules are not reimplemented.** `move_quality.grade_from_scores`
was split out of `classify_move` so both paths share one implementation of the
thresholds — a blunder in a review is a blunder in play, by construction. The
one grade the scan cannot produce is **Great**, which is a claim about the
second-best move and needs a MultiPV search this pass does not do; the summary
declares that rather than absorbing it silently into "Best".

Two display rules that came out of driving it:

- **Centipawn loss is on the `MATE_SCORE` scale.** Rendered as pawns, throwing
  away a mate in three read as "-93.0", which is not a quantity of anything.
  `moveQuality.lossText` says "missed mate" / "lost a forced win" instead.
- **The eval curve is clamped to ±8 pawns**, the same compression the eval bars
  use, or one blunder pins the line to the edge and it never moves again.

### The API

| endpoint | does |
|---|---|
| `POST /import` | PGN text in, a replayed game and a review out |
| `GET \| DELETE /game/{id}` | the whole state / close it |
| `POST /game/{id}/{goto,back,forward}` | navigate — `forward` follows the game |
| `POST /game/{id}/branch` | play a different move from here |
| `POST /game/{id}/ai-move` | the engine answers **inside a branch** |
| `POST /game/{id}/return` | back to the game, where the branch left |
| `POST /game/{id}/analyse` | start the whole-game scan (idempotent) |
| `GET /game/{id}/analysis` | progress, grades, curve and summary together |
| `GET /game/{id}/analysis/{node_id}` | one move's evidence, full depth |
| `POST \| GET /game/{id}/chat` | the review coach, and its transcript |

**`/ai-move` refuses on the real game with a 409.** What happened next there is
recorded, not decided; an AI reply would be writing over history. The way
forward through the game is `/forward`.

**Import refuses anything it cannot replay exactly**, with a message written for
a chess player rather than a parser author — an ambiguous SAN gets a different
message from an illegal move, because they are different problems for the person
who exported the file. This matters beyond politeness: a PGN is the only path in
the app by which a stranger's bytes reach the shared engine, and `move_quality`
documents that Stockfish *segfaults* on an unreachable position rather than
rejecting it.

### The coach

A third persona in `gemini_chat_service.py`, on its own model chain
(`GEMINI_POSTMORTEM_CHAT_MODELS`, a sixth caller on one key so a sixth distinct
lead). The differences from the other two are the mode:

- The real game's chat is the **opponent** and withholds a winning move.
- The sandbox coach teaches a position built for teaching.
- This one talks about a decision the user actually made in a game that is
  already over. Nothing is withheld, and the tense is not a detail: *"you could
  have played"*, never *"you should play"*.

It is given the evidence packet, not the PGN. A model handed a raw game invents
the analysis, and an invented centipawn number is precisely what this mode would
be judged on. It is also told what to do when a field is missing: say so rather
than estimate.

### The UI

`PostMortem.tsx` orchestrates; the dropzone, move list, report and chat are
their own files. The layout is the sandbox's — board column pinned to the
board's width, heading in its own grid row, panel capped by its grid track — for
the reasons §11 gives, and it is checked by the same invariants (§10).

Two states, and the interface is never ambiguous about which: **the game** (a
normal frame, "Move 15... Nxd7 of 17") and **a what-if** (an amber frame, "What
if: a3 Qxb3 instead of move 16", a "Back to the game" button that only exists
here, and a chat context line naming the same move number the board strip does).

**The empty canvas's note about where the game goes is a load-bearing claim,
not decoration.** It used to say the game "stays on this machine and on the
server", which reads as *it goes nowhere else* and is not what this mode does:
the review chat hands Gemini the FEN, the line in SAN, the branch, and the
PGN's `White`/`Black` headers. It now says so. If the coach's inputs ever
change, that sentence changes in the same commit — a claim about someone's
data is the one kind of copy that has to be checked against the code rather
than written from intent.

---

## 15. Known open issues

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
- **An imported review dies with the process, and after an hour idle.** By
  design for now — the build spec says do not overbuild persistence yet — but
  it means a review is not somewhere to keep a game. The frontend remembers the
  review id across a reload, so a refresh resumes while the server still has it;
  a miss is a 404 and lands back on the empty canvas.
- **Underpromotion in a branch is not offered.** A branch move promotes to a
  queen without asking, because stopping to ask interrupts the one interaction
  the mode exists for. A deliberate gap, not an oversight.
- **Sandbox sessions die with the process** — by design, no persistence layer.
  Note the frontend now *remembers the session id* across a reload, so a
  refresh resumes the same board when the server still has it; a miss is a 404
  and opens a fresh one.
- **`LANGFLOW_AUTO_LOGIN=true`** grants unauthenticated superuser access.
  Mitigated by the compose profile (it doesn't run), not fixed.
- **The `Correct` tab is hard to find, and its empty states do not help.**
  It needs a loaded game *and* a board sitting on a real move; meet neither and
  you get two empty states in a row with no route out of them. The turning
  points in Report are the intended way in and do not say so. See §21.
- **Three of the eight correction themes have a re-test; five do not**, because
  the engine would not certify a single right answer for the quiet ones (§21).
  Eleven positions total. Not a bug - but it is the ceiling on how much of the
  loop most corrections can actually complete.
- **The folder is still named `chess-app-v3.9`** (§1).

---

## 16. Playtest — what QA actually established

A full-service playtest of the `ui-overhaul` build on 2026-09-05: the three
modes driven in a browser, the APIs probed adversarially, and every service
failure-injected. The brief is `~/Downloads/Zugzwang Full-Service Playtest &
Bug-Fix Mission.md`. Verdict: **READY WITH MINOR ISSUES**.

### Fixed, with regression tests

| sev | what was wrong |
|---|---|
| P2 | **Figurine PGN imported a different game.** `1. ♘f3` is a knight move; python-chess drops the symbol it cannot read and parses the rest as the PAWN move f3, leaving `game.errors` **empty** — so `parse_pgn`'s validation, which exists precisely to refuse a game it cannot replay exactly, could not see it. Now mapped to letters before parsing (`_defigurine`), header values untouched. |
| P2 | **"Make AI move" was enabled, prominent and did nothing.** `handleMakeAIMove` returns silently when it is not the AI's turn, which on a fresh game is always. Pre-existing, but the overhaul had promoted it to the mode's primary action. Now disabled with a reason, and demoted to a normal control — the coach answers on its own, so this is the manual nudge, not the main thing you do in Play. |
| P2 | **The Sandbox told students to win lost positions.** `description` is written by the model *before* the position is built. "Black is completely winning" produced a lone knight against a queen and two rooks and said "find the winning plan for black". The honest correction was already computed, already in `notes`, already held in state — and never rendered. Now shown, in words rather than centipawns, flagged amber via `favor_met`. |

### Checked and clean — do not re-litigate these without new evidence

- **Branching never mutates the canonical game.** The mainline is byte-identical
  after branching and returning. Hostile branch input is rejected; unknown ids
  are 404, not 500.
- Malformed, empty and oversized PGN all fail gracefully.
- A failed move / AI move / navigation request recovers, is surfaced, does not
  corrupt the board and does not stick on "Thinking".
- No state leaks between the three modes; each survives a reload.
- No horizontal overflow across 3 modes x 10 widths (1920 down to 390).
- axe-core: 0 violations, 3 modes x 2 themes. Every focusable stop rings.

### Known and deliberately left

- **P3 — a scenario `description` is still written before the position exists.**
  Mate requests are verified and unmet `favors` requests are flagged, but a
  request like "a tricky middlegame" makes no claim anything can check. The real
  fix is to write the description *from* the built position, which is a design
  change rather than a bug fix.
- **P3 — 16 eslint errors** (`no-explicit-any`, empty blocks) in
  `chessService.ts`, `types/chess.ts` and `ChessBoard.tsx`. Identical to the
  pre-overhaul baseline; verified by linting `postmortem`. Not this work's.

### Four things that LOOK like bugs and are not

Each of these cost real time before it turned out to be the test's fault. They
will cost the next agent the same time unless it reads this.

1. **A review 404s the instant it is created** — if the client does not carry
   the `zw_guest` cookie. Reviews are scoped per identity (`identity.py`); a
   browser sends it automatically, `urllib`/`curl` do not. Use a cookie jar.
2. **Import starts returning 429** — `limit_postmortem_import` is 10/min.
   Navigation (`forward`/`back`/`goto`) is *not* rate-limited, so stepping
   through a game is never throttled, however fast you click.
3. **Learner Mode's "AI move" fires request after request** — that is what it
   is: auto-play, both sides, until stopped. It is not a duplicate-request bug.
   Measure *concurrent* in-flight requests if you suspect one; the correct
   answer is a maximum of 1.
4. **The Report tab sits on "Not analysed yet" forever** — a raw `fetch` to
   `/api/postmortem/import` does not start the scan; `postmortemService.importPgn`
   does. Import that way in a test and the tab is right and the test is wrong.

---

## 17. How the user works

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

---

## 18. Product & Business Doctrine (persistent)

> Everything below this line is the founder's standing product/strategy
> context for Zugzwang, pasted in whole. Its own heading numbering (1–30)
> is internal to this doctrine and independent of the engineering sections
> above it. Consult it before substantial product, UX, architecture, or
> feature decisions, and preserve its principles unless the founder
> explicitly changes them.

# Zugzwang — Persistent Product & Engineering Context

> **This file is persistent context for Claude Code sessions working on Zugzwang.**
>
> Treat this as product/engineering doctrine, not a one-off task description. Before making substantial product, UX, architecture, or feature decisions, consult this file and preserve its principles unless the founder explicitly changes them.

---

# 1. Product Identity

**Zugzwang** is an AI-native live chess learning product.

The long-term product is **not** intended to become:

* another generic chess website;
* a Stockfish wrapper;
* an LLM chatbot with a chessboard;
* an AI opponent with personality;
* a generic analysis dashboard;
* a puzzle library with an AI label.

The strategic product thesis is:

> **Zugzwang should become the most trustworthy system for turning a player's repeated chess decisions into measurable improvement.**

The core loop is:

> **Play or import → analyze → diagnose → understand → practice → re-test → update player model → play again**

The defining promise is approximately:

> **Bring Zugzwang your games. It remembers what you repeatedly misunderstand, explains the exact issue, gives you targeted correction practice, and shows whether that mistake is actually disappearing.**

Do not reduce this vision to simply "AI chess coach." That category is increasingly crowded.

---

# 2. Strategic Wedge

Market research indicates that generic:

> **Stockfish/engine analysis + LLM explanation**

is rapidly commoditizing.

The strongest opportunity is a **longitudinal correction system**.

Zugzwang should become exceptionally good at:

1. Detecting recurring decision patterns across a player's games.
2. Identifying the underlying issue rather than merely labeling individual inaccuracies.
3. Explaining the issue using concrete chess evidence.
4. Turning the issue into a small, targeted correction exercise.
5. Re-testing the player independently.
6. Measuring whether the same pattern decreases in later games.

The strategic gap is primarily an **outcome/trust gap**, not a feature gap.

The eventual product should be able to tell a player, with evidence:

> **"This is the mistake you keep repeating. This is why it happens. This is how to correct it. And here is evidence that you are getting better."**

---

# 3. Initial Customer

The initial target customer is:

> **An adult online chess player who plays regularly, has plateaued or feels inconsistent, has already tried engine analysis or generic puzzles, and wants a coach that remembers their actual decisions.**

An initial validation range of roughly **800–1800 online rating** is reasonable, but this is a hypothesis rather than a hard product boundary.

Do not initially attempt to optimize simultaneously for:

* complete beginners;
* casual entertainment players;
* titled players;
* tournament competitors;
* schools;
* coaches;
* families.

The initial wedge must be narrow enough to validate.

---

# 4. Current Product Reality

The current product is a **Vercel-stage deployed prototype**.

It already demonstrates promising ingredients including:

* Play mode;
* adjustable AI strength;
* move grading;
* natural-language coaching tied to board positions;
* review/progress surfaces;
* Learn-mode sandbox;
* engine settings;
* line exploration;
* guest access;
* engine numbers being off by default.

However, the current build is a starting point, not proof of product-market fit.

Known strategic weaknesses:

* capabilities can feel like a collection rather than one coherent improvement system;
* there is not yet a dominant first-session outcome;
* progress/history needs to become meaningful;
* persistent player memory is not yet the center of gravity;
* the full correction loop is not yet proven;
* some integrations/capabilities require production validation;
* current differentiators are not yet defensible.

The critical transition is:

> **From a collection of chess/AI capabilities → to a coherent longitudinal improvement system.**

Do not expand surface area merely because a feature is technically interesting.

---

# 5. Flagship Vision

The flagship experience should eventually center on:

> **Today's Correction**

rather than a generic dashboard or blank chessboard.

Example:

> "In your last 18 rapid games, you repeatedly moved before checking your opponent's forcing reply after an exchange. It appeared in 5 comparable positions. You accepted this diagnosis twice and solved 3 of 5 practice positions. Let's test it once more."

The player should be able to:

* open supporting games;
* inspect exact board evidence;
* explain what they intended;
* disagree with the diagnosis;
* correct the system's interpretation;
* practice a fresh position;
* receive hints when needed;
* re-test independently;
* see whether the pattern is actually declining.

After ~10 games:

> Zugzwang should understand the player's active correction themes.

After ~100 games:

> Zugzwang should understand recurring patterns across phases, time controls, openings, and situations, with evidence.

After months:

> Zugzwang should become a longitudinal improvement record rather than merely an analysis tool.

---

# 6. Player Model

The player model is a central product and engineering concept.

It should remember **structured, evidence-backed facts**, not vague personality judgments.

## Strong evidence candidates

* recurring error categories;
* phase-specific patterns;
* time-control-specific patterns;
* evaluation swings;
* recurring structures;
* practice performance;
* re-test transfer;
* hint dependence;
* response time;
* goals;
* diagnosis feedback.

## Reasonable inferences after repeated evidence

* missed-threat patterns;
* calculation-depth problems;
* endgame gaps;
* opening knowledge gaps;
* time-management patterns;
* explanation formats that work best;
* preferred study cadence.

## Do not infer prematurely

Do not make unsupported claims such as:

* "You are impatient."
* "You lack confidence."
* "You are an aggressive player."
* "You always panic in time trouble."

Do not infer psychological traits from a handful of games.

Do not infer intent without asking.

Every important player-memory/pattern record should ideally contain:

* evidence IDs;
* confidence;
* first observed date;
* last observed date;
* supporting games/moves;
* user confirmation status;
* contradiction status;
* decay/review rules.

The player should be able to:

* inspect important memories;
* correct them;
* reject them;
* understand why Zugzwang believes something.

**Trust is more important than apparent intelligence.**

---

# 7. Correction Cards

The fundamental product artifact should be a **Correction Card**.

A good correction contains:

* What happened?
* What did the player appear to be trying to do?
* What did they fail to notice?
* What evidence supports the diagnosis?
* What should they check next time?
* What practice will reinforce the correction?
* How confident is Zugzwang?
* Has this appeared in prior games?

The player should be able to respond:

* "That diagnosis is wrong."
* "That isn't what I was trying to do."
* "Show me the supporting games."
* "I already understand this."
* "Remind me later."

The correction must be actionable, evidence-backed, and revisitable.

---

# 8. Product Roadmap Doctrine

Development should proceed through meaningful product eras rather than arbitrary version numbers.

## Era 0 — Harden Reality

Goal:

> **One reliable post-game correction flow.**

Priorities:

* PGN ingestion;
* server-side legal replay;
* persistent games;
* immutable game versions;
* deterministic turning-point selection;
* evidence-grounded Correction Card;
* one practice exercise;
* independent re-test;
* feedback controls;
* analytics;
* error tracking.

---

## Era 1 — Retention Through Repeated Correction

Goal:

> **Give the player a reason to return after every serious game.**

Priorities:

* correction queue;
* recurring-theme clustering;
* "you have seen this before" links;
* spaced re-tests;
* weekly review;
* user-editable diagnosis labels;
* multiple game sources;
* before/after trends.

---

## Era 2 — Persistent Personalization

Goal:

> **Make Zugzwang materially better than generic analysis.**

Priorities:

* "Your next correction" home;
* time-control/phase segmentation;
* intent questions;
* confidence calibration;
* monthly priorities;
* user-editable memories;
* cross-platform history;
* personalized explanation depth.

---

## Era 3 — Measurable Improvement

Goal:

> **Make "did this work?" a first-class product answer.**

Priorities:

* fresh-position re-tests;
* identified/practiced/recognized/transferred/persistent states;
* comparable-position sampling;
* cautious before/after reports;
* correction history and decay.

Do not make unsupported rating-improvement claims.

---

## Era 4 — Coaching Depth

Only after the core correction loop works:

* personalized explanation calibration;
* adaptive hints;
* time-management coaching;
* opening preparation based on actual weaknesses;
* voice;
* coach collaboration;
* assignments/review links.

---

## Era 5 — Flagship Ecosystem

Eventually:

* cross-platform history;
* live play calibrated to known weaknesses;
* coach/creator tools;
* portable player learning records;
* mobile;
* club/academy plans;
* international distribution.

Do not build this early.

---

# 9. BUILD NOW / SOON / LATER / DON'T BUILD

## BUILD NOW

1. Canonical post-game correction flow.
2. Server-side legal replay.
3. PGN import.
4. Immutable game versions.
5. Deterministic turning-point detection.
6. Evidence-grounded Correction Cards.
7. Practice + independent re-test.
8. Persistent history.
9. Feedback/diagnosis correction.
10. Product analytics.
11. AI/engine cost monitoring.
12. Privacy/export/delete.

## BUILD SOON

1. Recurrence clustering.
2. Correction queue.
3. Spaced re-tests.
4. Multiple import sources.
5. User-editable player model.
6. Weekly review.
7. Paid entitlements.
8. Coach pilots.
9. Creator-shareable diagnoses.

## BUILD LATER

1. Live weakness-targeted sparring.
2. Voice.
3. Personalized opening preparation.
4. Mobile.
5. Club/academy plans.
6. Advanced time-management coaching.
7. Community features.

## DO NOT BUILD NOW

* broad social network;
* course marketplace;
* custom chess engine;
* generic chatbot as the primary product;
* multiple AI personalities;
* native app before web retention is proven;
* real-time assistance in rated games;
* Kubernetes/Kafka/microservices for premature scale;
* a vector database merely because it is fashionable.

Every proposed feature must be justified against the core correction loop.

---

# 10. Architecture Principles

The early production architecture should favor a:

> **Modular monolith + workers**

rather than premature distributed architecture.

A sensible evolution includes:

* Next.js/Vercel for frontend and thin API/BFF where appropriate;
* managed authentication;
* managed Postgres;
* object storage;
* durable job queue;
* separate worker service for long-running work;
* Stockfish workers;
* LLM gateway/model routing;
* structured logging;
* error tracking;
* feature flags;
* CI/CD.

Do not run long-running Stockfish or LLM workflows inside short-lived Vercel request handlers.

---

# 11. Canonical Chess State

The server must be authoritative for chess state.

For every imported/replayed game:

1. Parse PGN.
2. Replay every move legally.
3. Store FEN before and after every ply.
4. Validate castling.
5. Validate promotion.
6. Validate en passant.
7. Validate check/termination/result.
8. Quarantine malformed games.
9. Record parser/chess-library versions.

Never allow the LLM to be the authority for chess legality.

---

# 12. Engine Architecture

Use a two-pass model.

### Pass 1

Cheap scan across many positions.

### Pass 2

Deep analysis only on selected turning points or explicit requests.

Cache engine results using relevant dimensions such as:

* normalized position;
* Stockfish version;
* engine configuration;
* depth/time profile;
* MultiPV settings.

Never treat a shallow, timed-out, and deep engine result as equivalent.

Stockfish should be authoritative for objective chess evaluation where appropriate.

Zugzwang sells:

> **prioritization + understanding + correction + learning**

not centipawns.

---

# 13. AI Architecture

Zugzwang should be:

> **A deterministic chess system with probabilistic coaching around it.**

## Deterministic layer owns

* board state;
* legal moves;
* FEN/SAN/UCI;
* game replay;
* engine evaluations;
* evidence references;
* entitlements;
* analysis status.

## Probabilistic layer handles

* pedagogical wording;
* candidate causes;
* prioritization;
* exercise framing;
* conversational interaction.

Never simply send raw PGN to an LLM and ask it to coach the player.

Create an **evidence packet** containing:

* game/move IDs;
* FEN before/after;
* played move;
* best move/principal variation;
* evaluation before/after;
* engine depth/nodes/time;
* relevant phase/opening data if derived;
* prior player patterns;
* user intent;
* uncertainty constraints.

Require structured model output such as:

* explanation;
* category;
* candidate cause;
* correction;
* confidence;
* evidence IDs;
* uncertainty;
* suggested practice question.

Validate model output before displaying it.

---

# 14. Model Routing

Use different model classes for different jobs.

Prefer:

* cheap/fast models for classification, tagging, rewriting, and routine interactions;
* stronger reasoning models for difficult multi-game synthesis and ambiguity;
* deterministic templates for routine/fallback explanations;
* Stockfish for chess truth.

Do not use the strongest model for every operation by default.

The LLM itself is not the moat.

---

# 15. Trust Architecture

The largest product risk is:

> **A system that sounds intelligent while teaching something wrong.**

The explanation pipeline should be:

1. Create a versioned evidence packet.
2. Generate structured output.
3. Validate schema.
4. Verify cited moves, scores, and evidence IDs.
5. Reject unsupported numerical or causal claims.
6. Validate legality of suggested moves.
7. Compare claims against engine evidence.
8. Fall back to deterministic explanation if validation fails.

Communicate uncertainty honestly.

Examples:

* "This pattern appeared in 6 of 22 comparable positions."
* "The engine prefers this move at the current depth."
* "Several alternatives are close."
* "Analysis timed out, so this diagnosis is provisional."
* "Your explanation suggests a different cause."

Never fabricate certainty.

---

# 16. Measuring Actual Improvement

Do not define product success purely through:

* DAU;
* MAU;
* games played;
* AI messages;
* time spent.

The north-star concept should be something like:

> **Verified correction rate:** the share of sufficiently supported recurring patterns where the player later demonstrates independent recognition and shows reduced recurrence in comparable future games.

Important learning metrics:

* recurrence of diagnosed themes;
* recognition on fresh positions;
* practice success;
* re-test success;
* hint dependence;
* time to recognition;
* confidence calibration;
* transfer into later games.

Important product metrics:

* game import completion;
* analysis completion;
* diagnosis view;
* diagnosis acceptance/correction;
* practice start;
* practice completion;
* re-test completion;
* weekly return with a new game;
* correction queue completion.

Important trust metrics:

* factual error rate;
* unsupported-claim rate;
* illegal-move rate;
* engine disagreement rate;
* wrong-diagnosis rate;
* analysis timeout rate.

Important business metrics:

* paid conversion among retained users;
* month-three retention;
* annual-plan retention;
* churn reasons;
* cost per reviewed game;
* cost per retained user;
* gross margin;
* acquisition source by retained paid user.

---

# 17. Business Model Hypothesis

A likely initial model:

## Free

Enough value to establish trust:

* limited monthly imports;
* limited Correction Cards;
* short practice/re-test;
* basic history;
* export/delete.

## Core paid

Initial hypothesis:

* **$7.99–$11.99/month**
* **$60–$90/year**

Paid value should be longitudinal:

* deeper history;
* recurring patterns;
* correction queue;
* weekly review;
* adaptive practice;
* re-tests;
* cross-platform imports;
* progress evidence.

## Possible later premium

Approximately:

* **$19–$29/month**

Potential value:

* advanced preparation;
* higher analysis limits;
* coach collaboration;
* voice;
* priority compute.

## Possible coach/academy product

Potentially:

* workspace fee;
* per-active-learner pricing.

These are hypotheses, not fixed requirements. Validate willingness to pay before optimizing pricing.

---

# 18. Business Ceiling

Strategic assessment:

* A profitable founder-led/small-team software business is plausible.
* $1M ARR is credible if retention and paid conversion become strong.
* $10M ARR is possible with substantial scale, creator distribution, and potentially coach/academy revenue.
* $50M+ ARR is unlikely as a pure consumer subscription product and would probably require international scale plus B2B2C or adjacent revenue.

Do not use inflated TAM arguments.

Revenue depends on:

* retention;
* conversion;
* ARPU;
* AI/engine COGS;
* acquisition;
* distribution;
* expansion.

---

# 19. Distribution Thesis

Potential channels:

* chess creators;
* YouTube;
* Reddit;
* Discord;
* X;
* TikTok;
* SEO;
* coach referrals;
* creator partnerships;
* shareable Correction Cards;
* public improvement journeys;
* referrals;
* chess communities.

A particularly strong potential loop:

> **Creator demonstrates a real diagnosis → viewer uploads their own game → Zugzwang finds a pattern → viewer shares the result → viewer returns to re-test.**

Distribution should be evaluated by retained users, not vanity traffic.

---

# 20. Competitive Reality

## Chess.com

Strongest overall threat because of:

* audience;
* game history;
* accounts;
* engine infrastructure;
* premium subscriptions;
* bots;
* community;
* AI coaching surface.

## Lichess

Strongest free substitute for:

* raw analysis;
* studies;
* chess tooling;
* community.

## Specialist competitors

Relevant examples include:

* Chessigma;
* ChessLogix;
* Sensei;
* Aimchess;
* ChessMind;
* Noctie;
* others in the expanding AI chess category.

Do not try to out-Chess.com Chess.com.

Zugzwang should remain:

* cross-platform;
* improvement-focused;
* longitudinal;
* evidence-driven;
* specialized in correction and transfer.

---

# 21. Defensibility

The real potential moat is NOT:

* an LLM;
* Stockfish;
* chat;
* voice;
* AI personalities;
* evaluation bars;
* generic imports;
* a larger puzzle library.

Potential moat:

1. Normalized longitudinal game data.
2. Trusted taxonomy of recurring decision patterns.
3. Data about which interventions actually work.
4. Outcome data showing recurrence reduction.
5. Portable player learning records.
6. Coach/creator distribution relationships.
7. Trust around correctness and uncertainty.

The moat does not exist at launch.

It begins to emerge when:

* data is normalized;
* player models become useful;
* intervention outcomes are measured;
* players trust accumulated history;
* the system becomes better at selecting the next correction.

---

# 22. Competitive Response Planning

If Chess.com copies the core:

* stay cross-platform;
* make player history portable;
* specialize in recurring correction and measurable improvement;
* do not compete on breadth.

If Lichess builds an equivalent:

* compete on workflow, prioritization, progress evidence, and coaching experience;
* keep core data portable and trustworthy.

If a major AI company launches an AI chess tutor:

* compete through chess-state reliability;
* longitudinal player models;
* domain-specific pedagogy;
* outcome measurement.

If models become dramatically cheaper:

* use cost reduction to improve margins and increase useful intelligence/evaluation/practice.

If engines become stronger:

* sell understanding and correction, not stronger numbers.

---

# 23. Product Philosophy

For every proposed feature, ask:

### Does this strengthen the correction loop?

If yes, prioritize consideration.

### Does this improve trust/correctness?

Usually high priority.

### Does this make the player model more useful?

Potentially high priority.

### Does this improve retention through genuine improvement?

High priority.

### Is this merely a flashy AI feature?

Probably defer.

### Does this turn Zugzwang into a generic chess platform?

Probably avoid.

### Can an established competitor copy this in six months?

If yes, it is probably a feature, not a moat.

### Can we measure whether it actually helps the player?

If not, be cautious about making it central.

---

# 24. Founder Execution Horizon

## Next 7 days

* Freeze unnecessary feature expansion.
* Baseline the current deployment.
* Audit where game state currently lives.
* Add basic error tracking/event logging.
* Create a legal-PGN test corpus.
* Recruit 10–15 adult improvers.
* Collect 5–20 games each.
* Identify potential creators/coaches.
* Avoid mobile/social/extra personalities/broad integrations.

## Next 30 days

* PGN ingestion.
* Server replay.
* Immutable game versions.
* Managed DB/auth/storage.
* Durable jobs.
* Evidence-grounded Correction Card.
* Practice + re-test.
* Manual diagnosis audits.
* Compare LLM explanations with deterministic baselines.

## Next 90 days

* Recurrence clustering.
* Correction queue.
* Spaced re-tests.
* Model routing.
* Cost budgets.
* Schema validation.
* Retries.
* Privacy/export/delete.
* "Your next correction" home.
* Real checkout.
* 20–50 player cohort.

## 6–12 months

Only expand toward:

* deeper personalization;
* adaptive coaching;
* live weakness-targeted play;
* opening preparation;
* creator/coach products;
* mobile;
* internationalization;

after the core retention/improvement loop is proven.

---

# 25. Validation Gates

Continue expanding only if:

* users bring new games;
* users understand the correction;
* users practice;
* users re-test;
* diagnosed patterns show a credible decline;
* retained users pay;
* inference/engine costs remain controlled.

Consider a pivot if:

* users like explanations but do not return;
* users play but ignore review/practice;
* diagnoses remain generic after 10–20 games;
* re-tests do not predict later-game behavior;
* users will not pay for longitudinal value;
* engagement causes COGS to rise faster than revenue.

Possible pivots:

* analysis/import-first software with a correction layer;
* coach co-pilot;
* adaptive training product;
* creator tool for shareable game diagnosis.

---

# 26. Strategic Ratings

Current strategic assessment:

| Area                       | Rating |
| -------------------------- | -----: |
| Product potential          | 86/100 |
| Market potential           | 76/100 |
| Technical feasibility      | 82/100 |
| Business potential         | 70/100 |
| Defensibility              | 64/100 |
| Overall flagship potential | 78/100 |

Overall verdict:

> **GO WITH WEDGE**

There is a real market, but not an empty category.

Zugzwang succeeds only if it becomes substantially better at **longitudinal, trustworthy correction and measurable improvement** than generic AI chess analysis.

---

# 27. Development Doctrine for Claude Code

When working on Zugzwang:

1. **Preserve the strategic wedge.**
   Do not accidentally turn the product into a generic chess platform.

2. **Prefer end-to-end vertical slices.**
   A complete Play → Diagnose → Practice → Re-test loop is more valuable than ten disconnected features.

3. **Make chess state deterministic.**
   Never delegate legality or canonical state to an LLM.

4. **Make AI evidence-grounded.**
   Important coaching claims should have inspectable chess evidence.

5. **Build data foundations early.**
   Longitudinal player intelligence is the future product, so game/move/evaluation/diagnosis/practice history must be modeled carefully.

6. **Don't over-engineer.**
   Modular monolith + workers is preferable until actual scale requires more.

7. **Instrument before optimizing.**
   If we cannot measure activation, correction completion, re-test behavior, recurrence, retention, cost, and trust, we are flying blind.

8. **Validate before expanding.**
   New surface-area features should wait until the core loop demonstrates repeat usage and learning value.

9. **Treat UX as part of the strategy.**
   The product should guide the player toward their next correction, not overwhelm them with analysis controls.

10. **Protect user trust.**
    If the system is uncertain, say so. If a diagnosis is wrong, let the player correct it.

11. **Keep the product portable.**
    Zugzwang should work with games from Chess.com, Lichess, and PGNs rather than requiring users to abandon existing chess ecosystems.

12. **Do not optimize for impressive demos.**
    Optimize for a player returning with their next game.

---

# 28. Current Strategic Question

The most important question for every significant development decision is:

> **Does this move Zugzwang closer to becoming the most trustworthy system for turning a player's repeated chess decisions into measurable improvement?**

If the answer is no, the feature needs a strong independent justification.

If the answer is yes, explain exactly how it strengthens:

* the correction loop;
* player understanding;
* trust;
* measurable learning;
* retention;
* or the eventual moat.

---

# 29. Canonical Flagship Thesis

### Product thesis

Zugzwang should become a longitudinal chess improvement system centered on recurring decision patterns and targeted correction rather than generic analysis.

### Customer thesis

Start with adult online improvers who play regularly and want help breaking through recurring weaknesses.

### Differentiation thesis

The product remembers what the player repeatedly misunderstands and closes the loop from diagnosis to practice to measurable transfer.

### Technology thesis

Use deterministic chess infrastructure and engine evidence as the foundation, with AI models providing interpretation, personalization, and pedagogy.

### Business thesis

A focused consumer subscription can become a profitable software business; larger scale likely requires creators, coaches, academies, and B2B2C distribution.

### Moat thesis

The moat is longitudinal player data + diagnostic taxonomy + intervention/outcome data + trust, not the underlying LLM.

### Growth thesis

Use creator/coach distribution and shareable, evidence-backed personal diagnoses to turn individual improvement into an acquisition loop.

### Financial thesis

A small profitable company is credible; a very large company is possible but requires exceptional retention, distribution, and expansion beyond a simple consumer subscription.

### Biggest risk

Zugzwang becomes a fluent but generic Stockfish/LLM wrapper whose explanations feel impressive but do not change player behavior.

### Biggest opportunity

Zugzwang becomes the first chess product a player trusts to understand their recurring mistakes and demonstrate, over time, that those mistakes are actually disappearing.

### Founder recommendation

Prioritize the canonical correction loop and validate it with a 20–50 player cohort before materially expanding product surface area.

---

# 30. Immediate Next Strategic Deliverable

The next major planning artifact should be:

> **A 90-day engineering + product execution plan**

It should include:

* database schema;
* event taxonomy;
* API boundaries;
* UI states;
* component boundaries;
* analysis pipeline;
* job architecture;
* acceptance criteria;
* testing strategy;
* observability;
* cost controls;
* 20–50 player cohort experiment;
* success/failure thresholds.

Do this before adding substantial new surface-area features.

---

# Working Rule

When uncertain, optimize for:

> **A player bringing their next game back to Zugzwang and receiving a better, more trustworthy correction than they received last time.**

That is the product.

---

## 19. Board interaction and game state — drag, check, and the endings

The board now takes a move two ways and says three things about itself. Both
halves lean on one file, and that is the point of the section.

### One reading of the position: `chess-frontend/src/boardState.ts`

`readBoardStatus(fen, flags?)` is the only place in the frontend that decides
whether a position is check, checkmate, stalemate or some other draw. Before
it there were three: Play read four booleans off `/api/status`, Learn ran its
own `useMemo` over chess.js, and Review switched on a server-side status
string. Three implementations of one chess rule is three chances for the red
king square, the alert strip and the end-state layer to say different things
about the same board.

**The authority did not move.** The booleans still come from the server when
the caller has them — python-chess generated them for that exact position and
is what will validate the next move, so nothing in the frontend may overrule
it. What is *derived* is only what is not on the wire and cannot be in
dispute: which square the checked king is standing on, and what to call a
draw. The one thing chess.js genuinely cannot see from a bare FEN is
threefold repetition, which is exactly why the server's flags win — it says
the game is over and we fall back to an unqualified "Draw" rather than
guessing.

Callers: Play passes `gameState` as the flags; Review passes its
`status.state`; Learn passes none and gets chess.js's reading of the same
FEN, which is what it already used.

**One caveat on Play, established by the audit in §20 and worth knowing before
you rely on the paragraph above.** `gameState`'s four booleans are only the
server's on the `/api/ai-move` path, which merges `result.game_state` over the
local shape. On `/api/status` and `/api/move` the component does
`chessService.loadPosition(fen)` and takes `getGameState()`, whose booleans
chess.js derives from that same FEN — so Play usually passes chess.js's reading
of the server's position rather than the server's own answer about it. They
agree on everything except the draw counters: chess.js ends a game at the
fifty-move mark, `python-chess`'s `is_game_over()` (no `claim_draw`) waits for
seventy-five. That divergence ends a dead-drawn game slightly early and in the
correct direction, so it was left alone; do not "fix" it without a reason
better than tidiness, and do not write down that Play is reading the server's
flags on every path, because it is not.

### Drag and click are both live, and neither is a mode

Play's board had `arePiecesDraggable={false}`; Learn and Review already
dragged. Play now drags too, **alongside** `onSquareClick`, with no toggle,
no preference and no setting — the absence of one is deliberate.

Both read the same `legalTargets` map, built from `gameState.legal_moves`
(UCI, from python-chess on every server-touching path and from chess.js on
the same FEN otherwise), and both call the same `makePlayerMove`. So there is
no second chess engine in the UI, and an illegal destination is never
*offered* rather than being offered and then refused:

- `isDraggablePiece` — a piece with no legal move cannot be picked up at all.
- `onPieceDragBegin` — lights the destinations while the piece is in flight.
- `onPieceDrop` — returns `false` for anything not in the map, which snaps the
  piece home. That one `false` is the clean revert for every illegal release
  there is: a square the piece cannot reach, a pinned piece's obvious square,
  a king stepping into check, a move that leaves an existing check standing,
  and the origin square itself.

A drag clears any click-selection when it begins, so a cancelled drag ends
with a quiet board however it was cancelled.

`interactive` is the single gate on human input in each mode — game over, AI
thinking, AI-vs-AI, not your turn — and click, drag and draggability all read
it. **There is no second place to remember to lock.**

### What the board says

- **Check** is `--sq-check-mark` on the checked king's square: a radial burn
  sized `closest-side` so it reaches the square's edges and stays aligned with
  it, strongest under the king and gone by the edge. It is drawn *under* the
  move hints, because being told what you may do with a piece is more urgent
  than being told again that you are in check. It appears on all three boards,
  including a Review replay, and it survives resizing because it is a square
  style rather than an overlay.
- **The endings** are `BoardEndState` — a translucent layer, `inset: 0` inside
  the board frame, red for checkmate and graphite for stalemate and every
  other draw, with the one word over it, a line saying who won or why it is a
  draw, and **the mode's own reset** under that ("New game" in Play, "Reset
  board" in Learn, "Back to the game" on a Review branch). It belongs to the
  board frame and not to the page for the reason trap 11 records.

**Review shows the layer on a branch only.** On the mainline it is a replay
of a game that already finished, and stepping to the last move of a mated
game is something you do constantly — tinting that position would cover the
one move you came to look at. The `pm-alert` strip already names the result
there. A branch ending is live and yours, so it gets the layer.

### Three bugs found on the way, all fixed

1. **`/api/ai-move` returns a short `game_state`** — `fen`, `turn`,
   `legal_moves` and the four booleans, and *none* of `move_history`,
   `san_history`, `move_count`, `piece_count` or `castling_rights`.
   `handleMakeAIMove` assigned it wholesale, so after any manual AI move
   `move_count` was `undefined` (the strip under the board read "undefined
   moves" for the rest of the game) and `chessService` was left holding the
   pre-move position. It now loads the FEN, takes the full local shape from
   it, and lets the server's booleans win on top.
2. **A mated king lost its red square in Review.** `status.state` is a single
   value reporting the stronger of the two, so `'checkmate'` has to imply
   `'check'` when the flags are built — otherwise the one position where the
   mark matters most is the one position without it.
3. **A drag promotion opened react-chessboard's own dialog** while a clicked
   promotion auto-queened, on all three boards. See trap 13.

### The `--sq-check` token was defined and never used

It had been in `obsidian.css` since the UI overhaul with no consumer. The flat
wash is still there; `--sq-check-mark` is the radial form the board actually
uses. Do not delete the former without checking, and do not add a third.


---

## 20. The audit pass — what was found, and what was checked and left alone

A full product audit of the running app on `:3001` — repository, backend,
frontend, rendered UI, network, storage, configuration, assets and the
existing suites. **Three findings. Everything else was already right**, and
the second half of this section is the list of things that were checked and
needed nothing, because in a codebase this old the expensive mistake is
re-investigating a settled question.

### 1. A step was offered where there provably was none (Learn, Review)

`Back`/`Previous` guard on having somewhere to go. Neither forward button
fully did.

- **Learn's `Forward`** guarded on `busy || booting` and nothing else, so it
  was live at the root of every fresh session — the first thing anyone sees in
  this mode — and pressing it spent a round trip arriving at the position it
  was already on.
- **Review's `Next`** guarded `state.on_mainline && state.ply >=
  state.total_plies`, which is the end of the *game*. Inside a branch that
  condition is false however far along you are, so it was live at the tip of
  every what-if.

Neither could corrupt anything: `MoveTree.forward()` no-ops with no children,
and `postmortem_api.step_forward` no-ops at the end of the mainline. That is
precisely why it needed finding rather than reporting — a control that
quietly does nothing looks like a control that is broken. Post-Mortem's own
"Back to the game" button already states the rule (*"Only where it means
something. On the game it would be a control that does nothing, which is worse
than one that is not there"*); the two forward buttons were the places that
did not follow it.

Both now read `state.node.children`, and Review keeps its mainline guard
alongside — the two ends are genuinely different because `/forward` is two
different walks (it follows the *game* on the mainline and the *tree* inside a
branch, deliberately; see `postmortem_api.step_forward`). Section 6 of
`tools/verify/interaction.mjs` is the regression net: 12 checks covering both
ends in both modes, including that Forward comes back on when there is a move
ahead again.

### 2. Review's empty canvas made a privacy claim the coach contradicts

"Your game stays on this machine and on the server while you are reviewing
it." The engine work is local, and the review really is dropped when the
server lets go of it — but the coach is Gemini, and asking it a question sends
Google the FEN, the line in SAN, the branch, the PGN's `White`/`Black`
headers and the question itself. Rewritten to say that. See §14.

### 3. §0 said the branch was committed and it was not

The whole interaction and game-state pass was sitting in the working tree.
Fixed in the table, and worth a line here because §0 is the one thing the
next agent believes without checking: **`git status` is the authority, and a
handoff that disagrees with it is the handoff that is wrong.**

### Checked, and needing nothing

Do not re-litigate these without new evidence.

| area | finding |
|---|---|
| Click **and** drag, no toggle | Already correct. Both live on all three boards, both reading one `legalTargets` built from the position's real generator, both calling one mover. 120 interaction invariants, mouse and touch, six viewports. |
| Check / checkmate / stalemate | Already correct — one `boardState.ts` reading, 22 cases, the red king mark and the two board-level layers with the mode's own reset under them. Verified in a browser, not only in a test. |
| Analytics / tracking | **None exists.** No GA, Plausible, PostHog, Mixpanel, Segment, Pixel, Sentry, or custom telemetry, in source or on the wire. None was added — an audit is not a reason to start collecting. |
| Cookies | One: `zw_guest`, HttpOnly, first-party, server-minted, opaque, and strictly necessary (it is *whose board this is*). No third-party cookies. No consent surface is implied by it. |
| `localStorage` | Eleven keys, all display preferences plus two resumable session ids. No identifiers, no analytics. |
| Third-party resources | Google Fonts only (`fonts.googleapis.com` + `fonts.gstatic.com`), plus the Gemini API server-side. Self-hosting the three faces would end the browser-to-Google hop; it is a recommendation, not a defect. |
| Secrets | Nothing key-shaped in any tracked file, `.env` ignored, no `import.meta.env` read in `src/`, nothing key-shaped in the built bundle. `httpx` is already pinned to WARNING so the key cannot reach the log (§7). |
| Debug surface | `/docs`, `/redoc` and `/openapi.json` already follow `is_production()`, overridable by `ENABLE_DOCS`. CORS is an explicit list. |
| Asset licensing | All four piece sets are CC0/public-domain with the source and licence recorded in `pieceThemes.tsx`. Nothing to clear. |
| Images / alt text | **N/A** — the app has no raster images at all. Every graphic is inline SVG, and the decorative ones carry `aria-hidden`. |
| Forms | The account form (labels, `autocomplete`, a busy state, an error line), two chat composers (`aria-label`, disabled while sending, submit disabled on empty) and the PGN input (a real `<button>` drop target). All already correct. |
| Keyboard / focus | Every header stop rings; the account dialog now traps Tab, restores focus and closes on Escape; the mode control is a real `nav` with `aria-current`. Verified live, not read off the source. |
| Console / network | Zero console errors and zero 4xx/5xx across three modes at 1280 and 390. |
| Toggle combinations | All four combinations of *Engine numbers* × *Coordinates* at 1280 and 390: no overflow, board inside its column in every one. |
| Testimonials, reviews, statistics, partnerships, user counts | **N/A — none exist.** There is no marketing surface to be untruthful on. |
| Business identity | **None is displayed, and none was invented.** No entity, address, registration number or support address appears anywhere, and inventing one would have been the harm. Owner input required if the deployed app is ever to have them. |
| Refund / payment terms | **N/A** — there is no payment, checkout or subscription code of any kind. |
| Account terms | **N/A for now** — accounts are built and switched off (§13); this becomes real the day `ACCOUNTS_ENABLED` flips. |
| Privacy policy | **Genuinely relevant and genuinely absent.** The deployed app sets an identity cookie and sends typed questions, positions and imported games to Google. That is a disclosure the product does not make anywhere except, now, on Review's empty canvas. Owner and counsel decision — this file does not draft one, and nobody here should claim the app is compliant. |
| The default difficulty | 20 — *Merciless*. Deliberate or not, it is what a first-time visitor meets in a product whose thesis is correcting weaker players. A product decision, flagged not changed. |
| eslint | 12 errors, 2 warnings — the same pre-existing baseline §16 records, in the same three files. Not touched; one of the two warnings (`PostMortem.tsx` `useMemo`) is a false positive, keyed on `state?.fen` on purpose. |

### One process note, paid for in this pass

**Do not edit a source file while a verify tool is driving the app.** An edit
to `PostMortemDropzone.tsx` landed mid-run; Vite failed the HMR update, the
run recorded a console error and then died on a locator that was waiting for a
page that had not re-rendered. Nothing was wrong with the product. Let the
tool finish, or stop it first.

**And `*/` inside a JSX comment closes it.** The same edit first shipped a
comment containing a route written as a glob; `tsc -b` caught it, a bare
`tsc --noEmit` would not have (§10).

---

## 21. The learning loop — corrections, practice, and what "saved" means

The first persistent version of *game → decision → understand → correct →
practice → re-test → remember*. It lives inside Review, because a correction
is about a decision in a game you brought, and Review is where those are.

**Read §20's rule before extending it: five of the eight themes cannot be
practised, and that is a finding rather than a gap to fill.**

### Where it is, and the one thing to say before describing it

**`Correct` is the fourth tab inside Review's right-hand panel** - beside
Coach, Moves and Report. It is **not** a fourth mode in the header; the header
is still `Play` / `Learn` / `Review`.

Two entry conditions, and both have to be said out loud before the location
is any use:

1. **A game must be loaded.** Review's whole panel - all four tabs - does not
   exist on the empty canvas. No PGN, no tab strip.
2. **The board must be on a move.** At ply 0 the tab reads "Pick a decision"
   and offers nothing, because there is no decision at the starting position.

That second one is a real discoverability risk rather than a note. The
intended route in is a turning point in **Worth a second look** on the Report
tab, which navigates the board and leaves you one click from `Correct` - but
nothing on screen says so, and someone who opens the tab first meets an empty
state twice in a row. Recorded in §15; the fix is probably a "Work on this"
action on each turning point rather than more words in the empty state.

> This cost real confusion the day it shipped: the tab was described to the
> user by its location without mentioning that a PGN has to be loaded first,
> and they went looking for something that could not have been there.

### What already existed, and was reused rather than rebuilt

This is the important half of the section. The brief that produced this work
described building a persistent identity and an evidence layer; both were
already here, and duplicating them would have been the expensive mistake.

- **Persistent anonymous identity: `identity.py`, unchanged.** A visitor
  already gets an opaque `guest:<hex>` in an HttpOnly one-year cookie, and
  everything downstream keys on that string without parsing it. Nothing about
  accounts, sign-in or onboarding was added, and nothing needed to be.
- **The evidence packet: `postmortem_analysis.build_evidence`, unchanged.** It
  already produced ply, SAN, both FENs, both evaluations, the engine's
  preference, its line, the centipawn loss, the grade and **the search depth**,
  per half-move. A correction stores a copy of that packet; it never assembles
  its own.
- **Critical moments: `summarise()['turning_points']`, unchanged.** Already
  deterministic, already the Report tab's "Worth a second look".
- **Trying the better move: `POST /api/postmortem/game/{id}/branch`,
  unchanged.** The loop does not implement a second way to play a move. It
  turns Review's explore mode on and notices what happened.

### What was added

| file | what |
|---|---|
| `learning_loop.py` | the eight-theme taxonomy, `Correction`, `PracticeAttempt`, and `CorrectionStore` |
| `diagnosis_service.py` | the structured diagnosis, its validator, and the deterministic fallback |
| `retest_bank.py` | the certified re-test positions |
| `learning_events.py` | the funnel seam |
| `learning_loop_api.py` | `/api/learning-loop/*` |
| `CorrectionPanel.tsx/.css` | the `Correct` tab: intent → diagnosis → try it → re-test |

### One card per player per theme, and that is the whole recurrence mechanism

`corrections.upsert()` looks for a card with the same theme and increments
`occurrence_count` instead of making a second one. **"You have seen this
before" is true because two diagnoses carried the same controlled token** —
not because anything judged two positions to feel alike. Do not replace this
with similarity scoring and keep the same sentence in the UI; the sentence is
only honest while the mechanism is this dumb.

### The model interprets. It does not assert.

`diagnosis_service.validate()` rejects — never repairs — a reply that:

1. carries a theme outside the taxonomy;
2. names a move that is **not legal in `fen_before`**, checked against a real
   `chess.Board`; or
3. quotes an evaluation the engine did not produce.

A rejected reply falls through to `fallback_diagnosis`, which is built from the
engine's facts alone, is marked `source: "engine"`, carries confidence 0.3, and
says in the card that the coach could not be reached. **A trustworthy failure
beats a convincing lie**, and the fallback existing is why the validator can
afford to be strict.

> The one heuristic in that check is worth knowing about. A bare square in
> chess prose is usually a *reference* ("the knight on g8"), not a move, and
> treating every one as a claimed pawn move rejected perfectly good writing on
> the first run. So a bare square counts as a move only after a play-verb
> ("you should have played c5"). Piece moves, captures and castling are caught
> outright. The narrow gap this leaves — a fabricated pawn move phrased with no
> verb — is deliberate and documented in the file.

### Why five themes have no re-test

Stockfish was asked to adjudicate candidate positions for every theme. The
quiet ones — king safety, piece activity, premature attack, plan-before-reply —
came back with **0 to 20 centipawns between the best and second-best move**.
There is no single right answer in a position like that, so grading a player
pass or fail on one would teach them something false. Those themes return
`available: false` with the reason, and the UI says it.

The eleven that survived were harvested offline, filtered at a 250cp gap and
**re-confirmed at depth 20**; `test_retest_bank.py` re-runs that confirmation
against the real engine on every run. A twelfth was dropped when it
re-measured at 237cp. **Do not lower `MIN_GAP` to keep a position.** The number
is what "there is one right answer here" means.

### What "saved" actually means — say this accurately or not at all

**In memory, and nothing on disk.** §13's guest contract ("everything works,
nothing is saved") is intact: no correction, attempt or event is written
anywhere. The user chose this deliberately when asked.

So a correction survives **a refresh, a second tab, and closing and reopening
the browser** — the identity cookie outlives all three and the process still
holds the card. It does **not** survive a server restart, a Render redeploy, or
the 24-hour idle sweep. The panel says so in as many words, and the fine print
under "Your corrections" is not decoration: a player deciding whether to invest
effort is entitled to know.

`CorrectionStore._backing` is the one seam. Moving to SQLite or Postgres is
that class and nothing above it, the way `GuestLearningService` is
`LearningService` with one method changed. **Do not scatter storage calls past
that file.**

### Instrumentation, and what it refuses to record

`learning_events.py` is a counter and a bounded ring buffer behind an
`emit()`. Nothing leaves the process and no vendor was added — §20 established
this product has no analytics, and a brief mentioning a funnel is not a reason
to start collecting.

It never records **the identity string**: `identity.py` says that value is a
credential, so writing it to a log would put a live session key there. Events
carry a per-process salted hash instead. It also never records **anything the
player typed** — `intent_submitted` notes that they answered and which preset,
and the sentence itself lives on the card where the player can see it.

### Two boards, on purpose

"Try the better move" happens on the **real** board through the existing branch
flow. The re-test is a position from a different game and gets its **own small
board inside the panel**. Painting a stranger's position onto the board that
has been showing your game is exactly the confusion this mode spends a coloured
frame and a "What if:" label avoiding — and a practice position is further from
your game than a branch is. The panel also collapses the correction card to one
line while a re-test is on screen, because with a real diagnosis in it the card
pushed the exercise off the bottom of the panel.
