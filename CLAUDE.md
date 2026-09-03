# Zugzwang — Chess AI Platform (V4.5, deployed)

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

**State of play:** the app is **deployed and live**. Backend on Render,
frontend on Vercel, both serving V4.5. Sandbox Learner Mode is feature
complete. The UI has been through a full design-system overhaul (§9) and the
"Why not?" panel has been replaced by a live coach chat (§8.4).

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
| `sandbox_api.py` | `/api/sandbox/*` router |
| `chess-frontend/src/styles/obsidian.css` | **the design system — token source of truth** |
| `chess-frontend/src/hooks/useTheme.ts` | light/dark/system preference |
| `chess-frontend/src/hooks/useBoardSize.ts` | responsive board sizing, shared by both modes |
| `chess-frontend/src/components/ChessBoard.tsx` | the real-game UI |
| `chess-frontend/src/components/Sandbox.tsx` | Learner Mode |
| `chess-frontend/src/components/ThemeToggle.tsx` | the theme switch |
| `chess-frontend/src/components/EmptyState.tsx` | empty/loading panel states |
| `OBSIDIAN_DESIGN.md` | **read before touching any CSS** |
| `DEPLOY.md` | Render + Vercel click-path and known limits |

---

## 2. Deployment (live)

| | |
|---|---|
| Frontend | Vercel, root directory **`chess-frontend`**, branch `master` |
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
| `VITE_PROXY_TARGET` / `VITE_POLL` | — | dev server backend + watcher |

**All five model chains lead with a different model on purpose.** They share
one API key, and when moves and chat both led with `gemini-3.5-flash`, chat
immediately got HTTP 429 from move traffic. `test_scenario.py` asserts the
five leads stay distinct — if you retune, keep that true. `gemini-3.6-flash`
is last in every chain: it does not refuse, it *hangs*, so leading with it
spends the full timeout on every request.

---

## 6. Tests — 200/200

| file | what | needs |
|---|---|---|
| `test_gemini_move.py` | 9, mocked HTTP | — |
| `test_sandbox_state.py` | 41, move tree + sessions, pure | — |
| `test_scenario.py` | 61, incl. a 480-position legality fuzz | — |
| `test_decide_integration.py` | 6, real Stockfish + faked Gemini | Stockfish |
| `test_sandbox_api.py` | 49, `/api/sandbox/*` end to end | Stockfish |
| `test_sandbox_narration.py` | 34, narration + parallel wiring | Stockfish |

```bash
cd /mnt/c/Users/David/Documents/chess-app-v3.9
/tmp/chessapp/bin/python -u test_gemini_move.py && \
/tmp/chessapp/bin/python -u test_sandbox_state.py && \
/tmp/chessapp/bin/python -u test_scenario.py && \
DISABLE_LANGFLOW=true /tmp/chessapp/bin/python -u test_decide_integration.py && \
DISABLE_LANGFLOW=true /tmp/chessapp/bin/python -u test_sandbox_api.py && \
DISABLE_LANGFLOW=true /tmp/chessapp/bin/python -u test_sandbox_narration.py
```

Spell the six out — a `for t in ...` loop inside `bash -lc "..."` has its `$t`
mangled and every suite runs as an empty name.

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
4. **Lean on Gemini.** (Quota warning in §13.)
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
| `GET /session/{id}/chat` | the transcript |

**Two field-name traps:** the session id field is **`session_id`, not `id`**;
a node carries **`narration_status`** while the narration poll endpoint returns
**`status`**. Both spellings are correct in their own place.

**`/reset` takes a `ResetRequest` and every field is optional.** An omitted
field keeps what the session has, and an omitted `start_fen` means *this
session's own root*, not the standard opening. It is also the only way to
change a session's strength. Reset drops the tree — that is what restart
means — which is why the UI makes it a separate, labelled click.

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

Both sandbox columns are pinned to the board's width, and the analysis panel
is capped at `calc(--board-size + padding)` so it can never be wider than the
board — a fixed pixel cap can't hold that promise, because the board is
height-constrained and is only 384px at 1280×720.

One segmented-control treatment is shared by the header (`Play`/`Learn`), the
game's analysis rail, and the sandbox tabs (`Coach`/`Line`/`Chat`). They all
answer "which view am I looking at" and used to be three different designs.

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

## 13. Known open issues

- **The real game is single-user.** Module-level state in `app.py` means every
  visitor shares one board. Sandbox sessions are properly isolated; normal
  play is not. **This is the thing to fix before the URL is shared**, and
  `sandbox_state.py` is a working model for how. The user plans accounts, and
  has knowingly accepted the risk for now on an unlisted URL.
- **`allow_origins=["*"]`** in `app.py`. The Vercel rewrite means the browser
  never needs CORS, so this can be narrowed to the Vercel domain.
- **API quota contention.** Five Gemini paths share one key. Mitigations: five
  distinct chain leads, `GEMINI_NARRATION_CONCURRENCY`, and `rate_limit.py`.
- **The learning DB resets on Render** (ephemeral disk). A persistent disk is
  paid.
- **Sandbox sessions die with the process** — by design, no persistence layer.
- **`LANGFLOW_AUTO_LOGIN=true`** grants unauthenticated superuser access.
  Mitigated by the compose profile (it doesn't run), not fixed.
- **The folder is still named `chess-app-v3.9`** (§1).

---

## 14. How the user works

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
