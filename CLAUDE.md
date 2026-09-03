# Zugzwang — Chess AI Platform (V4.5)

Human-vs-LLM chess coach. **Stockfish proposes, Gemini decides.** Stockfish
ranks legal moves and slices a 3-move window by difficulty; Gemini picks one
from that window and explains it in its own voice. Every half-move is graded
chess.com style, there's a mid-game chat about the position, and a SQLite
cross-game learning layer.

If you change one thing about how this app works, do not break that
sentence: **Gemini choosing from Stockfish's shortlist is the product.** A
build where Stockfish just plays its own top move is a regression even if
every test passes.

**State of play:** the app works. Sandbox Learner Mode is **feature complete**
— backend (§6) and UI (§7). The move tree, the alternatives panel, taking
over the board and the difficulty control all exist and were driven in the
running app, not just typechecked. §7.3 is now a short polish list rather
than a build queue.

---

## 1. Where you are, and the one thing to know about this copy

- **This build is V4.5.** Shipped as `~/Downloads/chess_app_V4.5.zip`, which
  extracts to a `chess_app_V4.5/` root. V4.5 is V3.9 plus a feature-complete
  Sandbox Learner Mode UI (§7) and one backend fix (`/reset`, §6).
- **Working dir:** `C:\Users\David\Documents\chess-app-v3.9` — **still the old
  V3.9 name on disk.** Not a git repo — no git, no history, no undo. Be careful
  with destructive edits; back a file up before overwriting it.

  > ⚠️ **The folder rename is the one step left, and it can't be done from
  > inside a Claude session or while the servers are up** — both hold the
  > directory open and Windows refuses the rename with "used by another
  > process". Every path in this file is therefore still written against
  > `chess-app-v3.9`, which is *true right now*. To finish the rename, close
  > the session, stop both servers, then from PowerShell:
  >
  > ```
  > Rename-Item C:\Users\David\Documents\chess-app-v3.9 chess_app_V4.5
  > ```
  >
  > Then update the path in six places: this bullet, the §2 rebuild command,
  > the §4 test command, the §5 typecheck command, and both runner scripts in
  > §9 (plus the live copies at `/tmp/run_backend.sh` and
  > `/tmp/run_frontend.sh`). A search-and-replace of `chess-app-v3.9` →
  > `chess_app_V4.5` across this file and those two scripts covers all of it.
- **This copy is NOT what is deployed.** The live app is on **Render**, where
  the user replaced Langflow with plain Python for RAM reasons — a change *not*
  in this zip. The user knows and reconciles by hand. So prefer changes that
  don't touch `langflow_service.py` / `langflow_config.py`, and keep any change
  to the AI path small and localised so it ports cleanly.
  **To port by hand for the sandbox UI work: `sandbox_api.py` only** — the
  `/reset` endpoint's `ResetRequest` (§6). `app.py` and the AI path are
  untouched; everything else in §7 is frontend.
- **Other zips exist** in `~/Downloads` (V4.5, the original V3.9, V3.5, v2,
  PROTO1, a backup). V4.5 is the newest **as of this file** — don't assume it
  still is. The V4.5 zip is source only: no `.env` (deliberately — see below),
  no `node_modules`, no `__pycache__`. 1.0 MB unzipped, 76 files; a runnable
  copy needs `npm install` and the venv from §2 on top.
- **`.env` is not in any zip and must not be.** It holds the live
  `GEMINI_API_KEY`, which §8 records as **still unrotated** after leaking into
  the logs. `.env.example` ships instead; `cp .env.example .env` and paste the
  key in. A scan at V4.5 confirmed the real value appears in `.env` and nowhere
  else in the tree — `flows/Chess.json` and `docker-compose.yml` mention only
  the variable *name*.

### File map

| file | role |
|---|---|
| `app.py` | FastAPI app, real-game state, `decide_ai_move()` — 1100+ lines |
| `game_logic.py` | `ChessGame`: board + flat `game_history` for the real game |
| `stockfish_service.py` | shared engine, staged search, ranking/refining |
| `move_quality.py` | chess.com-style grading + the `OPENING_LINES` book |
| `learning_service.py` | SQLite cross-game learning |
| `gemini_move_service.py` | Gemini picks a move from the shortlist |
| `gemini_chat_service.py` | mid-game chat |
| `gemini_narration_service.py` | sandbox coach narration |
| `scenario_service.py` | NL → validated legal position |
| `sandbox_state.py` | sandbox move tree + sessions (pure) |
| `sandbox_api.py` | `/api/sandbox/*` router |
| `chess-frontend/src/components/ChessBoard.tsx` | the real-game UI — 1832 lines |
| `chess-frontend/src/components/Sandbox.tsx` | Sandbox Learner Mode UI (§7) |
| `chess-frontend/src/services/sandboxService.ts` | client for `/api/sandbox/*` |
| `chess-frontend/src/types/sandbox.ts` | wire types mirroring `sandbox_api.py` |

## 2. Run it locally

Everything runs in **WSL Ubuntu** (no Python or Node on the Windows side —
`npx`/`python` from Git Bash will fail with "command not found"). Stockfish is
installed at `/usr/games/stockfish`, the path the code expects.

```bash
wsl -d Ubuntu -- bash -lc "setsid nohup /tmp/run_backend.sh > /tmp/backend.log 2>&1 < /dev/null & disown; sleep 8; pgrep -af 'port 8081'"
```

```bash
wsl -d Ubuntu -- bash -lc "setsid nohup /tmp/run_frontend.sh > /tmp/frontend.log 2>&1 < /dev/null & disown; sleep 12; curl -s -o /dev/null -w '%{http_code}\n' http://localhost:3001/"
```

- App: **http://localhost:3001** — API docs: **http://localhost:8081/docs**
- **Ports are non-default on purpose.** 8080 is held by `com.docker.backend`
  and 3000 was taken. Don't "fix" this by killing Docker.
- Python venv: `/tmp/chessapp`. Logs: `/tmp/backend.log`, `/tmp/frontend.log`.
- `.env` holds the real `GEMINI_API_KEY`. **Never print it, never paste it
  into a command line, never commit it.** The runner sources `.env`.

### Rebuilding after a WSL restart

**`/tmp` is wiped when WSL restarts** — venv, both runner scripts and both
servers vanish at once. Rebuild:

```bash
wsl -d Ubuntu -- bash -lc "cd /mnt/c/Users/David/Documents/chess-app-v3.9 && python3 -m venv /tmp/chessapp && /tmp/chessapp/bin/pip install -q -r requirements.txt && /tmp/chessapp/bin/python -c 'import fastapi, chess, httpx; print(\"deps ok\")'"
```

then recreate the runner scripts (§9). Stockfish is a system package and
survives.

### Traps that will waste your time (every one has actually bitten)

1. **Vite does not see your edits.** Source is on `/mnt/c`, the dev server is
   in WSL, and inotify does not fire for Windows-side writes. Vite silently
   serves the *previous* file and HMR goes quiet. The runner sets `VITE_POLL=1`
   to force polling. Verify with:
   `curl -s http://localhost:3001/src/components/Sandbox.css | grep <selector>`
2. **Never put `pkill -f 'uvicorn app:app'` in a `bash -lc "..."` string.** The
   pattern matches the parent shell's own command line, killing the shell that
   is launching the server. Keep the kill inside the script file (it is), or
   kill by explicit PID.
3. **Background jobs die with the WSL session** unless launched with
   `setsid nohup ... & disown` — **and even then the launching `bash -lc` can
   exit before `setsid` forks**, so the server never starts and the log stays
   empty. Append `; sleep 8` to hold the parent open, then assert with
   `pgrep -af 'port 8081'`. Don't trust "started" — check.
4. **A restart that didn't restart looks like a working server.** If the old
   uvicorn survives, `> /tmp/backend.log` truncates while the old process holds
   an fd at its previous offset, so its next write re-inflates the file with
   NUL bytes. Two symptoms mean exactly this: `grep` reporting "binary file
   matches", and startup lines missing from a growing log. Kill the old PID
   explicitly before starting a new one. (Use `grep -a` on that log.)
5. **Heredocs written from the Windows side get CRLF** and break in bash.
   Write scripts inside WSL, or `sed -i 's/\r$//'` first.
6. **Complex quoting through `wsl -d Ubuntu -- bash -lc "..."` mangles `$`.** A
   `$t` loop variable or `$(...)` is eaten before bash sees it and the command
   silently does the wrong thing rather than erroring. For anything
   non-trivial, write a script file and execute that.
7. **The Bash tool's `/tmp` is Windows-side, not WSL's.** A file written to
   `/tmp` from Git Bash is invisible to `wsl ... bash -lc`. Stage helper
   scripts in the scratchpad dir and copy them in via their `/mnt/c/...` path.
8. **`.action-btn` sets `width: 100%`** (`ChessBoard.css` ~line 1555, plus
   `flex: initial`). Anything borrowing that class inside a flex row will eat
   the row — it collapsed the chat input to 28px once, and the sandbox prompt
   input to 30px later. Override **`width`, not just `flex`**, and use a
   two-class selector: `Sandbox.css` is imported by `Sandbox.tsx`, which
   `App.tsx` imports *above* its own `ChessBoard.css` line, so ChessBoard.css
   lands last and wins every equal-specificity tie.

### Startup lines that tell you the LLM is actually in the loop

Check these after every restart; Gemini's absence once surfaced only as a move
explanation reading "Langflow unavailable".

```
🤖 Move selection: Gemini direct (models: gemini-3.5-flash, ...)
🗣️ Sandbox narration: Gemini (models: gemini-3.1-flash-lite, ...)
🎬 Sandbox scenarios: Gemini (models: gemini-flash-lite-latest, ...)
```

## 3. Tuning knobs (all env vars, no code change)

| var | default | what it does |
|---|---|---|
| `GEMINI_API_KEY` | — | required; without it the app is engine-only |
| `GEMINI_MOVE_MODELS` | reliability chain | comma-separated override, move selection |
| `GEMINI_CHAT_MODELS` | 2nd chain | same, for chat |
| `GEMINI_NARRATION_MODELS` | 3rd chain | same, for sandbox narration |
| `GEMINI_SCENARIO_MODELS` | 4th chain | same, for scenario generation |
| `GEMINI_MOVE_TIMEOUT` | 6 | seconds per model, move selection |
| `GEMINI_CHAT_TIMEOUT` | 15 | seconds per model, chat |
| `GEMINI_NARRATION_TIMEOUT` | 20 | narration is background, can afford longer |
| `GEMINI_SCENARIO_TIMEOUT` | 15 | user is waiting |
| `GEMINI_NARRATION_CONCURRENCY` | 2 | max narration calls in flight, all sessions |
| `STOCKFISH_DEPTH` | 15 | full depth: candidate window, grading, eval bar |
| `STOCKFISH_RANK_DEPTH` | 10 | shallow stage-1 ordering depth |
| `STOCKFISH_THREADS` / `STOCKFISH_HASH_MB` | 1 / 16 | sized for a small container |
| `DISABLE_LANGFLOW` | false | skip the Langflow path entirely |
| `VITE_PROXY_TARGET` / `VITE_POLL` | — | dev server backend + watcher |

**All four model chains lead with a different model on purpose.** They share
one API key, and when moves and chat both led with `gemini-3.5-flash`, chat
immediately got HTTP 429 from move traffic. A test asserts the four leads stay
distinct — if you retune the chains, keep that true.

For Render: set `GEMINI_API_KEY`, `DISABLE_LANGFLOW=true`, and consider
`STOCKFISH_DEPTH=12` / `STOCKFISH_RANK_DEPTH=8` for another ~3× on a throttled
instance. **The Langflow service can be dropped entirely.**

## 4. Tests — 199/199

| file | what | needs |
|---|---|---|
| `test_gemini_move.py` | 9 tests, mocked HTTP | — |
| `test_sandbox_state.py` | 41 tests, move tree + sessions, pure | — |
| `test_scenario.py` | 60 tests, incl. a 480-position legality fuzz | — |
| `test_decide_integration.py` | 6 tests, real Stockfish + faked Gemini | Stockfish |
| `test_sandbox_api.py` | 49 tests, `/api/sandbox/*` end to end | Stockfish |
| `test_sandbox_narration.py` | 34 tests, narration + parallel wiring | Stockfish |

```bash
wsl -d Ubuntu -- bash -lc "cd /mnt/c/Users/David/Documents/chess-app-v3.9 && /tmp/chessapp/bin/python -u test_gemini_move.py && /tmp/chessapp/bin/python -u test_sandbox_state.py && /tmp/chessapp/bin/python -u test_scenario.py && DISABLE_LANGFLOW=true /tmp/chessapp/bin/python -u test_decide_integration.py && DISABLE_LANGFLOW=true /tmp/chessapp/bin/python -u test_sandbox_api.py && DISABLE_LANGFLOW=true /tmp/chessapp/bin/python -u test_sandbox_narration.py"
```

Four things that make a passing suite look broken:

- **Don't chain these with `for t in ...` inside `bash -lc "..."`** — the `$t`
  is mangled (trap 6) and every suite runs as an empty name, printing nothing
  and exiting 1. Spell the six out, as above.
- **`TestClient` must be a context manager** (`with TestClient(app) as
  client:`) in any test exercising a detached background task. Used bare it
  builds a fresh portal — and so a fresh event loop — per request, so a task
  detached by one request dies the instant that request returns. Narration then
  sits at `pending` forever and the design looks broken. It isn't; uvicorn runs
  one long-lived loop.
- **Any test importing `app` and touching Stockfish must end with
  `app.stockfish_service.close()`.** python-chess runs the engine on a
  non-daemon thread, closed by a FastAPI shutdown hook a plain script never
  fires. Without it the interpreter hangs at exit *after* printing "all
  passed" — which looks exactly like a deadlock in the code under test.
- The reused engine keeps its hash between searches, so **analysing the same
  position twice can reorder near-equal moves.** Don't assume determinism
  across calls.

Frontend typecheck (must run in WSL):

```bash
wsl -d Ubuntu -- bash -lc "cd /mnt/c/Users/David/Documents/chess-app-v3.9/chess-frontend && npx tsc -b --force"
```

## 5. Backend work already done (settled — don't redo)

### Performance: 10.08s → 1.97s per half-move (5.1×)

Root cause was **not** the LLM. `get_ranked_moves` analysed *every* legal move
at full depth (~35 depth-15 searches), and three entry points each spawned a
fresh Stockfish process (~530ms apiece). Fixed by a **staged search**: stage 1
orders all legal moves at shallow `RANK_DEPTH` (ordering is all it's for —
those scores are never shown); stage 2 re-analyses **only the selected window**
at full `SEARCH_DEPTH` via `root_moves`. The engine is opened once and reused
under a lock, with restart-on-crash. `move_quality.py` grades against that
shared engine; `app.py` calls `refine_candidates()` after the window is chosen,
plus a shutdown hook.

> A benchmark that disproved the obvious fix is preserved in the module
> docstring: **MultiPV over all legal moves is NOT faster** (0.8–1.3×), because
> asking for N principal variations kills alpha-beta pruning. The win came from
> not searching deeply in stage 1. **Don't "optimise" this back.**

### Gemini reconnected without Langflow

`gemini_move_service.py` calls the Gemini REST API directly, exposing
`choose_move_from_candidates(fen, candidates) -> (move, explanation, success)`
— deliberately the same signature as `ChessLangflowManager`, so
`decide_ai_move()` can use either. Order in `app.py`: **direct Gemini →
Langflow (only if configured and no key) → Stockfish fallback.**

### Model chains — ordered by reliability, measured not assumed

`gemini-2.5-flash` / `gemini-2.5-pro` are listed by the models endpoint but
**404 on this key** ("no longer available to new users"). The user asked for
2.5 specifically; it is not available and no code change fixes that.

Measured: `gemini-3.5-flash-lite` 0.88s but **ReadTimeouts in real play**;
`gemini-3.5-flash` 3.09s, reliable, best explanations; `gemini-3.7-flash` 3.24s,
intermittent timeouts; `gemini-3.1-flash-lite` 6.97s, works; `gemini-3.6-flash`
7.89s, **hangs often** — last in every chain; `gemini-3.8-flash`/`flash-latest`
503; `gemini-pro-latest` 429.

Lessons baked in: order by **reliability, not benchmark speed** (a 2-call probe
is not a sample); a hanging model costs the full timeout on *every* request, so
timeouts are short and all four services **try the last model that worked
first**.

### API key was leaking into the logs (fixed)

`httpx` logs every request at INFO as `HTTP Request: POST <full url>`, and the
Gemini REST URL carries the key as `?key=...` — writing the **real key in
plaintext** into `/tmp/backend.log`, and on Render into the hosted log stream.
`app.py` now sets `logging.getLogger("httpx").setLevel(logging.WARNING)`.
**If you re-enable httpx INFO logging, you re-open this.**

> ⚠️ **The key was exposed in the local log before the fix and read there
> during diagnosis. Rotating it is cheap and still outstanding — the user's
> call.**

### Frontend fixes

`ChessBoard.css`: the chat Send button borrowed `.action-btn`, whose `width:
100%` rule made it claim 366px of a 402px row, collapsing the chat input to
28px. Now content-sized. See **trap 8** — the same rule bit the sandbox later.
`vite.config.ts`: `VITE_PROXY_TARGET` and `VITE_POLL`.

## 6. Sandbox Learner Mode — backend COMPLETE

**What it is:** the AI plays both sides to demonstrate tactical lines, openings
or custom scenarios, with synchronized narration. The user types a
natural-language prompt to generate a position, and can rewind, ask why an
alternative wasn't chosen, or take over the board.

**User's decisions — decisions, not suggestions:**

1. **A separate mode**, not bolted onto normal play. Must not touch the real
   game state or the learning DB.
2. **Taking over the board branches permanently** from that point — unless the
   user rewinds or asks for a new position.
3. **Scenario generation is natural language only — no buttons.**
4. **Lean on Gemini** — the user considers it free. (Quota warning in §8.)
5. **Design for session isolation from the start** — this goes public.
6. **Match the existing UI**, treated as context, not reinvented.

### The API

| endpoint | does |
|---|---|
| `POST /api/sandbox/scenario` | NL prompt → validated position **+ opens a session** |
| `POST /api/sandbox/session` | session from explicit `start_fen`, or standard position |
| `GET \| DELETE /session/{id}` | full state (session + tree + current node) / drop it |
| `POST /session/{id}/ai-move` | AI plays one half-move |
| `POST /session/{id}/move` | user plays one half-move (`{"move","narrate"}`) |
| `POST /session/{id}/{goto,back,forward,reset}` | navigate / restart |
| `GET /session/{id}/alternatives` | Stockfish ranking + what's already explored |
| `GET /session/{id}/narration[/{node_id}]` | poll narration for the line, or one node |

**Two field-name traps, both already cost time:** the session id field is
**`session_id`, not `id`**; and a node inside the tree carries
**`narration_status`** while the narration *poll* endpoint returns **`status`**.
Both spellings are correct in their own place. `types/sandbox.ts` documents them.

**`/reset` takes a `ResetRequest`, and every field of it is optional.** This
is the one backend change made after §6 was first written, and it was made
because the difficulty control in §7 had nothing to call. Two things it now
guarantees, both covered by tests:

- **An omitted field keeps what the session has.** It used to take a
  `CreateSessionRequest`, whose `difficulty` defaults to 20 — so a bare
  `POST /reset {}` silently re-pointed a difficulty-6 "easy king and pawn
  endings" session at full-strength play.
- **An omitted `start_fen` means *this session's own root*, not the standard
  opening.** `MoveTree(None)` builds `chess.Board()`, so restarting a
  generated rook endgame used to dump the student back on move one of a
  normal game and throw the scenario away.

It is also the only way to change a session's strength; `difficulty` is
clamped to 1–20. Reset still drops the tree — that is what restart means —
which is why the UI makes it a separate, labelled click (§7.1).

### Architecture, and the invariants that hold it together

**`sandbox_state.py` is pure** — no FastAPI, Stockfish, Gemini or SQLite, just
`chess.Board`. That is what makes it cheap to test.

- `MoveTree` — nodes with a parent and ordered children. A move either follows
  an existing child (re-walking a known line doesn't grow the tree) or creates
  a new one. **Nothing is ever deleted**, so an abandoned branch is still there
  to rewind into. `goto`/`back`/`forward`, `siblings_of` (the data behind "why
  not X?"), `path_to`, `line_san`.
- Each node stores **its own FEN**, so `board_at()` is an O(1)
  `chess.Board(fen)` rather than a replay, and a node handed to an async job is
  self-describing.
- `SandboxSession` holds **no** `current_game_id`, learning-service handle or
  reference to the module-level `game`. It *cannot* write to the learning DB
  because it holds nothing that could. A test asserts it.
- `SessionStore` — TTL sweep (1h idle) + hard cap (50), per decision 5.

**The sandbox reuses `app.decide_ai_move` rather than forking a second
selection path** — a demo mode that selected moves differently would be
demonstrating something the app doesn't do. `decide_ai_move` gained three
keyword args, all defaulting to today's behaviour so every existing caller is
untouched: `difficulty` (defaults to the global slider), `last_move` (defaults
to `last_ai_move_by_color`; uses an `_UNSET` sentinel because `None` is itself
meaningful), and `use_learning` (sandbox passes **False** — a demonstration
must not read from or bias the player's real history). That is the *only*
change to the AI path, kept small so it ports to Render by hand.

**The dependency is one-way.** `app.py` imports `sandbox_api` to mount the
router, so `sandbox_api` can't import `app.py` back — `app.py` calls
`sandbox_api.configure(decide_ai_move)` instead. Tests inject a fake decider
through the same seam.

#### Narration: a different voice, run in parallel

`gemini_move_service` returns the *player* justifying its own choice ("I'm
staking a claim in the centre"). Narration is the *coach* telling a student
what the move accomplishes and what it rules out. **Don't collapse the two** —
the sandbox shows both. The narrator is handed Stockfish's ranked alternatives
(in SAN, not UCI), which makes it teaching rather than commentary: *"more
ambitious than pushing the queen's pawn"*.

`_start_narration()` creates a detached asyncio task and **never awaits it**;
`/ai-move` returns as soon as the move exists, node marked `pending`. Three
things hold that up, all load-bearing:

- Tasks live in a **strong** reference set. asyncio keeps only weak ones, and a
  task nobody holds can be GC'd mid-flight — presenting as narration that
  silently never arrives. Delete and reset cancel their own tasks.
- Narration **leads with a model that measured slower** (~7s), on purpose: it
  never blocks a user-visible response, so staying out of move selection's way
  beats finishing first.
- **Narration is addressed by node id, never ply index — keep it that way.**
  Node ids are minted once and never reused, so a late job either finds its
  node or finds nothing (`attach_narration()` returns `False`, doesn't raise).
  Real-game move grading *does* use ply indices, which is exactly why it needs
  the `game_epoch` guard — indices get recycled, so a grade landing after a
  reset could be written onto whatever move now sits there. No epoch counter is
  needed here. Don't switch this to indices.

#### Scenario generation: Gemini never emits a FEN

Asked for one it produces two black kings, pawns on the first rank, or the side
not to move in check — and `move_quality.py` documents that Stockfish
**segfaults** on an unreachable position rather than rejecting it, taking down
the engine the real game shares. So: Gemini returns **structured constraints**,
python-chess builds and validates the board. A FEN in the model's reply is
ignored; a test fires a rogue one to prove it can't get through.

Three ways a position gets built, by how much trust they need:

1. **A named opening** → resolved against `OPENING_LINES` in `move_quality.py`
   (~40 openings, verified at import). Legal by construction, so "the Sicilian"
   comes from that book, not the model's memory; Gemini only *names* it.
2. **An explicit SAN line**, for what the book lacks. Each move is parsed
   against the running board, so a hallucination fails on the move that
   introduces it.
3. **Constructed material**, for endgames — pieces placed on an empty board
   under real constraints (one king a side, never adjacent; no pawns on ranks 1
   or 8; no castling rights, since a constructed position has no history to
   justify them), then validated.

`test_scenario.py` fuzzes 480 constructed positions and asserts each is
`is_valid()`, not in check, not already over. **If you change placement, keep
that fuzz test** — it is the only thing between a sloppy constraint and a
segfaulted engine.

Failures are deliberately honest, not approximate: an opening the book lacks
with no fallback → **400 with the reason**; generic words (`gambit`, `defense`,
`attack`, …) are excluded from name matching (without that, any invented name
containing "gambit" resolved to whichever real gambit was listed first);
material that can only ever draw (K vs K, K+N vs K) → 400 saying so; absurd
material (20 queens) is **clamped**, not refused. "Make it winnable" builds up
to 6 positions, evaluates each at the shallow depth and keeps the first
clearing ±150cp — or returns the closest and **says so in `notes`**.

### Measured, live, with real Gemini

- Sandbox AI half-move **1.3–2.3s**, `source=gemini` every time; `alternatives`
  (full rank + depth-15 refine) **~1.3s**; scenario generation **0.9–2.2s**.
- Branching: 4 AI plies, rewind 2, user plays a different move → tree grows 5→6
  nodes and the AI's original line is still intact.
- **Narration costs +0.15s median (+8%) on move selection** (n=24 per arm,
  alternated to cancel drift) — smaller than run-to-run noise; the OFF arm had
  the *higher* p75 and larger maximum. **Move latency spikes of 6–14s are
  Gemini's own long tail and happen with narration off too.** If you see one,
  don't hunt for contention in the sandbox; it isn't there.
- Scenarios: "hard endgame as white" → K+P+R vs K+R, difficulty **18** inferred
  from "hard", verified winning; "black has a queen and a rook and white has 2
  pawns, make it winnable" → exactly that; "the Sicilian Najdorf" → book line;
  "the Zugzwang Gambit" → honest 400; "easy king and pawn endings" → K+2P vs
  K+P, difficulty **6**.

> One result looks like a blunder and isn't. In the first scenario the AI played
> Re8+ Kxe8, apparently dropping a rook — it's a deflection, the b-pawn queens
> next move. Don't "fix" it.

### Behaviour worth knowing before you change it

- Narration fires automatically for **AI** moves. For the student's own moves
  it's opt-in per request (`{"move": "...", "narrate": true}`), off by default
  so exploring doesn't double the load on the shared key.
- Sessions are **in-memory** and die with the process. A Render restart drops
  them. No persistence layer exists.

## 7. Sandbox Learner Mode — the UI

### 7.1 What exists now (feature complete, verified in the running app)

Agreed scope before building: **a full-screen sibling component with a header
toggle** (not a sixth rail tab — `ChessBoard.tsx` is already 1832 lines), the
**full feature set** as the target, with a **checkpoint after the skeleton**.
Both halves are now built.

| file | role |
|---|---|
| `src/types/sandbox.ts` | wire types; documents the `session_id`/`status` traps |
| `src/services/sandboxService.ts` | thin client, all 12 endpoints; surfaces backend `detail` strings verbatim |
| `src/components/Sandbox.tsx` | the mode |
| `src/components/Sandbox.css` | reuses the Obsidian tokens, no second palette |
| `src/App.tsx` / `App.css` | mode toggle in the header |

Working and verified live: session open on mount, NL scenario generation, AI
half-moves, auto-play with pause, back/forward, and narration polling that
renders **both voices separately** — the player's `explanation` in dim text,
the coach's `narration` in ice with a left rule.

**The right column is three panels behind one tab row**, not three columns —
the board plus a 380px panel already fills a laptop, and the three are read one
at a time:

- **Coach** — the running commentary, oldest first. Unchanged.
- **Line** — the whole move tree, PGN-style: the main line runs inline and
  every other continuation from a position is an indented variation under the
  move it replaces. "Main line" is the *first* child, so an AI demonstration
  stays the spine and the student's detours hang off it. Every chip is a
  `goto`; the current node is pressed in; an ice dot means narration landed.
  Move numbers come off the **parent's** FEN — a node's own FEN is the position
  *after* its move, so numbering from it prints every black move one too high.
- **Why not?** — `GET /alternatives`, fetched lazily and only for the node
  being looked at (a full rank plus a depth-15 refine is ~1.0–1.3s and shares
  the engine lock with move selection, so firing it every half-move would slow
  the demonstration to buy nothing). UCI is converted to SAN with chess.js.
  Scores stay in the **side-to-move's** frame, which is what `get_ranked_moves`
  returns — don't flip them to White's to match the real game's eval bar, they
  answer different questions. Unplayed moves get "Try it" (which plays them,
  branching); already-explored ones get "Go to" plus the AI's own words.

**Taking over the board is an explicit toggle.** The board is inert while you
watch — a stray click that silently branched the line would be a confusing way
to find out the tree branches at all. Click-to-move and drag both work, legality
comes from `state.legal_moves` (the server's own list, so there is no second
opinion to disagree), promotions auto-queen as in the real game, and square
hints reuse **ChessBoard.tsx's own amber/green/red** rather than the ice accent.
A checkbox wires `narrate: true` for the student's own moves; off by default,
same reason as the backend's.

**Difficulty is a draft plus a labelled click.** The dropdown stages a value and
the button becomes "Restart at 18"; nothing is lost until it is pressed, because
`/reset` drops the tree (§6). Pressed with no change, it reads "Restart line".

**The game pane is hidden, not unmounted, when the sandbox is open.** The real
game's *client* state (chat transcript, review data, active rail section) is
local to `ChessBoard.tsx`; unmounting would throw it all away every time
someone glanced at the sandbox.

Design language ("Obsidian", defined at `:root` in `ChessBoard.css` — reuse it,
don't re-declare): near-black layered surfaces (`--cp-bg: #0a0b0d`), depth from
hairline border + cast shadow + inset top highlight (`--cp-emboss-out`/`-in`),
radii 10/12/16/pill, fonts Sora / Manrope / JetBrains Mono. **One accent: ice
`--cp-primary: #7fd4ff`, explicitly "the AI's voice"** — narration gets it and
nothing else does. Amber `--cp-warn` = thinking; red `--cp-danger` =
destructive only.

### 7.2 Bugs found by driving the live app — fixed, don't reintroduce

1. **Board flipped on every half-move.** Orientation was derived from the
   *current* turn, which makes a demonstration impossible to follow. It is now
   fixed when a position is opened, from the starting side to move (the side
   being taught). Don't wire it back to `state.turn`.
2. **`.action-btn { width: 100% }` collapsed the prompt input to 30px** — the
   same rule that once collapsed the chat input to 28px. Full explanation in
   **trap 8**; the two-class selectors in `Sandbox.css` are load-bearing, don't
   flatten them.
3. **StrictMode leaked a sandbox session per mount.** React's dev
   mount/unmount/remount created two sessions and kept both, against the store's
   50-session cap. The create effect now deletes the session that arrives after
   cancellation. Verified: 2 created, 1 deleted, 1 live.
4. **Scenario description and notes vanished on the first move.** Only
   `POST /scenario` returns a `scenario` block; every other endpoint returns the
   same body without it, so reading it off `state` silently discarded the honest
   notes ("verified winning for white", "closest I could get") as soon as
   anything else was called. It is now held in its own state.

Found in the second pass, building the rest of §7:

5. **`/reset` accepted `difficulty` and ignored it.** The difficulty control
   had nothing to call. Fixed in `sandbox_api.py` — full detail in §6, because
   the fix also changed what an *omitted* field means.
6. **Pause left four dead buttons for up to ~14s.** Two separate causes, both
   fixed: nothing said the in-flight half-move was still landing (now a
   "Finishing this move..." strip, amber, always in the DOM so the column
   doesn't jump), and the auto-play loop slept its full 650ms inter-move gap
   *after* the paused move landed, because it only re-read `autoPlayRef` at the
   top. It now re-reads it before sleeping.
7. **The status label flickered between two strings every ply.** Derived from
   `thinking` alone, it dropped to "Working..." during each 650ms gap. It reads
   `thinking || autoPlay` now, which is stable across a whole auto-played line.

Measured after the fixes: prompt input 912px, buttons content-sized; scenario
"a hard endgame as white" → difficulty 18, notes "verified winning for white";
3-ply auto-play with 3 narrations ready, 0 failed; back/forward preserve
narration. **Isolation confirmed live:** the real game was still at the
starting position after ~10 sandbox moves, and `gemini_moves: 0` in
`/api/learning/summary` proves the sandbox's Gemini moves were never recorded.

Second pass, driven live end to end: `g3 c6` demonstrated, rewound one ply,
`e5` played from the "Why not?" panel → the tree grew a variation and the AI's
`c6` line stayed clickable; `d4` played by click-to-move → a second branch;
difficulty 12 → 18 restarted the line and the server confirmed 18. Status
transitions captured at 100ms: `AI is thinking...` → Pause → `Finishing this
move...` → idle. Scenario "a hard rook endgame as white" → 5-piece position,
difficulty 18, "verified winning for white", and **Restart line came back to
that 5-piece position** rather than the standard opening. Isolation re-checked
after all of it: real game `move_count: 0`, `gemini_moves: 0`.

> One measurement trap, in case it bites again: snapshotting board occupancy
> straight after a state change reads the **previous** position, because
> `animationDuration={300}` means the piece is still in flight. It looks
> exactly like a move that didn't apply. Wait out the animation before
> asserting on the DOM.

### 7.3 What is left

Nothing structural — everything §7.3 previously listed is built. What remains
is genuinely optional:

1. **Underpromotion.** Promotions auto-queen, matching `ChessBoard.tsx`.
   Fine for the real game; a *learner* mode is the one place underpromotion
   might be worth a picker.
2. **The AI doesn't reply automatically after you take over.** You click "AI
   move" yourself. Deliberate — nothing should start thinking without being
   asked — but "play me from here" is a reasonable thing to want.
3. **No keyboard navigation** of the tree (arrow keys for back/forward).
4. **Sessions still die with the process** (§6). Unchanged, still by design.

## 8. Known open issues

- **API key rotation still outstanding** (§5). The leak is fixed; the
  previously-exposed key has not been rotated.
- **Console error spam:** `<svg> attribute width/height: A negative value is
  not valid ("-12.5" / "-100")` on every render, from the real-game UI.
  Pre-existing and still present; not investigated. The user knows.
- **`--cp-t-base` is self-referential** — `ChessBoard.css` defines it as
  `var(--cp-t-base)`, so it resolves to nothing and silently kills every
  transition using it, including `.action-btn`. One-line fix, not yet made.
  `Sandbox.css` avoids the token and uses `--cp-t-fast`/`--cp-t-slow`.
- **API quota contention.** All four Gemini paths share one key, and chat has
  already been 429'd by move traffic. Mitigations: four distinct chain leads and
  `GEMINI_NARRATION_CONCURRENCY`. A sandbox ply can have two calls in flight at
  once (move + narration) by design.
- **Single-user backend.** Module-level globals mean two browser tabs share one
  board in the *real game*. (Sandbox sessions are properly isolated — that gap
  is now specific to normal play.)
- **`LANGFLOW_AUTO_LOGIN=true`** in `docker-compose.yml` grants unauthenticated
  superuser access to Langflow. Must-fix before any public deploy (relevant
  given decision 5).

## 9. Runner script contents (recreate if `/tmp` is wiped)

Write these **inside WSL** (trap 5) — they contain no `$`, so a quoted heredoc
is safe. `chmod +x` both.

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

## 10. How the user works

- Wants **evidence, not claims** — measure and show the numbers. Several
  hypotheses have been disproved by benchmarking across these sessions
  (MultiPV, narration contention); every time, the measurement was the useful
  output.
- Reacts badly to unexplained behaviour changes. If something degrades —
  especially the LLM dropping out of the loop — **say so loudly and up front**
  rather than burying it.
- Asks to be consulted on scope before big builds ("pause and ask me").
- Verify in the **running app**, not just tests. Every bug in §7.2 was found by
  driving the live UI; all four typechecked cleanly and none would have been
  caught by the Python suite.
