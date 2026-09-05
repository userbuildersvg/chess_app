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
| **Branch to work on** | `ui-overhaul` — branched off `postmortem` |
| **What is on it** | the full UI/UX overhaul: one shared layout shell for all three modes (§11) |
| **Deployed branch** | `master` — what Render and Vercel serve, **unchanged** |
| **Tests** | **496 across 10 suites, all passing** (§6) + **58/58 UI invariants** (§10) |
| **Driven live** | yes, on :3001 — import, navigate, branch, engine reply, scan, coach, both themes |
| **Docker build (:3000)** | rebuilt from `postmortem` on 2026-09-05 — image `zugzwang:v4.5` now **carries Post-Mortem**. Rollback point: `zugzwang:v4.5-pre-postmortem`. No git move was made; `master` is untouched. |

> ⚠️ **Do not push, merge to master, or deploy without asking.** Master is what
> Render and Vercel serve. The user's plan is "one final big push to Render and
> Vercel" *after* accounts land. Local commits on a branch are expected;
> anything leaving this machine is not.

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
11. **A browser tab open across a long session goes stale.** HMR sockets drop,
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

## 6. Tests — 495/495

| file | what | needs |
|---|---|---|
| `test_gemini_move.py` | 9, mocked HTTP | — |
| `test_sandbox_state.py` | 41, move tree + sessions, pure | — |
| `test_player_state.py` | **41, per-player isolation, pure** | — |
| `test_scenario.py` | 61, incl. a 480-position legality fuzz | — |
| `test_decide_integration.py` | 6, real Stockfish + faked Gemini | Stockfish |
| `test_sandbox_api.py` | **87**, `/api/sandbox/*` end to end | Stockfish |
| `test_sandbox_narration.py` | 34, narration + parallel wiring | Stockfish |
| `test_accounts.py` | **84, guest mode + accounts-off + auth internals** | Stockfish |
| `test_postmortem_state.py` | **60, PGN ingestion + the immutable game, pure** | — |
| `test_postmortem_api.py` | **72, `/api/postmortem/*` end to end** | Stockfish |

```bash
cd /mnt/c/Users/David/Documents/chess-app-v3.9
/tmp/chessapp/bin/python -u test_gemini_move.py && \
/tmp/chessapp/bin/python -u test_sandbox_state.py && \
/tmp/chessapp/bin/python -u test_player_state.py && \
/tmp/chessapp/bin/python -u test_scenario.py && \
DISABLE_LANGFLOW=true /tmp/chessapp/bin/python -u test_decide_integration.py && \
DISABLE_LANGFLOW=true /tmp/chessapp/bin/python -u test_sandbox_api.py && \
DISABLE_LANGFLOW=true /tmp/chessapp/bin/python -u test_sandbox_narration.py && \
DISABLE_LANGFLOW=true /tmp/chessapp/bin/python -u test_accounts.py && \
/tmp/chessapp/bin/python -u test_postmortem_state.py && \
DISABLE_LANGFLOW=true /tmp/chessapp/bin/python -u test_postmortem_api.py
```

Spell the ten out — a `for t in ...` loop inside `bash -lc "..."` has its
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

### Run the invariants first

```bash
node tools/verify/ui.mjs                        # dev server on :3001
node tools/verify/ui.mjs http://localhost:3000  # or the container
node tools/verify/ui.mjs http://localhost:3001 --shots out/
```

**58 checks, all currently passing**: console errors and overflow in both modes
and both themes; AA contrast on every text style; 44px touch targets under a
coarse pointer; and the Learner Mode layout invariants — board and tab row on
one line, the eval toggle moving nothing, the layout centred. Each of those
was a real bug on this branch, so the file is a regression net rather than a
checklist. Run it before claiming any UI work is done.

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
- **The folder is still named `chess-app-v3.9`** (§1).

---

## 16. How the user works

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

## 17. Product & Business Doctrine (persistent)

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
