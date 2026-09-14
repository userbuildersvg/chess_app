# Zugzwang — Chess AI Platform (V4.5, deployed)

Human-vs-LLM chess coach. **Stockfish proposes, Gemini decides.** Stockfish
ranks legal moves and a chosen **opponent profile** (Beginner ~400 … Master-like
~2400, §37) samples the three a player of that level might consider; Gemini
picks one from that pool, told who it is playing as, and explains it in that
player's voice. Every half-move is graded
chess.com style, there is a mid-game chat about the position, a Learner Mode
sandbox with its own coach chat, a Post-Mortem review for games you bring
yourself, and a Postgres-backed cross-game learning layer.

**Three modes, and they are peers.** `Play` is the real game, `Learn` is the
sandbox, `Review` is Post-Mortem (§14). One header control switches between
them; all three are mounted at once and hidden rather than unmounted, because
each holds state the user would be furious to lose by glancing at another.

If you change one thing about how this app works, do not break that
sentence: **Gemini choosing from Stockfish's shortlist is the product.** A
build where Stockfish just plays its own top move is a regression even if
every test passes. It has a visible signature — `source: "stockfish_fallback"`
and the string *"Stockfish-calculated move (no Gemini API key configured)"*.

> 🔒 **THE APP IS PRIVATE.** Zugzwang is behind a closed-beta gate: every
> guarded `/api` route answers **403**
> to a caller who has not redeemed an invitation, and the browser draws a
> landing page instead of the board. It is a server-side authorization system
> (§26), not a frontend gate, and it is **fail-closed** — with
> `BETA_ACCESS_REQUIRED` unset the door is shut. If you are wondering why the
> board will not load on `:3001`, that is why: redeem a code, or export
> `BETA_ACCESS_REQUIRED=false` in the backend's environment while you work on
> something else. **Every other API-driving test suite sets that to `false` at
> the top**, so a new suite that drives the app must do the same or every
> request in it is answered 403.

**State of play:** commit **`b4d96e2`** was pushed to `origin/master` on
2026-09-11, triggering the configured Render and Vercel deployments. The
backend is <https://zugzwang-api.onrender.com> and the frontend is
<https://chess-app-rho-swart.vercel.app>, but that rollout has **not yet been
re-probed**, so do not claim the two live services are on `b4d96e2` until their
build/version evidence says so. The local checkout remains
`barry-validation-readiness`; see §0 and §30.

---

## THE THREE BUILDS — know which one you are looking at

**Read this before you touch anything.** There are three builds of this app,
they are not interchangeable, and confusing them has cost whole sessions. The
user has asked, twice, that this be the first thing every agent knows.

| build | where | what it is |
|---|---|---|
| **DEV** | **`localhost:3001`** (Vite) + uvicorn on **`:8081`** | Live host files, HMR. **All development and verification happens here.** |
| **STABLE** | **`localhost:3000`** (nginx in Docker) + backend on **`:8080`** | The shipped build, **source baked into the image**. Host edits do NOT appear here. It exists to verify what will ship. |
| **DEPLOYED** | **whatever is pushed to `origin/master`** | Backend on Render (`zugzwang-api.onrender.com`), frontend on Vercel (`chess-app-rho-swart.vercel.app`). This is what real users see. |

In the user's words: *"the dev build is port 3001, stable build is docker port
3000 and deployed build is the one pushed to origin master on render and
vercel."*

Three consequences, each of which has bitten:

1. **Work and verify on `:3001`.** Point `tools/verify/*.mjs` there by default.
   If a change does not appear, the cause is the Vite-on-`/mnt/c` watching trap
   or a stale tab (§4 traps 1 and 16) — **not** a reason to switch to `:3000`.
2. **`:3000` cannot show you an edit.** Its source is inside the image. Rebuild
   the image or you are reading last week's code and concluding your change did
   nothing.
3. **Pushing to `origin/master` IS the deploy.** Render and Vercel both build
   from it automatically, with no further step and no confirmation. Set any
   environment variables a release needs **before** the push, not after — the
   deploy starts the moment the push lands. And see the warning in §0: never
   push, merge to master, or deploy without asking.

> ⚠️ **The frontend and the backend deploy separately and skew silently** (§2).
> Both halves carry a build id for exactly this reason — `version` in
> `/api/health`, and the footer on the account pages — so *"did my deploy
> land?"* is a comparison anybody can make rather than a guess.

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
| **Current checkout** | **`barry-validation-readiness`**, at **`d89e91e`**, which is also `origin/master` (pushed 2026-09-13 as a fast-forward `818a6b0..d89e91e`; the tree was clean after the push). Local `master` is stale at `4635108`; start unrelated work from `origin/master`, or fast-forward local `master` first. Any further edits are local until the user explicitly approves another push. |
| **Historical branches** | `beta-hardening-1` and the other named sprint branches are ancestors of `origin/master`, not parallel uncommitted work queues. Preserve them as history, but do not use their branch tips to infer current product or deployment state. |
| **What is pushed** | **`d89e91e`** on `origin/master`: Advanced Coach Settings (§39), the Play move speaker stance (§40), the widened style range and the test-assertion refresh, on top of `818a6b0` (opponent profiles §37 + latency pass §36, pushed earlier the same day). The remote ref was read back at the same full SHA. Auto-deploys should have started, but neither live service has been re-probed after this push. Confirm `version` in `/api/health` and the footer build id both read `d89e91e` before calling the rollout live. |
| **Production configuration to verify** | Confirm `VITE_CONTACT_EMAIL`, `BETA_CODE_PEPPER`, `TRUSTED_PROXY_HOPS=1`, and the intended `EMAIL_ENABLED` value in their deployed environments. Mailjet was previously blocked (`mj-0001`), so password-reset availability must be verified rather than inferred from old notes. Never print any value while checking it. |
| **Security baseline** | The §28 OWASP hardening is already ancestral to `origin/master`: security headers, CSRF origin checks, a trusted-proxy boundary for rate limits, request-body ceilings, and fail-closed route documentation. `BETA_CODE_PEPPER` and `TRUSTED_PROXY_HOPS` remain deployment-critical. |
| **Shipped (in `818a6b0`)** | **Opponent profiles (§37)**, on `barry-validation-readiness`, on top of the latency pass below: the 1–20 difficulty integer is gone end to end. Seven Elo-style profiles (`opponent_profiles.py`, mirrored in `chess-frontend/src/opponentProfiles.ts`), a profile-weighted candidate pool (`candidate_selection.py`) in place of the slid 3-move window, a profile block in the one Gemini call, a decision record on every AI move in the events, `POST /api/difficulty {profile}`, migration `010_opponent_profile.sql` (`moves.opponent_profile`; the old integer column stays, unwritten), the selector reads "Club — about 1500" and is labelled **Opponent level** in Play and Learn. Verified on `:3001`: all 23 backend suites green (storage suites on a disposable schema, dropped), `tools/profile_sanity.py` mean cpl 363→80→46→21→10→6→2 beginner→master, browser suites guided 52, ui 118, chat 57, review-handoff 87, layout-stress 612, overlap 300, interaction 127, boardstate 22; one live beginner move through the real model in character. Committed and pushed. |
| **Shipped (in `818a6b0`)** | **The latency pass (§36)**, on `barry-validation-readiness`: the AI's reply is scheduled before the decoration and its engine stages jump the queue; `/api/move` answers in ~4ms instead of ~300; every product-event and grade-audit write left the request path (Review's scan of a 33-ply game 7.3s → 1.5s); the diagnosis chain leads with 3.5-flash and hedges; 150ms piece animation; the status poll no longer re-renders on unchanged data. Not one field was removed from any prompt or payload. Verified on `:3001`: `test_latency_paths` 21/21 plus the suites listed in §36; browser suites ui 118, interaction 127, boardstate 22, guided 52, chat 57, layout-stress 612, overlap 300, review-handoff 87. Awaiting the user's review before commit. |
| **Latest push** | **Advanced Coach Settings (§39), the Play move speaker stance (§40) and the widened style range**, pushed 2026-09-13 at the user's request. No new env vars, no migration. Verified on `:3001` before the push: `test_coach_style` 72, `test_move_speaker_stance` 39, `test_guided_play_api` 44, `test_decide_integration` 20, `test_gemini_move` 11, `test_guided_play` 42, `test_opponent_profiles` 105, `test_player_state` 47; `coach-style.mjs` 27, `ui.mjs` 118; build and lint clean. Rollout not yet re-probed. |
| **Previous push** | **Verification sprint (§33), readability hardening (§34) and one board size (§35)**, pushed 2026-09-12; rollout to be re-probed. |
| **Previous push** | **"Review this game"** (§32): a second button on Play's end-of-game layer that turns the finished game into a Post-Mortem review in one click - the server writes the PGN from its own board, the existing import/replay/scan path does the rest, and Review lands already analysing with the seats named You / Gemini and the board from the player's side. Verified on `:3001`: `review-handoff.mjs` 87/87 (a real 9-ply mate at strength 1, then the handoff), plus the suites below. Committed and pushed to `origin/master` on 2026-09-12 at the user's request; the rollout has not yet been re-probed. |
| **Same push** | **Guided Play** (§31): a Play-mode switch that makes the coach add a "Watch out" section to its move explanation - what to inspect before replying, never what to play. One Gemini call per move, the section grounded on python-chess facts, the preference local + account-synced, four events. The difficulty control is labelled **AI strength**. Verified on `:3001`: `guided.mjs` 52/52 at 1366 and 1280, `ui` 118, `overlap` 300, `interaction` 127, `chat` 57; backend suites touching it all green (incl. `test_accounts_postgres` 230 on a disposable schema, dropped). Also `tools/verify/layout-stress.mjs` (Codex's board-stability probe, 226 checks) passes. No new env vars or migrations in this release. |
| **Completed sprint** | **Barry validation readiness** (§30), committed as `b4d96e2` and pushed to `origin/master`: honest whole-game coverage versus decision-score denominators, first-party correction-funnel and stage-timing events, move-grade audit provenance/export, immediate loading language, and a lightweight Shipaton/RevenueCat readiness note. It deliberately did not absorb the separate direct-playtester UI sprint. |
| **Direct-playtester sprint** | §27 is also ancestral to `origin/master`: the move-feedback trust audit, promotion picker, Review seats/rotation, shared board sizing, AI-level clipping fix, Learn setup path, and measured AI-move latency work. It is separate in scope from Barry validation readiness, but it is not an unmerged branch anymore. |
| **Closed-beta baseline** | Migration 008, keyed invitations, fail-closed server middleware, guest-to-account access transfer, CLI-only administration, and the landing/contact/privacy/terms surfaces are in `origin/master`. Signup is gated until redemption; returning password and Google sign-ins remain reachable, while an unknown Google subject cannot create an account without an invited guest. See §26. |
| **Release gate - five blockers, all cleared** | An independent audit found five, four of them code. **(1)** `Dockerfile.backend` never copied `migrations/`, so the production image booted onto a database with no application tables and logged *"Schema up to date"* while doing it - the root `Dockerfile` had the same hole. Both now copy it, and `db.assert_migrations_present()` refuses to start a build without it. **(2)** A guest who signed up and then logged out was handed their **claimed** board back, still writable into account-owned history - the identity lifecycle now retires a guest at sign-in and issues a fresh one at sign-out (§13). **(3)** `GET /api/reset` destroyed a game in progress; it is a POST now. **(4)** `render.yaml` and DEPLOY.md contradicted the runtime about accounts; both now say what is true. **(5)** a Neon credential to rotate, which is the user's to do. The whole account is §22. |
| **Browser-verified (§28)** | Yes. Headers confirmed live on `:3001` on both halves; 118/118 UI, 127/127 interaction and 22/22 board-state invariants pass against the dev stack; and the SHIPPING CSP - the one with no `unsafe-inline` - was exercised in a real browser against a production build on `vite preview`: **zero CSP violations** across six routes, Google Fonts loaded, framing refused. The CSRF middleware also caught a genuine cross-origin write on first contact (Vite's preview port was not in `ALLOWED_ORIGINS`), which is the middleware being right rather than a bug. |
| **Browser-verified** | Yes, on `:3001` against the final commit. Guest plays → rows land under `guest:…` → signup claims them (`claimed_games: 1`, ownership rewritten, ledger row written, moves followed) → profile carries the email → settings saved → **a second browser signs in and gets the same board** → reset link redeemed once, reuse refused, every session killed, old password dead → a second account sees none of it. Also driven with `EMAIL_ENABLED=false`: identical answers for known and unknown addresses, 14ms, accounts fully usable. |
| **Previously in flight, now shipped** | **The account system, end to end.** Started as storage: `data/accounts.db` and `data/learning.db` were on Render's ephemeral disk, so a redeploy deleted every account and all cross-game history (DEPLOY.md blocker 1). Both now live in one Neon database. Then the product half — the audit found the backend was genuinely persistent and the *frontend* was not, so `/signin`, `/signup` and `/settings` became real routes, signup collects an email, and the five preferences became account-owned rows instead of `localStorage`. Then password reset by email. **New deploy requirements**: `DATABASE_URL`, `SESSION_COOKIE_SECRET`, `FRONTEND_URL`, and `psycopg[binary,pool]` — the first new dependency and required env vars in a while. |
| **Superseded** | **Postgres.** `data/accounts.db` and `data/learning.db` were both on Render's ephemeral disk, so a redeploy deleted every account and all cross-game history — DEPLOY.md's blocker 1. Both now live in one Neon database (`db.py`, `schema.sql`). Guest history became **claimable** in the same work, which inverted `guest_learning.py` (deleted) — §13 has the whole story, including the three guardrails that replaced it. Needs `DATABASE_URL` on Render before this is deployed, and adds `psycopg[binary,pool]` — the first new dependency and first new required env var in a while. |
| **What just landed** | Post-Mortem AND the UI overhaul AND the QA fixes, in one merge (`ec47f5e`). Neither feature had ever been deployed. |
| **Then** | **The interaction and game-state pass** (§19). Drag-to-move added to Play alongside click-to-move; one shared reading of check/checkmate/stalemate/draw (`boardState.ts`); the checked king's square marked red on all three boards; a translucent end-state layer over the board with the mode's own reset under it. Three real bugs fixed on the way — see §19. |
| **After that** | **A full product audit pass** (§20). Three confirmed findings, all fixed: Learn's `Forward` and Review's `Next` offered a step where there provably was none, and Review's empty canvas made a privacy claim the coach contradicts. Everything else checked came back clean or already correct — §20 lists what was checked and found to need nothing, which is the half of an audit that is worth writing down. |
| **After that** | **The learning loop, v0** (§21). Review has a fourth tab: state what you were trying to do, get an evidence-grounded diagnosis filed under one of eight controlled themes, play the better move on the real board, and take one certified fresh position testing the same idea. Corrections and practice accumulate per guest **in memory** - §13's "nothing is saved" contract is intact, and §21 says exactly what that means for how long a card lasts. |
| **Fixed before that** | **Play Mode's eval bar.** It stood beside the board, inside a column sized to exactly the board's width, so switching *Engine numbers* on pushed the board frame ~50px past its own column — under the coaching tab strip and over the moves list. It became a horizontal strip under the board for a while; **it is vertical and beside the board again as of §29**, with the column reserving its slot (`--ws-eval-slot`), which is what the first attempt lacked. |
| **Master** | `origin/master` is **`d89e91e`**, pushed 2026-09-13. Since `b4d96e2` the releases added migration 010 (`moves.opponent_profile`, in `818a6b0`) and no new env vars or dependencies; the coach-style release adds nothing at all to deploy configuration. Render and Vercel build automatically from this ref. |
| **Deploy state** | **PUSHED, LIVE ROLLOUT UNVERIFIED.** The last proven in-step deployment was the 2026-09-08 build. Do not repeat that old conclusion for `d89e91e`: compare backend `version` in `/api/health` with the frontend footer build id after the auto-deploys complete. Migrations 009 and 010 are additive, but their presence in the dev database is not proof that Render applied them. |
| **Tests** | **1773 checks across 25 suites** (§6). The full matrix was last run end to end on 2026-09-13 for the opponent-profile release; the coach-style release reran the eight suites it touches (`test_coach_style` 72, `test_move_speaker_stance` 39, `test_guided_play_api` 44, `test_decide_integration` 20, `test_gemini_move` 11, `test_guided_play` 42, `test_opponent_profiles` 105, `test_player_state` 47) plus `coach-style.mjs` 27 and `ui.mjs` 118, with build and lint clean. Database suites must use disposable schemas. |
| **Driven live** | yes, on :3001 — import, navigate, branch, engine reply, scan, coach, both themes; and for §19, drag and click in all three modes, mouse and touch, six viewports, 0 axe violations |
| **Playtested** | yes — full-service QA pass, 2026-09-05. Verdict **READY WITH MINOR ISSUES** (§16) |
| **Docker build (:3000)** | **Predates `b4d96e2` and does not contain §30.** It was last rebuilt from the working tree carrying §27 and §28 (`zugzwang:v4.5`) and was healthy then: 7/7 document security headers, 7/7 API headers, zero CSP violations across six routes. No Docker build was performed for the Barry validation-readiness release; use `:3001` to inspect it until the image is rebuilt. |
| **The proxy question, answered** | `TRUSTED_PROXY_HOPS=1` is correct behind a proxy that genuinely appends. Proved on `:3000`, whose nginx uses `$proxy_add_x_forwarded_for`: twelve failed logins with a FIXED `X-Forwarded-For` gave `429` at the eleventh, and twelve with a ROTATING one gave **the same** - the forged entry no longer buys a fresh bucket. This does not prove Render's edge appends, but it does prove the code is right for an edge that does. |
| **Docker build, previously** | ⚠️ **Predated everything in §27.** The image was last built on 2026-09-06 and carries NONE of the beta-hardening work - no promotion picker, no board-size control, no Review seats, and the old move-feedback pipeline. That is not a bug, it is what the middle row of THE THREE BUILDS means: its source is baked in, so it shows you the tree as of its last build. Rebuild before using it to judge any of this. The dated record below is still accurate for the build it describes. **rebuilt from `master` (`8eef622`, the learning loop) on 2026-09-06** — container healthy, **73/73 UI and 119/119 interaction invariants pass against `:3000`**, and the whole learning loop was driven through the shipped build in a browser (26/26) against the real Gemini path. Newest rollback point: `zugzwang:v4.5-pre-learning-loop`. Previously rebuilt on 2026-09-06 from `9e7dff4` — image `zugzwang:v4.5` carries the interaction pass and the audit fixes; container healthy, **73/73 UI and 119/119 interaction invariants pass against `:3000`**, and the startup lines confirm Gemini on all three paths. Newest rollback point: `zugzwang:v4.5-pre-interaction`. Previously rebuilt from `ui-overhaul` on 2026-09-05 — image `zugzwang:v4.5` **carries the overhaul and the QA fixes**. 58/58 invariants pass against :3000; the mate and figurine fixes verified inside the container. Rollback points: `zugzwang:v4.5-pre-ui-overhaul` (the Post-Mortem build) and `zugzwang:v4.5-pre-postmortem`. No git move was made; `master` is untouched. |

### If you are auditing this branch, start here

Written for the next agent rather than for the author. The claims most worth
checking, and the things already known to be wrong or missing, so no one
spends time rediscovering them.

**Load-bearing invariants — breaking any of these is a release blocker:**

1. `/api/auth/forgot-password` answers **identically** for a registered
   address, an unregistered one, a malformed one, a Google-only account, and
   a dead mail provider. `test_accounts_postgres.py` §13 and §14 assert this.
   The honest "email is unavailable" message is safe *only* because it keys on
   configuration, never on the address.
2. `games.owner` is the only ownership column; `moves` inherit by cascade.
   Every profile read filters on it. `reweight_candidates()` is the one
   deliberate exception and reads `owner LIKE 'user:%'` only.
3. Claiming is one-way, once per guest identity, and reads the identity from
   `identity_of(request)` — never from client input.
4. Live game state is per session. No module-level mutable game state, no
   `global` statements in `app.py`.
5. Reset tokens and session tokens are stored **hashed only**.

**Known gaps, already decided — not oversights:**

- Corrections, practice attempts, sandbox sessions and Post-Mortem reviews are
  **in memory** and are NOT claimed at signup. Scoped decision; see the end of
  §13.
- **Email addresses are not verified.** This is why a Google sign-in whose
  email matches a password account is *refused* rather than linked.
- **Google OAuth has never run against real Google.** Everything after Google
  answers is tested with the exchange faked.
- **Mailjet delivery does not work**: the account is blocked at their end
  (`mj-0001`), confirmed by the account API answering 200 while `/v3.1/send`
  answers 401. `EMAIL_ENABLED=false` is the intended state until resolved. The
  sender is also a `@gmail.com` address, which fails DMARC alignment through
  an ESP and should move to an owned domain before real users.

**Corrections made mid-session, in case an older claim is still believed:**

- An earlier version of this file called the deployment "the Vercel-to-Render
  origin split". It is not: `vercel.json` rewrites `/api` server-side, so the
  browser only sees the Vercel origin. Read from config, not observed live.
- Two accounts "vanished" during testing and were twice reported as an
  unexplained one-off. They were not: it was the Neon pooler leaking
  `search_path` between clients. See the trap in §13 — that is now the single
  highest-value thing in this file.
- "Duplicate backend processes on :8081" was partly a `pgrep -f` artifact
  matching the auditing command's own string. Count with
  `ps -eo pid,args | grep "[u]vicorn app:app" | grep -v "bash -c"`.

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
- The identity cookie is `SameSite=Lax; Secure` in production. **This changed
  in §28** - it used to be `SameSite=None`, and the paragraph below explains
  why that was never buying anything.

  **The correction this file already carried, now finished.** It once
  described the deployment as "the Vercel-to-Render origin split", implying
  the browser talks to Render directly. It does not: `chess-frontend/vercel.json`
  rewrites `/api/:path*` to the Render URL, which is a server-side proxy, so
  the browser only ever sees the Vercel origin and the cookies are
  first-party. That correction used to end "not verified against the live site
  - the rewrite is read from config, not observed". **It is verified now:**
  `curl -sD- https://chess-app-rho-swart.vercel.app/api/health` answers 200
  from the Vercel origin carrying `x-render-origin-server: uvicorn`.

  Which settles the flag. If the API is first-party, `SameSite=None` is not
  belt-and-braces - it is a hole, because `None` means "send this cookie
  cross-site" and about twenty POST routes here take no request body and so
  can be driven by a plain HTML form on any website. `Lax` closes it and costs
  nothing. See §28.
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
| `move_quality.py` | chess.com-style grading + the `OPENING_LINES` book. **`NOISE_MARGIN` is a measurement, not a taste setting** - see §27 |
| `move_feedback_log.py` | **one greppable line per graded half-move**, from every mode - §27 |
| `engine_evidence.py` | **the engine's opinion, rendered for a model to quote.** Shared by Play's chat and Learn's coach |
| `gemini_http.py` | **one pooled connection to Gemini**, shared by all six callers - §27 |
| `pgn_text.py` | figurine movetext to letters, shared by Review's importer and Learn's paste path |
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
| `opponent_profiles.py` | **the seven opponent profiles** - the only strength setting; `get_profile()` never raises, unknown → club (§37) |
| `candidate_selection.py` | **the candidate pool**: python-chess facts for every legal move, then a profile-weighted sample of three (§37) |
| `chess-frontend/src/opponentProfiles.ts` | the same seven profiles as the UI names them; `test_opponent_profiles.py` fails if it drifts from the Python table |
| `tools/profile_sanity.py` | **does each profile actually play weaker, like a person** - engine-only games per profile, mean cpl / blunder rate / top-move share (§37) |
| `migrations/010_opponent_profile.sql` | `moves.opponent_profile text`; the integer `difficulty` column stays, no longer written |
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
| `chess-frontend/src/moveQuality.ts` | **the grade palette, shared by Play and Review**, plus `gradeSentence` - the one place the frontend phrases a verdict |
| `chess-frontend/src/moveTiming.ts` | move timings in the browser, off unless `zugzwang-move-timing` is set |
| `chess-frontend/src/components/PromotionPicker.tsx` | **which piece a pawn becomes**, on all three boards - §27 |
| `chess-frontend/src/hooks/useBoardScale.tsx` | **the board-size preference and the whole sizing chain** (`useBoardSizing`) - §27 |
| `chess-frontend/src/components/ThemeToggle.tsx` | the theme switch |
| `chess-frontend/src/components/AccountMenu.tsx` | the account UI + the unavailable notice |
| `chess-frontend/src/services/authService.ts` | client for `/api/auth/*` |
| `chess-frontend/src/services/http.ts` | `apiFetch` — carries the identity cookie, §13 |
| `chess-frontend/src/components/EmptyState.tsx` | empty/loading panel states |
| `OBSIDIAN_DESIGN.md` | **read before touching any CSS** |
| `DEPLOY.md` | Render + Vercel click-path and known limits |
| `tools/verify/ui.mjs` | **31 frontend invariants against the running app** (§10) |
| `tools/verify/lifecycle.mjs` | **the account lifecycle in a real browser** - guest, play, signup, logout, guest (§10, §22) |
| `beta_service.py` | **closed beta: codes, hashing, redemption, grants** - §26 |
| `beta_gate.py` | **the middleware that refuses.** Deny by default, every `/api` route |
| `beta_api.py` | `/api/beta/status` and `/api/beta/redeem` - the gate's own open routes |
| `migrations/008_beta_access.sql` | `beta_codes`, `beta_redemptions`, `users.beta_access` |
| `tools/beta_codes.py` | **the beta admin CLI. There is no admin endpoint** |
| `chess-frontend/src/components/BetaGate.tsx` | **what the browser draws while locked out** - it authorizes nothing |
| `chess-frontend/src/pages/BetaLanding.tsx` | the door: logo, explanation, code box |
| `chess-frontend/src/pages/BetaPages.tsx` | request access, contact, privacy, terms |
| `chess-frontend/src/pages/About.tsx` | **what the app is, and what it keeps** - §23 |
| `chess-frontend/src/components/DataRetention.tsx` | **the retention facts, rendered in two places from one source** |
| `chess-frontend/src/components/SiteFooter.tsx` | About + the build id, on the pages that scroll |
| `chess-frontend/src/buildInfo.ts` | the commit this bundle was built from |
| `pattern_detectors.py` | **the twelve-theme taxonomy and the per-ply detectors (pure)** - §24 |
| `profile_service.py` | **the imported library, and the profile aggregated out of it** |
| `profile_worker.py` | **the background scan** - one game at a time, yielding the engine |
| `profile_api.py` | `/api/profile/*` |
| `chess-frontend/src/pages/ImprovementProfile.tsx` | **the multi-game workflow** at `/profile` |
| `tools/verify/profile.mjs` | **the workflow in a browser** (§10, §24) |
| `test_move_feedback.py` | **the move-feedback pipeline as properties** (§6, §27) |
| `db_writer.py` | **one background thread for the rows nobody waits for** - product events and grade audits queue here instead of blocking the request (§36) |
| `tools/latency_probe.py` | **the API latency profile, flow by flow** - real Stockfish and Gemini against `:8081` (§36) |
| `tools/latency_browser.mjs` | **the browser's half of that profile** - click to piece to thinking to reply to explanation, and the handoff (§36) |
| `test_latency_paths.py` | **the engine gate, the writer, and /api/move answering first** (§6, §36) |

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

### Vercel Web Analytics

`@vercel/analytics` is mounted in `main.tsx` **only in a bundle Vercel built**
(`__ON_VERCEL__`, a Vite `define` from the `VERCEL=1` Vercel sets at build
time). A plain `npm run build` tree-shakes it out entirely, which is why the
Docker build and the dev server never request `/_vercel/insights/script.js`
- a path nginx would answer with `index.html`, and a console error on every
load. The script and its beacon are same-origin, so the shipping CSP
(`script-src 'self'; connect-src 'self'`) needed no change. It reports
nothing until **Analytics is enabled on the project in the Vercel dashboard**
(a toggle with no CLI or API), and `/privacy` says what it collects: page
views, cookieless, no IP stored, no games or accounts in a page address.

## 3. Run it locally

Everything runs in **WSL Ubuntu** (no Python or Node on the Windows side).
Stockfish is at `/usr/games/stockfish`, the path the code expects.

There are **two** local stacks and they are independent. The third build - the
deployed one on Render and Vercel - is not local at all; see **THE THREE
BUILDS** at the top of this file, which is the section to read first if you are
unsure which one you are looking at.

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
    over the moves list. **The bar is vertical and beside the board again
    (§29), and this is how it does not repeat that:** it takes no space in the
    flow (`position: absolute; right: 100%` on the board frame) and the column
    reserves the room for it at all times as `--ws-eval-slot` padding, in the
    two modes that have one. Anything ELSE placed beside the board without a
    reserved slot will push the board exactly as before.
12. **`customSquareStyles` land on the square's INNER div**, not on the
    element carrying `data-square`. react-chessboard renders
    `<div data-square="e4" style="background-color: var(--board-light)">` and
    then a child div that gets your style. Reading the outer one back in a
    test returns the board colour for all 64 squares, so a working highlight
    looks exactly like a feature that was never wired up. Both verify tools
    read `el.firstElementChild`.
13. **react-chessboard opens its own promotion dialog on a drag.** All three
    boards still set `autoPromoteToQueen`, and it no longer means what its
    name says: since the beta-hardening pass (§27) the app asks which piece,
    through its own `PromotionPicker`. The prop's only remaining job is to
    stop the LIBRARY opening a dialog on a drag - with it off, a dragged
    promotion would get react-chessboard's dialog and a clicked one would get
    ours, which is the same inconsistency this trap was written about. **Do
    not "tidy" it away**: the four buttons a user sees come from
    `PromotionPicker.tsx`, and this prop is what keeps the library out of the
    way on both paths.
14. **A test suite that HANGS rather than failing is trap 6.** An
    exception anywhere in a suite that imported `app` and touched Stockfish
    skips its closing `app.stockfish_service.close()`, and python-chess's
    non-daemon engine thread then holds the process open forever. A real one
    cost ten minutes here before it was recognised: the actual fault was a
    one-line `UnboundLocalError` on a rarely-taken branch. **Do not wait one
    out** - re-run it under `timeout 120` and read the traceback.

    Two things hid it, and both are worth avoiding: piping the run to `tail`
    prints nothing until the process exits (so a hang looks like silence
    rather than like output), and the exception was on the no-API-key path,
    which only one of the suite's six cases reaches.
15. **The backend can be LISTENING and still be dead.** `SSL SYSCALL error: No
    route to host` in `/tmp/backend.log` means the Neon connection dropped (a
    WSL network blip is enough), and requests then hang on the pool while the
    port stays open. `ps` and `ss` both look healthy; `curl` times out. It is
    not the engine and not your code - restart the backend. And see §12: a
    `pkill` does not always take, so confirm with `ss -ltnp | grep :8081`
    which PID actually holds the port rather than counting processes in `ps`.
16. **A browser tab open across a long session goes stale.** HMR sockets drop,
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
| `POSTMORTEM_SCAN_MULTIPV` | 1 | 2 buys Review the `Great` grade and costs **+80%** on the scan — measured, see §27 |
| `RATE_LIMITS_ENABLED` | **true** | **dev only.** Only the literal `false` turns limits off, and doing so logs a warning at boot. Never set it in production (§27) |
| `POSTMORTEM_PROBE_DEPTH` | `STOCKFISH_DEPTH` | depth for one position the user asked about |
| `GEMINI_*_TIMEOUT` | 6–20 | seconds per model, per service |
| `GEMINI_MOVE_HEDGE_DELAY` | 1.4 | seconds before a second model is asked **alongside** the first, so a hung model does not cost the whole timeout (§27). 0 disables |
| `GEMINI_MOVE_MAX_IN_FLIGHT` | 3 | most requests in flight for one move |
| `GEMINI_DIAGNOSIS_HEDGE_DELAY` | 6 | seconds before a second model is asked alongside the diagnosis lead (§36). Deliberately long: the lead thinks for 5-13s and the hedge models do not think at all |
| `GEMINI_DIAGNOSIS_MAX_IN_FLIGHT` | 2 | most requests in flight for one diagnosis |
| `DATABASE_POOL_MAX` | 5 | Postgres connections. The background writer (`db_writer.py`) holds one while it drains |
| `GEMINI_NARRATION_CONCURRENCY` | 2 | max narration calls in flight |
| `STOCKFISH_DEPTH` | 15 (12 on Render) | full depth |
| `STOCKFISH_RANK_DEPTH` | 10 (8 on Render) | shallow stage-1 ordering |
| `DISABLE_LANGFLOW` | true in both images | skip the Langflow path entirely |
| `ACCOUNTS_ENABLED` | false | accounts refuse with 503 until this is true (§13). **`render.yaml` sets it to `true`** - the default is off, the deployment is on |
| `BETA_ACCESS_REQUIRED` | **true** | the closed beta gate (§26). **Default is CLOSED** - only the literal `false` opens the app |
| `BETA_CODE_PEPPER` | `SESSION_COOKIE_SECRET` | keys the beta code hash. **Rotating it kills every outstanding code, irrecoverably** |
| `BETA_CACHE_TTL` | 30 | seconds an access answer is cached in-process. Invalidated explicitly on redemption, so this is a staleness bound and not the mechanism |
| `VITE_CONTACT_EMAIL` | — | frontend: the address on the landing page's Contact and Request pages. Unset, they say so rather than inventing one |
| `ALLOWED_ORIGINS` | localhost | comma-separated CORS allowlist; **required in production** |
| `COOKIE_SAMESITE` / `COOKIE_SECURE` | lax/none by host | identity cookie flags, §13 |
| `ENABLE_DOCS` | on locally, off in production | serve `/docs` and `/openapi.json` |
| `ADMIN_EMAILS` | — | comma-separated account emails allowed into the read-only admin panel (`/admin`, `GET /api/admin/overview`, `admin_api.py`). **Set it for the owner account before deploying.** Unset, nobody is admin by email |
| `ADMIN_INVITE_SECRET` | — | optional. Keys the HMAC of one-time admin invite codes (`admin_invites.py`, `POST /api/account/become-admin`). Rotating it voids every hash minted under it |
| `ADMIN_INVITE_CODE_HASHES` | — | optional. Comma-separated HMACs from `tools/generate_admin_invites.py --count 10`. The script prints the raw codes ONCE - **never commit or paste a raw code anywhere**; only these hashes and the secret go into env. A redeemed hash is recorded in `admin_invite_redemptions` (migration 012) and cannot be reused; to rotate, replace both env vars (the old redemption rows can stay - they match nothing) |
| `BUILD_SHA` | `dev` | the commit `/api/health` reports as `version`. Vercel's `VERCEL_GIT_COMMIT_SHA` feeds the frontend's copy |
| `VITE_PROXY_TARGET` / `VITE_POLL` | — | dev server backend + watcher |

**All six model chains lead with a different model on purpose.** They share
one API key, and when moves and chat both led with `gemini-3.5-flash`, chat
immediately got HTTP 429 from move traffic. `test_scenario.py` asserts the
six leads stay distinct — if you retune, keep that true. `gemini-3.6-flash`
is last in every chain: it does not refuse, it *hangs*, so leading with it
spends the full timeout on every request.

---


## 6. Tests — 1887 checks across 27 suites

| file | what | needs |
|---|---|---|
| `test_admin_api.py` | **80, the read-only admin panel: guest 401 / non-admin 403 / allowlisted admin 200, every overview group (funnel, recent accounts, import, provider, review and evidence health), an empty schema answering nulls and `not_tracked` rather than 500, and no PGN/FEN/hash/token/trace anywhere in the body; disposable schema** | `DATABASE_URL` |
| `test_account_admin_invites.py` | **34, one-time admin invite codes: normalisation, HMAC under the secret, guest refused, one identical refusal for malformed/unknown/spent, a valid code makes the account admin and opens the overview without re-login, the same code refused for a second account, `ADMIN_EMAILS` admins unchanged, the rate limit, nothing configured; disposable schema** | `DATABASE_URL` |
| `test_gemini_move.py` | 11, mocked HTTP | — |
| `test_coach_style.py` | **72, Advanced Coach Settings (§39): the five bands per axis and their cut points, the gap between the extremes (opposite required/forbidden wording, under 20% shared vocabulary, an off-position tone example in every band), sampling only for explanation calls, the respect and no-invention rules at every setting, the block in all three chat personas, the Play/Guided move prompt with a byte-identical evidence block across every style, profile knobs untouched, the sanitiser, and the account allowlist, pure** | — |
| `test_move_speaker_stance.py` | **39, the Play move speaker stance (§40): the five prompt blocks, first person and present tense, the As Black/White ban, FEN/history/shortlist/facts preserved, all seven profiles, both Guided states, the sanitiser samples and the Watch out separation, pure** | — |
| `test_opponent_profiles.py` | **105, the profiles and the pool (§37): the table and its TS mirror, the python-chess annotation (check, capture, hangs, answers a threat, walks into mate, forced), eligibility and seeded sampling per profile - a beginner's pool holds real mistakes and rarely the engine move, a master's never exceeds 12cpl, a missed mate is really missed, no pool is empty, illegal or carries the reference** | — |
| `test_guided_play.py` | **42, Guided Play (§31): the python-chess facts, the guided prompt, the Watch out splitter, the coach turn's own key, the settings key and the events, pure** | — |
| `test_guided_play_api.py` | **44, Guided Play through `decide_ai_move` and both Play routes, Gemini faked; the profile block in every prompt, the own-flaw allowance only for weak profiles; the profile routes; events carry the decision record and no text** | Stockfish |
| `test_play_review_handoff.py` | **29, "Review this game" (§32): an unfinished game refused, a mated game becomes an owned review with the right result/seats/plies/termination and a running scan, Black as the player, a promotion replays, a stalemate, and the events carry no moves** | Stockfish |
| `test_sandbox_state.py` | 41, move tree + sessions, pure | — |
| `test_player_state.py` | **47, per-player isolation, pure, plus the coach-turn helper of §29 and the bounded coach-style pair** | — |
| `test_scenario.py` | **89**, incl. a 480-position legality fuzz and mate-request verification | — |
| `test_decide_integration.py` | 20, real Stockfish + faked Gemini: the decision record, the shortlist refusal, the profile block in the prompt | Stockfish |
| `test_sandbox_api.py` | **90**, `/api/sandbox/*` end to end | Stockfish |
| `test_sandbox_narration.py` | 34, narration + parallel wiring | Stockfish |
| `test_accounts.py` | **86, guest mode + accounts-off + auth internals** | Stockfish + `DATABASE_URL` |
| `test_postmortem_state.py` | **65, PGN ingestion (incl. figurine notation) + the immutable game, pure** | — |
| `test_postmortem_api.py` | **82, `/api/postmortem/*` end to end, including full-game coverage versus a one-decision-per-side score denominator** | Stockfish |
| `test_learning_loop.py` | **90, the store, the diagnosis validator, the event sink, the engine-fallback card's cost wording on the mate scale, pure** | — |
| `test_retest_bank.py` | **34, every re-test position re-certified at depth 20** | Stockfish |
| `test_learning_loop_api.py` | **89, `/api/learning-loop/*` end to end, coach faked, including privacy-bounded events, stage timing, and audit provenance** | Stockfish |
| `test_improvement_profile.py` | **129, the Improvement Profile: detection, storage, aggregation, the API, and the three audit regressions of §24** | Stockfish + `DATABASE_URL` |
| `test_move_feedback.py` | **100, the move-feedback pipeline (§27): the engine's own best move is never criticised, both colours are graded in their own frame, promotions and underpromotions grade as the piece they became, every grade carries its provenance, a critical label has to clear its threshold by `NOISE_MARGIN`, the coach is handed the grade with the rule that it is not its to make, and `RATE_LIMITS_ENABLED` opens only on the literal "false"** | Stockfish |
| `test_latency_paths.py` | **21, the latency pass (§36): a priority caller goes ahead of the queue at the engine gate and nothing slips between two searches of one section, the background writer keeps order and logs a failing write, `/api/move` answers in under one engine search, and a second move played the instant the AI replies is answered rather than skipped** | Stockfish |
| `test_security.py` | **61, the hardening invariants (§28): the security headers on every response including the ones no route produces, HSTS as a production-only promise, a cross-site write refused by origin, an oversized body refused before it is buffered, a rate-limit bucket key the caller cannot write, the middleware order, and that the route map at `/` follows the docs flag** | — |
| `test_beta_access.py` | **123, the closed beta gate: deny-by-default enumerated from the real route table, signup withheld until redemption (password and Google), returning Google sign-in, forged-input bypasses, one-time redemption under an eight-thread race, identical refusals, access following the account, both rate-limit buckets, the chosen owner key, and that it fails closed** (§26) | Stockfish + `DATABASE_URL` |
| `test_accounts_postgres.py` | **230, accounts ON: migrations, ownership, claiming, cross-account isolation, live-session isolation, the global AI boundary, retention, the account area (profile, preferences, password, deletion), password reset, email being unavailable, rate limiting, security probes, the three release-gate regressions of §22 - the guest identity lifecycle, the reset verb, and a build with no migrations - and §23's email-availability and build-id checks** | Stockfish + `DATABASE_URL` |

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
/tmp/chessapp/bin/python -u test_opponent_profiles.py && \
/tmp/chessapp/bin/python -u test_gemini_move.py && \
/tmp/chessapp/bin/python -u test_coach_style.py && \
/tmp/chessapp/bin/python -u test_move_speaker_stance.py && \
/tmp/chessapp/bin/python -u test_guided_play.py && \
DISABLE_LANGFLOW=true /tmp/chessapp/bin/python -u test_guided_play_api.py && \
DISABLE_LANGFLOW=true /tmp/chessapp/bin/python -u test_play_review_handoff.py && \
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
DISABLE_LANGFLOW=true /tmp/chessapp/bin/python -u test_improvement_profile.py && \
DISABLE_LANGFLOW=true /tmp/chessapp/bin/python -u test_accounts_postgres.py && \
DISABLE_LANGFLOW=true /tmp/chessapp/bin/python -u test_beta_access.py && \
DISABLE_LANGFLOW=true /tmp/chessapp/bin/python -u test_move_feedback.py && \
DISABLE_LANGFLOW=true /tmp/chessapp/bin/python -u test_latency_paths.py && \
DISABLE_LANGFLOW=true /tmp/chessapp/bin/python -u test_security.py
/tmp/chessapp/bin/python -c 'import db; db.drop_schema()'
```

`test_security.py` is last but needs neither Stockfish nor `DATABASE_URL`, so
it is also the one to run ALONE and first when the change under test is a
header, a cookie flag, a middleware or a limit - it answers in seconds.

The last line is not optional housekeeping. Leave it out and every run
accumulates another schema in the Neon project, and the free plan's storage
is finite.

Spell the eighteen out — a `for t in ...` loop inside `bash -lc "..."` has its
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

> ⚠️ **The app is behind the closed beta gate (§26), so every tool in
> `tools/verify/` except `beta.mjs` and `boardstate.mjs` needs the backend
> started with `BETA_ACCESS_REQUIRED=false`** - otherwise the browser gets the
> landing page and the first `waitForSelector` times out on a board that was
> never going to render. Verified this way: 118/118 UI, 127/127 interaction,
> 22/22 board state.


```bash
node tools/verify/boardstate.mjs                # no browser, no server, ~1s
node tools/verify/ui.mjs                        # layout; dev server on :3001
node tools/verify/interaction.mjs               # drag, check, the endings
node tools/verify/lifecycle.mjs                 # guest -> signup -> logout -> guest
node tools/verify/profile.mjs                    # the multi-game workflow
node tools/verify/chat.mjs                       # the unified Chat + Actions panel (§29); calls Gemini
node tools/verify/overlap.mjs                    # geometric sweep: no two visible elements intersect, nothing past the viewport - 3 modes x every tab x 5 viewports x 2 themes (300 checks)
node tools/verify/guided.mjs                     # Guided Play (§31): the switch, the Chat shortcut, the Watch out block, the AI strength label - 2 laptop viewports (52 checks); calls Gemini
node tools/verify/review-handoff.mjs             # "Review this game" (§32): a real game to mate at strength 1, the handoff into an analysed review, served endings at 4 viewports, the failure paths (87 checks); calls Gemini
node tools/verify/loop.mjs                       # the whole loop (§33): a deliberate hang in Play -> Review finds it -> intent -> correction card -> fresh practice -> hint -> attempt, at 1366 and 1280 (51 checks); calls Gemini
node tools/verify/coach-style.mjs                # Advanced Coach Settings (§39): open/close, drag, sliders, the four preview quadrants, six presets, persistence, the move payload, desktop + 390px (27 checks); no Gemini
node tools/verify/layout-stress.mjs              # board-size stability across 10 viewports and 6 zoom levels, watching every frame, PLUS the readability audit of every tab and the long-content/correction-lifecycle states (§34); `--quick` for 5 cases
node tools/verify/ui.mjs http://localhost:3000  # or the container
node tools/verify/ui.mjs http://localhost:3001 --shots out/
```

**`lifecycle.mjs` is 12 checks and needs a backend with `ACCOUNTS_ENABLED=true`
behind :3001**, which the ordinary dev runner does not set - see §22. It owns
the one thing TestClient cannot see: that a real browser, carrying real cookies
through a redirect-driven signup and a button-driven sign-out, ends up where
the server thinks it does. The check that matters most in it is the plainest
one - after logging out, the board on screen is the starting position.

There are three tools and they own different things. **`ui.mjs` owns layout**
— where things are and whether anything overflows. **`interaction.mjs` owns
what the board lets you do and what it says about itself** — that a drag only
ever offers legal squares, that the checked king is marked, that the three
endings raise the right layer and freeze the board, in all three modes, on
mouse and on touch, at six viewports. **`boardstate.mjs` owns the chess**:
22 positions through `boardState.ts` with no browser at all, which is the
cheap half of the pair and the one to run while iterating.

`interaction.mjs` is **127 checks** and `boardstate.mjs` **22**, both
currently passing. The last eight are the promotion picker (§27): that a
promotion asks the same question whether it was dragged or clicked, that the
pawn does not move while it asks, that Escape leaves the position untouched,
and that a knight and a rook actually arrive when chosen. The check they
replaced asserted the opposite - that a dragged promotion auto-queened with no
dialog - which was right while queen was the only option the app had. The last 12 are the transport's own ends (§20): the only
controls in Learn and Review that could be pressed with provably nothing to
do. The 22 include every distinction the two endings turn on:
mate with a block available, mate with a capture available, double check,
smothered mate, stalemate with the king boxed in, stalemate where another
piece can still move, and a king with no square that is not in check.

**ui.mjs is 118 checks, all currently passing**: console errors and overflow in both modes
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
Learner Mode layout claims repeated for `Review` (with one of them rewritten -
see below) (it has the same two-column
shape and would fail them the same ways), that stepping through a game resizes
nothing, and that the empty canvas is a real button large enough to drop a file
on. The tool imports a PGN itself — Morphy's Opera Game, inline in the file —
because an empty canvas exercises almost none of the mode.

> ⚠️ **One Review invariant changed shape in §27, and the reason matters more
> than the change.** It used to assert that `.pm-board-wrapper` and `.pm-tabs`
> start on the same line. The rule §11 is actually about is that the two
> COLUMNS begin at the same height; the board was a valid proxy for its column
> only while the board was the first thing in it. Review now carries a player
> seat above the board, exactly as Play always has, so the check reads
> `.pm-board-column` against `.pm-tabs` and the seat's own position is asserted
> separately. **Weakening a failing invariant is how a regression net rots**,
> so if you find yourself doing this, check first that the RULE still holds -
> here it does, and it is measured: both columns start at y=147 at all three
> widths.

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

- **Do not locate a meta-row control by its label text.** The row carries two
  spellings of each label - the full one and a short one for when the board
  column is tight (`.ws-label-full` / `.ws-label-tight` in shell.css) - and
  exactly one of them is `display: none` at any width. `getByText('Engine
  numbers')` resolves to the hidden span and then waits forever for it to
  become visible, which looks exactly like a control that stopped working.
  Click the `input` or the `select` instead.
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
- **The eval bar's room is always in the layout**, and the bar is hidden with
  `visibility` when off. When it was a row under the board, un-rendering it
  shrank the board by 19px → narrowed the column → wrapped the display row
  (+43px) → shrank again: a 19px strip cost 70px of board and dragged the
  panel and tabs with it, because both size from `--board-size`. It is a
  vertical bar beside the board now (§29), with the same rule kept: the slot
  it hangs in is reserved whether it is showing or not, so toggling it moves
  nothing.
- **`useFittedBoardSize` measures rather than models.** The chrome under the
  board is not a fixed height — the control row wraps differently at different
  widths — so `useBoardSize`'s hand-tuned constant (340 → 372 → 360 → 297) was
  right at exactly one viewport each time. It corrects by the page's actual
  overflow, so the next row added under the board needs no retuning.

One segmented-control treatment is shared by the header (`Play`/`Learn`), the
game's analysis rail, and the sandbox tabs (`Chat`/`Line`/`Board`/`Actions`, §29).
They all answer "which view am I looking at" and used to be three designs.

**Learner Mode's controls, as they now stand (§29).** Below the board: an
always-present alert strip (check / checkmate / stalemate / draw, the same
three colours the real game uses, with the eval bar standing to the LEFT
of the board rather than on a row under it), then
**AI move / Take over / Back / Forward**, then **Reset board / View as
Black** as a second two-button row, the reserved status line, then the
opponent-level select alone on the meta row (§37). `Eval bar`, `Coach my moves` and
the board size are on the Actions tab.

- **AI move toggles** — it starts the line playing and stops it, and says which
  in its own label. "Play line" is gone; single-stepping went with it.
- **Take over** sits in the transport row because the thing worth discovering
  there is that you can play too.
- **One reset**, always to the standard opening. It carries a staged profile
  ("Reset at Expert — about 2100").
- The opponent-level select reads "Club — about 1500" under an **Opponent
  level** label (§37); before that it had no visible label and read "12 — Club", which
  is the label and the value in one. Removing that one redundant word was the
  9px that let the display row fit one line, which is worth 43px of board.

**What persists** (all `localStorage`): `chess-mode` (Play vs Learn),
`sandbox-panel`, `sandbox-piece-theme`, `sandbox-eval-bar`, `sandbox-session`,
plus the game's `chess-active-section`, `chess-piece-theme`,
`chess-engine-numbers`, `chess-coordinates`, `chess-move-quality`,
`chess-guided-play` (§31), and
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
# Both of these are DEV ONLY and both fail safe - only the literal "false"
# opens either, and the second logs a warning at boot. The gate one is why
# the board loads at all on :3001 (see the padlock note at the top of this
# file); the limits one stops tools/verify taking 429s from its own speed.
export BETA_ACCESS_REQUIRED=false
export RATE_LIMITS_ENABLED=false
# --no-proxy-headers: uvicorn otherwise rewrites the client address from
# X-Forwarded-For, which makes request.client.host caller-controlled and
# defeats every rate limit. Both Dockerfiles carry it too. See §28.
exec /tmp/chessapp/bin/python -u -m uvicorn app:app --host 0.0.0.0 --port 8081 --no-proxy-headers
```

> ⚠️ **`pkill` here does not always work.** uvicorn sometimes survives the
> SIGTERM - the non-daemon engine thread of trap 6 - leaving the old process
> alive beside the new one. `ps` then shows two and only one is real. Settle it
> with `ss -ltnp | grep :8081`, which names the PID actually holding the port,
> and `kill -9` the other.

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

### Retiring a guest identity - the lifecycle, and the blocker it closed

**This was a reproduced release blocker, and it is the reason the sequence
below is three steps rather than one.** A guest played, signed up, their games
were claimed, and they logged out. Logout deleted the session cookie and
nothing else, so the very next request fell back to the *same* `zw_guest`
cookie the browser still held - and that identity still keyed a live
`PlayerSession` holding the board **and** the `current_game_id` of a row the
account now owned. The signed-out visitor was handed the claimed position back
and could go on writing moves into account-owned history.

Claiming alone did not stop this, and could not: it rewrites `games.owner`,
which protects the *stored* rows. The leak was the *live* session, which
nothing had ever been asked to end.

So a guest identity can now be retired, and three things happen together
(`identity.retire_guest`, `auth_api._end_guest_identity`):

1. **Revoked.** `identity.revoke_guest()` records it, and the middleware
   refuses it from then on. The MAC still verifies - the server did mint it -
   so revocation, not the signature, is what stops a saved copy of that cookie
   coming back.
2. **The live `PlayerSession` is dropped.** This is the one that bit.
3. **A fresh guest cookie is issued in the same response**, so the browser has
   a clean identity to fall back to when the account session ends.

| moment | what happens |
|---|---|
| signup / login / Google callback | claim, then retire the guest identity, then start the account session |
| logout | end the account session, issue a **brand-new** guest identity. The `user:<id>` `PlayerSession` is deliberately KEPT, so signing back in returns you to your board |
| account deletion | as logout, and the account's own `PlayerSession` is dropped too - there is nothing left to sign back into |
| password reset completed | every session ended already; a fresh guest identity is issued with the cleared cookie |

> ⚠️ **The revocation set is process-local, and that is deliberate.** Checking
> `claimed_guests` would be a database query on every guest request, on the hot
> path, to answer "no" almost every time - and it is not what makes this safe.
> Two durable things do: ownership (claiming rewrote `games.owner`, and every
> read filters on it) and the fact that `PlayerStore` is memory, so the board
> and the `current_game_id` that were the actual leak do not outlive the
> process. The set closes the window those two leave open - the life of one
> process, where the retired guest's session is still resident - and it is
> bounded at `MAX_REVOKED_GUESTS` because an unbounded set keyed on anything a
> caller influences is a slow memory leak.

> ⚠️ **An endpoint that issues its own guest cookie must win over the
> middleware.** `IdentityMiddleware` appends its `Set-Cookie` after the
> endpoint's, and for the same cookie name the last one is what the browser
> keeps - which would hand back the identity the endpoint had just retired.
> The middleware therefore checks whether the response already sets `zw_guest`
> and stays out of the way if it does. Do not remove that check.

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
- ~~**Underpromotion in a branch is not offered.**~~ **Closed** (§27). It was
  a deliberate gap - stopping to ask interrupts the one interaction Review
  exists for - and the user overturned it when asked directly: the knight that
  forks on arrival and the rook that promotes without stalemating are exactly
  the alternatives someone replays a lost endgame to check. All three modes
  ask now, queen pre-selected so the common case is still one extra click.
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
Chat, Moves and Report (the Chat tab was labelled Coach until §29). It is **not** a fourth mode in the header; the header
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
this product has no product analytics (page-view counting on Vercel came later, §2, and sees no game and no account), and a brief mentioning a funnel is not a reason
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


---

## 22. The release gate — five blockers, and what closed each

An independent audit of the `postgres-storage` branch found five release
blockers. All five are addressed; four were code, one is the user's to do.
Each was **reproduced before it was fixed**, and each has a regression that
fails on the code as it was — that pairing is the point of this section, since
"we fixed it" and "we can tell if it comes back" are different claims.

### 1. The production image shipped without its schema (critical)

`Dockerfile.backend` copied `*.py` and `flows/` and nothing else, so
`migrations/` was never in the image. Reproduced by building the image at
`a923af3` and looking: `/app/migrations` absent.

What made it a blocker rather than a bug is how quietly it failed.
`_migration_files()` returned `[]` for a missing directory, `applied_migrations`
created `schema_migrations` on the way past, no migration ran, and the log said
**"Schema up to date"**. The container was healthy, `/api/health` said
`database: "ok"` — `SELECT 1` works in a schema with nothing in it — and every
application table was missing. The first symptom would have been an error on a
user's first real query, a long way from the cause.

Three changes, because the copy alone would leave the trap armed for the next
Dockerfile:

- `Dockerfile.backend` and the root `Dockerfile` both `COPY migrations/`. The
  root one had the same hole and is what serves `:3000`.
- `db._migration_files()` raises `db.MigrationsMissing` for a missing **or
  empty** directory instead of returning `[]`. Its own exception type, so
  "this build is wrong" is distinguishable from "the database is unreachable"
  — the second is survivable by retrying and the first never is.
- `app.py`'s startup hook calls `db.assert_migrations_present()` **before** it
  looks at `DATABASE_URL`, and lets the exception out. A missing database is
  still not fatal (§13); a missing schema directory now is.

Verified by booting the rebuilt image against an empty disposable Neon schema:
all five migrations applied, all nine tables created, `/api/health` `ok`. And
the failure path, by moving `/app/migrations` aside inside the container:
`Application startup failed. Exiting.`

### 2. A guest kept the game their account had claimed (critical)

Fresh guest → play → signup → games claimed → logout → **the claimed board
came back**, still writable into account-owned history. §13's *Retiring a guest
identity* is the whole story and the fix. Reproduced by running the new
regression against the pre-fix `auth_api.py`: eleven checks fail, and the
signed-out guest is handed `rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR` —
the position it had played before signing up — on a game row the account now
owned.

### 3. `GET /api/reset` destroyed a game in progress (medium)

Every other state-changing endpoint in this app is a POST; this one was the
exception, and a state-changing GET is reachable by anything that can make a
browser follow a URL while it carries its own identity cookie — an `<img>` on
another site, a link, a prefetch. It is `@app.post` now, at the same path, so
only the verb changed. Updated with it: `chessService.resetGameOnServer`, the
two `fetch('/api/reset')` calls in `tools/verify/interaction.mjs`, and both
account suites. GET now answers 405, which is asserted in both.

### 4. Deployment configuration contradicted the runtime

The user chose to **ship accounts** with this release, so `render.yaml` now
declares `ACCOUNTS_ENABLED=true` rather than leaving a flag that decides
whether signup answers 200 or 503 to a default nobody wrote down. It also
declares `COOKIE_SECURE=true` explicitly — `identity.py` already inferred it on
Render, but a security flag that depends on inference is one nobody can check
by reading the blueprint.

Two comment blocks at the foot of `render.yaml` were describing a build that no
longer exists — "accounts would sit on the ephemeral disk", "there is no
password reset" — and both are rewritten. DEPLOY.md's *Before switching them
on* is now a status board rather than a gate, with a new *What is still open
with accounts on* listing what ships unmet.

`app.py` also says it at boot: with `ACCOUNTS_ENABLED=true` and no
`DATABASE_URL`, or no `SESSION_COOKIE_SECRET`, startup logs an ERROR naming
which. Neither failure announces itself at the point it matters otherwise —
the first is a clean boot where every signup fails, the second is a clean boot
where every restart makes every visitor a new guest.

### 5. Rotate the Neon password (the user's to do)

A `DATABASE_URL` was exposed in an audit log. No code change; the steps are in
the release summary and in DEPLOY.md. Nothing here prints it.

### Running the account stack locally

`tools/verify/lifecycle.mjs` and the browser half of any account work need a
backend with accounts **on**, which `/tmp/run_backend.sh` does not set. Copy it
and add two lines — and point it at a disposable schema, for the reason §6
gives about guest rows landing in `public`:

```bash
export ACCOUNTS_ENABLED=true
export DATABASE_SCHEMA=zwdev
```

Drop the schema when you are done:
`DATABASE_SCHEMA=zwdev /tmp/chessapp/bin/python -c 'import db; db.drop_schema()'`


---

## 23. Production surfaces — what the app now says about itself

Accounts are deployed, which changes what the product owes a person: somewhere
to read what is kept and for how long, an honest account of what deleting an
account does, and messaging that does not promise a password reset this
deployment cannot perform.

This is **Scope A** of the longitudinal-learning milestone, and the only part
of it with no dependencies. The design is in
`docs/superpowers/specs/2026-09-08-production-surfaces-design.md`, which also
records the decomposition of the rest: **B** bulk PGN import, **C** the async
analysis pipeline, **D** recurring-pattern detection and the Improvement
Profile, **E** the correction-card loop. Each gets its own spec. None is built.

### What is deliberately NOT here

**No privacy policy, no terms of service, no cookie notice, no contact page.**
The first three need a named data controller, a jurisdiction and a contact
address — facts this codebase does not hold, and a policy written around
invented ones is worse than no policy at all. The user was asked and chose to
skip them for now. The contact page went with them: there is no support
address, and a contact form is unbuildable while email is off because it would
silently discard messages.

The footer is built so adding the legal pages later is two links and two
routes, not a redesign.

### The layout rule this obeys, and why it is not a compromise

**There is no footer inside the app shell**, and there must not be. The
three-mode shell is height-fitted — `useFittedBoardSize` measures the space the
board is allowed to occupy — and traps 8 and 11 in §4 each record an occasion
where an element added to that layout pushed the board frame out of its own
column. So the footer renders only on surfaces that scroll and hold no board:
`/signin`, `/signup`, `/settings`, `/about`, and both password-reset pages.

On the app shell, the way to About is a quiet link in the header row
(`.acct-about`), which carries the build id in its `title`. `ui.mjs` asserts
both halves of this: the shell offers a route to About, **and** the shell has
no `.site-footer` in it.

### `DataRetention` is one component in two places

It renders on `/about` and in `/settings`. Written twice it would be wrong in
one of them within a release, because every claim it makes is enforced
somewhere else in the code — `GUEST_RETENTION_DAYS`, `claim_guest_games()`,
`delete_user()`, and which stores are in memory. If you change a retention
rule, that component is the one piece of prose that has to move with it.

The claim that matters most is the last one: **Learner Mode sessions,
Post-Mortem reviews, and the learning loop's corrections and practice are in
memory and do not survive a restart.** Everything else on that card is
reassuring; that one is the disappointment, and a person finding it out by
losing something is a worse way to learn it.

### `email_available` on `/api/auth/config` — why publishing it is safe

The UI needs to stop implying a recovery path the deployment does not have:
signup says so where the address is collected, and `/forgot-password` says so
**before** the form rather than after a round trip.

> ⚠️ **This does not weaken the uniformity of `/forgot-password`, and the
> distinction is the whole reason it is safe.** That endpoint must answer
> identically for a registered address, an unregistered one, a malformed one, a
> Google-only account and a dead provider, because every difference is a free
> way to check whether somebody has an account here. `email_available` is
> derived from **configuration**, before any address exists, so it is the same
> value for every caller and can carry nothing about a particular one. It is
> the same fact `/forgot-password` already returned in its own response, moved
> earlier.
>
> The check that would catch someone "improving" this into an address-keyed
> answer is in §18 of `test_accounts_postgres.py`: known and unknown addresses
> must still produce byte-identical responses with the field present.

### The build id

`/api/health` reports `version` from `BUILD_SHA`; the frontend footer reports
the same thing from `VERCEL_GIT_COMMIT_SHA`, baked in by `vite.config.ts`. Both
default to `dev`.

This exists because **the frontend and the backend deploy separately and skew
silently** (§2). The one time it happened, the symptom a user saw was *"Not
Found — try another file"* on a perfectly good PGN. Two build ids turn that
into a comparison anybody can make:

```bash
curl -s https://zugzwang-api.onrender.com/api/health   # "version": "..."
# and read the footer on https://chess-app-rho-swart.vercel.app/about
```

### One thing consolidated on the way past

`AuthShell` existed **twice** — once in `AuthPages.tsx` and once in
`PasswordReset.tsx`, identical. Adding the footer to one of them was how it was
found: the reset pages quietly lost it. `AuthPages.tsx` now exports both
`AuthShell` and `useAuthConfig`, and `PasswordReset.tsx` imports them.

### Also fixed, because the new sweep found it

`.auth-link` and `.auth-minor` are inline links inside sentences, so they were
sized by their line box — about 18px — and failed a 44px touch target on a
coarse pointer. They were like that before this work; the new `/signup` sweep
is what surfaced them. Under `pointer: coarse` only, they become inline-blocks
with vertical padding: the ink does not move, the hit area grows around it.


---

## 24. The Improvement Profile — many games, and what they keep showing

Scopes **B**, **C** and **D** of the longitudinal-learning milestone: bulk PGN
import with persistent storage, an asynchronous analysis pipeline, and the
first version of recurring-pattern detection. The design is in
`docs/superpowers/specs/2026-09-08-improvement-profile-design.md`.

The goal is not storing games. It is being able to say **"you consistently…"**
with evidence behind it, instead of "in game 7".

**Review is untouched.** Upload a PGN, get an immediate analysis: that flow is
exactly as it was. This is a second experience reached from a link at the
bottom of Review (`.pm-profile-cta`), and it is a **page at `/profile`, not a
fourth mode**. The three-mode shell is deliberate; this holds nothing a
navigation would destroy, because the library is on the account and the scan
runs on the server whether or not the page is open.

### The rule that must not be broken

> ⚠️ **Imported games are NOT in `games`/`moves`, and must never be.**
> `reweight_candidates()` reads `owner LIKE 'user:%'` on `games` to steer what
> the AI plays **against everybody**. Put imported PGNs there and anyone could
> poison the global move pool by importing a pile of grandmaster games,
> anonymously and repeatedly. `imported_games` and `game_findings` are
> deliberately outside every query that feeds the candidate pool, and
> `test_improvement_profile.py` asserts an import leaves `games` untouched.

### Why it asks for an account

The only surface in the app that does. Not a business rule: a profile is a
claim built from ten or more games over days, and a guest identity is a cookie
— someone who imported a library as a guest, waited out the scan and then
cleared their cookies would lose all of it with no warning that this was
possible. The 401 is rendered as an invitation, not an error.

### The pipeline, and the three constraints that shaped it

All three were measured, not assumed:

1. **One Stockfish process behind one lock.** A bulk scan that does not let go
   starves live play.
2. **0.16s per position at depth 12** locally — ~13s for a 40-move game, about
   3× that on a throttled instance. Fifteen games is ~10 minutes of engine.
3. **The free instance sleeps after ~15 minutes idle.**

So `profile_worker.py` does **one game at a time, yielding the engine between
every ply** (`PLY_PAUSE`), which means a live move queues behind at most one
position rather than behind somebody's whole library. The scan is slower in
wall-clock terms and that is the trade taken deliberately: this work is not
urgent and the game in front of somebody is.

> ⚠️ **`analyse_game_async` is not `analyse_game` in a thread, and must not
> become it.** One `asyncio.to_thread` around the whole game would hold the
> engine for the entire scan — exactly the starvation the pause exists to
> avoid. Each position is its own hop.

Constraint 3 is handled by `profile_service.requeue_stuck()`, called once at
boot in `app.py`: a row still marked `analysing` belongs to a process that no
longer exists, because there is one worker and it has just started. **That
single line is what makes the instance sleeping a non-event instead of a
permanently stuck job.**

### Detection — `pattern_detectors.py`, pure

Twelve themes. **The learning loop's eight are reused verbatim**, so a pattern
found across a library and a correction card offered after one move speak the
same language. Four are added: `FLANK_PAWN_COMMITTAL`, `ENDGAME_CONVERSION`,
`OPENING_UNCERTAINTY`, `TIME_PRESSURE`.

**One ply produces at most one finding.** A ply that tripped three detectors
would contribute three counts to three themes off a single mistake, and every
claim in the profile is a count.

> ⚠️ **Order the detectors by specificity, not by convenience — this was wrong
> once.** With `phase == "opening"` checked before the tactical classifier, a
> hung queen on move four was filed as `OPENING_UNCERTAINTY`. True, and
> useless: the thing to practise is seeing the reply, not learning a line. A
> phase says *where* a mistake happened and is only the best description of one
> when nothing better fits. The order is: a measured clock, a lost won endgame,
> a named tactical miss, a wing-pawn committal, the phase, then the honest
> catch-all.

**Two things are deliberately not detected.** *Discovered attacks* — naming
them needs a motif classifier this evidence does not support, so they count as
`TACTICAL_OVERLOOK`, which is true. *Time management without clocks* — most
PGNs have no `[%clk]`, and where it is absent `TIME_PRESSURE` is never claimed
rather than estimated from move numbers. A theme that cannot be detected
honestly is worse than a missing one, because a person will go and practise it.

### The threshold IS the product

Nothing is claimed below **10 analysed games**, and no theme is claimed from
fewer than **3 of them**. The gate is about games rather than findings on
purpose: one catastrophic game can supply a dozen findings of one theme, and a
dozen findings from one game is not a pattern, it is a bad afternoon.

Below the threshold the screen says how many more games it wants. An empty
findings list with no explanation is indistinguishable from a bug.

**There is no stored profile.** It is aggregated on read from `game_findings`,
because a cache would need invalidating every time a game finished, every time
one was deleted and every time the taxonomy changed — three chances to serve a
claim the evidence no longer supports.

**Wording is a template per theme, not generated.** *"You frequently push wing
pawns before the centre is settled"* must not drift from what the detector
actually counted, and a template is the only way to keep those two in step and
testable.

### Three regressions found by audit, and what closed each

All three were reproduced before being fixed, and each has a check that fails
on the code as it was.

**1. Deleting an account left the whole imported library behind.** `owner` is
the opaque identity string, not a foreign key to `users`, so no cascade reaches
`imported_games` — that is the price of the identity seam that lets a guest and
an account be the same kind of thing. `delete_user` deleted `games` and `users`
and nothing else, and an account "deleted" on request kept 52 imported games
and every finding in them.

> ⚠️ **`auth_service.delete_user` has to name every OWNER-KEYED table by hand.**
> There is no cascade to inherit. If you add a table keyed on `owner`, add its
> DELETE there in the same commit. `game_findings` is the exception and needs
> no line: it hangs off `imported_games.id` by a real foreign key.

**2. The same PGN could be imported repeatedly and inflate confidence.** This
is the one that mattered most, because every claim the profile makes is a
**count** — confidence is derived from how many distinct games carry a theme.
Importing one game five times turned it into five games' worth of evidence and
pushed a theme from low confidence to high without a single new move being
played. That is the feature lying, which is worse than the feature being empty.

Migration `007` adds a `fingerprint` over the **starting position and the move
sequence in UCI**, with a unique index on `(owner, fingerprint)`. Headers are
deliberately excluded: the same game exported from two sites differs in Event,
Site, Date and the spelling of both players' names, and none of that makes it a
different game. Duplicates are **skipped and reported**, never counted.

The insert is `ON CONFLICT DO NOTHING` rather than a select-then-insert,
because two requests arriving together would both see nothing and both insert.

> ⚠️ **A fixture for a uniqueness rule has to be provably unique.** Three test
> fixtures got this wrong in a row: games differing only in the Event header,
> then games differing by a count of "first legal quiet move" plies (which all
> converge on the same fivefold repetition), then a repeated `a3 a6` that is
> illegal after the first and got silently truncated into one game. Each time
> the test quietly measured deduplication instead of what it was for. Both
> suites now **assert their fixtures are distinct** before using them.

**3. A batch over the limit silently discarded games.** 51 games in gave
`200 OK, added: 50, skipped: 0` and the fifty-first simply ceased to exist.
The response now accounts for every game in the body —
`added + duplicates + skipped + ignored` always equals what was sent — and the
page says in words that some were left out and can be sent again.

### Growing into the correction loop

The seam is `game_findings`. A correction card is a finding plus a position to
practise; a retest is a finding whose later occurrences stop appearing. Both
read that table, and neither needs this design to change.

### Verified end to end

Twelve games imported through the real form on `:3001`, scanned by the worker,
producing three distinct claims — `OPENING_UNCERTAINTY`, `FLANK_PAWN_COMMITTAL`
and `CAPTURE_RECALCULATION` — each with evidence counts, confidence, trend and
the moves they came from.


---

## 25. The release — what shipped on 2026-09-08, and how it was verified

`11cfdcb` on `master`, pushed and auto-deployed to both platforms. Two
milestones in one release: the production surfaces (§23) and the improvement
profile (§24), on top of the account system and the release-gate fixes (§22).

### What a deploy of this needed

**No new environment variable. No new Python or npm dependency.** The only
deploy-affecting changes were migrations `006` and `007`, which run themselves
at boot, and four comment lines in `render.yaml`. `vercel.json`,
`requirements.txt`, `package.json` and both Dockerfiles were byte-identical to
what was already deployed.

That is worth knowing for the next release: **check it rather than assume it.**
The command is one `git diff`:

```bash
git diff --stat origin/master..master -- render.yaml chess-frontend/vercel.json \
    requirements.txt chess-frontend/package.json Dockerfile Dockerfile.backend migrations/
```

### The environment as it actually stands on Render

Fifteen variables on `zugzwang-api` (frankfurt — the other Render services are
not in the deploy path). All were already set except the one below.

Required: `GEMINI_API_KEY`, `DATABASE_URL`, `SESSION_COOKIE_SECRET` (stable —
changing it invalidates every guest cookie), `ALLOWED_ORIGINS`, `FRONTEND_URL`,
`ACCOUNTS_ENABLED=true`, `COOKIE_SECURE=true`, `GUEST_RETENTION_DAYS=30`,
`DISABLE_LANGFLOW=true`, `STOCKFISH_DEPTH=12`, `STOCKFISH_RANK_DEPTH=8`, and
the four `MAILJET_*`.

**Vercel needs ZERO environment variables.** `grep import.meta.env` across
`chess-frontend/src` returns nothing; the API is same-origin through the
rewrite. Build settings unchanged: root `chess-frontend`, framework `vite`,
production branch `master`.

> ⚠️ **`DATABASE_SCHEMA` must never be set on Render.** Production uses the
> default `public`. Setting it points accounts at a side schema where they
> silently disappear — the failure mode §13 documents, where an account was
> created, answered a login, and then could not be found.

> ⚠️ **`ACCOUNTS_ENABLED` is read at CALL TIME, not at boot.** During this
> release it went from `true` to `false` mid-verification while the environment
> was being edited, and sign-up, sign-in and the whole profile answered 503 on a
> healthy-looking server. The value comparison is `== "true"`, so `True`, `1`,
> `yes` and an empty value all read as OFF. If accounts are refusing on a build
> you know has them, check the spelling of that value before reading any code.

### Verified against the live deployment

Not against a container — against `zugzwang-api.onrender.com` and the Vercel
frontend, after the push:

| | |
|---|---|
| Build landed | `version` = the merge commit on the backend; the JS bundle carries `Improvement profile` and the About copy |
| Migrations | `database: "ok"`, and the first import returned `game_ids: [1]` — the first row in `imported_games` on the real database |
| Accounts | sign up → settings PUT/GET round-trip → account deletion → sign-in afterwards refused with 401 |
| Profile | import `added: 1`; the same PGN again `added: 0, duplicates: 1`; `ready: false, games_needed: 10` |
| Guest boundary | `/api/profile` as a guest → 401 with the invitation wording |
| Hardening | `GET /api/reset` → 405, `/docs` → 404, `/forgot-password` uniform for an unknown address |

> ⚠️ **A 200 from a Vercel path proves nothing about the build.** The SPA
> catch-all rewrite serves `index.html` for every path, so `/about` and
> `/profile` returned 200 on the OLD bundle too. The real test is a string that
> only exists in the new code — grep the bundle:
>
> ```bash
> FE=https://chess-app-rho-swart.vercel.app
> JS=$(curl -s $FE/ | grep -oE 'assets/index-[A-Za-z0-9_-]+\.js' | head -1)
> curl -s "$FE/$JS" | grep -c "Improvement profile"
> ```
>
> On the backend the equivalent tell is a KEY rather than a value:
> `email_available` in `/api/auth/config` exists only from §23 onward.

### Size of the thing, measured

Re-measured 2026-09-08, after the closed beta. The method is stated for each
row so the next count is comparable rather than merely newer — the previous
version of this table gave a total that did not equal the sum of its own two
components, which is what happens when the method is left implicit.

| | | method |
|---|---|---|
| Backend application Python | **16,100** lines, 39 files | root `*.py`, excluding `test_*.py` |
| Frontend TS/TSX + CSS | **17,828** lines, 65 files | `chess-frontend/src/**` — `.ts`, `.tsx`, `.css` |
| Schema | **548** lines, 8 files | `migrations/*.sql` |
| **Shipped application code** | **34,476 lines** | the three rows above, which is what the two images actually contain |
| Backend tests | **6,944** lines, 16 files | `test_*.py` |
| Browser invariants | **1,804** lines, 6 files | `tools/verify/*.mjs` |
| Operator tooling | **260** lines, 1 file | `tools/*.py` — does NOT ship; see below |
| Whole tracked repo | **58,776** lines, 186 files | `git ls-files` |
| Documentation | **5,760** lines | CLAUDE.md 4,436 · DEPLOY.md 624 · OBSIDIAN_DESIGN.md 333 · README.md 251 · AGENTS.md 116 |
| Vercel bundle | 564 KB JS + 90 KB CSS raw, **~191 KB gzipped** to a visitor | `vite build`, 99 modules |
| Render image | **457 MB** | unchanged — no new dependency; not rebuilt for this count |

Growth over the previous count, on the two rows that count also had
(backend Python + frontend, so it is like-for-like): **31,759 → 33,928, up
2,169 lines**, and **+9 KB gzipped** to a visitor. Essentially all of it is the
closed beta. The schema row is new here rather than new in the codebase — the
old table simply never counted `migrations/`, which is why its total did not
reconcile.

| the closed beta, itemised | lines |
|---|---|
| `beta_service.py` | 795 |
| `beta_gate.py` · `beta_api.py` | 169 · 99 |
| `migrations/008_beta_access.sql` | 129 |
| `tools/beta_codes.py` (operator CLI) | 260 |
| `test_beta_access.py` | 759 |
| `tools/verify/beta.mjs` | 290 |
| Frontend: gate, landing, four pages, CSS, client | 95 · 195 · 287 · 329 · 56 |
| **Total** | **3,463** |

Two things that count is honest about:

- **A little over half of it is not the feature.** 1,049 lines are the two test
  suites and 260 more are the admin CLI. The gate itself — the part that
  decides whether a request is served — is `beta_gate.py`, and it is **169
  lines**, most of them explaining why it is a middleware.
- **`tools/` does not ship.** `Dockerfile.backend` is `COPY *.py ./`, which is
  root only, so `tools/beta_codes.py` exists on the operator's machine and
  nowhere else. That is the intended shape: there is no admin surface in the
  deployment to find.

**`Dockerfile.backend` ships the test suite.** `COPY *.py ./` pulls in all 16
`test_*.py` files — 6,944 lines of them now. Harmless (they never run and hold
no secret) but it is dead weight in a production image, and a one-line change
would exclude it. Left alone deliberately; noted so the next person does not
have to rediscover it.

### Still open after this release

None of it blocking, all of it decided:

- **`EMAIL_ENABLED=false` is not yet set** — the one live item. See §0.
- Email addresses are unverified.
- No hard Gemini spend cap; `rate_limit.py` caps per IP, which does nothing
  against a distributed caller.
- `LANGFLOW_AUTO_LOGIN=true` is still in `docker-compose.yml`, behind a profile
  nothing starts and not deployed to Render at all.
- One instance only — live game state, sandbox sessions and Post-Mortem reviews
  are in memory. Do not scale horizontally.
- ~~Production cookies are `SameSite=None`.~~ **Done in §28** - they are
  `Lax` now, and `csrf.py` is a second lock behind that. This entry used to say
  `Lax` "would be tighter and is a small change for a quiet moment"; the
  hardening pass found it was not merely tighter, it was closing a reachable
  CSRF against every body-less POST route.
- The beta adds short privacy, terms, contact and request-access pages (§26).
  `VITE_CONTACT_EMAIL` still has to be set on Vercel before deployment.

---

## 26. The closed beta — how the door works, and why it is not in React

**Zugzwang is private.** Every `/api` route except a named handful answers
**403** to a caller who has not redeemed an invitation, and the browser draws a
landing page instead of the board. This is a server-side authorization system,
not a frontend gate, and the distinction is the whole feature: a person who
opens DevTools, edits React state, rewrites `localStorage`, disables JavaScript
or curls the API directly gets exactly the same 403 as everybody else.

### The five files

| file | role |
|---|---|
| `migrations/008_beta_access.sql` | `beta_codes`, `beta_redemptions`, and `users.beta_access` |
| `beta_service.py` | codes, hashing, redemption, grants, the admin operations |
| `beta_gate.py` | **the middleware that refuses** — deny by default |
| `beta_api.py` | `/api/beta/status` and `/api/beta/redeem`, the gate's own open routes |
| `tools/beta_codes.py` | the admin CLI. **There is no admin endpoint** |

Frontend: `components/BetaGate.tsx` decides what is drawn, `pages/BetaLanding.tsx`
is the door, `pages/BetaPages.tsx` is what its footer links to, and
`services/betaService.ts` is the client. None of them authorizes anything.

### The one thing to understand: it is deny by default

`beta_gate.py` is a **middleware**, not a `Depends()` on each route, and that
was a deliberate choice against the more obvious one. There are sixty-odd API
routes across six routers. The failure mode of a per-route dependency is not
that it refuses wrongly — it is that somebody adds route sixty-one and forgets
it. That route is then open, nothing fails, no test covers it, and the hole is
found by whoever was looking for one.

So everything under `/api/` is closed and `beta_gate.OPEN_PATHS` is the short
list of exceptions. **A route added next month is protected on the day it is
written, without its author doing anything.** Opening one is an explicit edit
to a list a reviewer has to look straight at.

`test_beta_access.py` asserts this by **enumerating `app.app.routes`** and
insisting every `/api` path is either closed or on the named list. That test is
the reason the property survives; do not weaken it into a list of remembered
paths.

### Middleware order, which is load-bearing

Starlette runs the **last-added middleware outermost**, so `app.py` adds the
gate **before** CORS and Identity:

```
IdentityMiddleware  →  CORSMiddleware  →  BetaGateMiddleware  →  routes
```

Both neighbours matter, and getting either wrong fails quietly:

- **Identity must be outside**, or `request.state.identity` is not set and the
  gate has nothing to authorize.
- **CORS must be outside**, or a 403 reaches a cross-origin caller with no CORS
  headers, which a browser reports as a network error with no status — the same
  class of silent confusion `ALLOWED_ORIGINS` exists to prevent. Preflight
  `OPTIONS` is answered by CORS before it ever reaches the gate.

### The codes

`ZG-BETA-XXXX-XXXX`, eight secret characters over Crockford's 32-character
base32 alphabet — no `I`, `L`, `O` or `U`, because these are read off one screen
and typed into another. That is **40 bits**, which is hopeless to guess against
a rate limiter and *not* hopeless against an offline sweep of a leaked table.

So the stored hash is **HMAC-SHA256 under a pepper**, not a bare SHA-256.
`BETA_CODE_PEPPER`, falling back to `SESSION_COOKIE_SECRET`. With neither set,
`beta_service` **raises** rather than using a random per-process key — a random
key mints codes in the admin tool that the server then rejects, which presents
as "every code is wrong" and is a miserable thing to debug.

> ⚠️ **Rotating the pepper invalidates every outstanding code, irrecoverably.**
> They cannot be re-hashed: the server does not have them. Treat it exactly like
> `SESSION_COOKIE_SECRET` — set once, never change.

> ⚠️ **`BETA_CODE_PEPPER` is now set EXPLICITLY, in the local `.env` and on
> Render, and the fallback to `SESSION_COOKIE_SECRET` must not be relied on
> again.** It was, once, and it cost a deploy: the codes were minted locally
> under the local `SESSION_COOKIE_SECRET`, Render's copy of that value is a
> *different* string, and so every code — the fifty and the owner key — was
> refused in production with the ordinary "not valid" message. A valid code and
> deliberate nonsense produced byte-identical answers, which is correct
> behaviour and gives an operator nothing to go on.
>
> Two lessons worth keeping:
>
> * **The two secrets were never guaranteed to match**, and assuming they did
>   is what made the fallback dangerous rather than convenient. They are also
>   different *concerns* — one signs cookies, one keys invitations — so leaving
>   them coupled meant a future cookie-secret rotation would silently destroy
>   every outstanding invitation.
> * **You can check it without moving a secret.** The deployment mints guest
>   cookies as `<id>.<hmac>` under its own `SESSION_COOKIE_SECRET`, so asking
>   the live API for one and running it through the local
>   `identity.verify_guest_cookie()` answers "are these the same value?" with a
>   yes or a no and nothing in between:
>
>   ```bash
>   curl -si https://zugzwang-api.onrender.com/api/auth/me | grep -i 'set-cookie: zw_guest'
>   # then verify that value locally with identity.verify_guest_cookie()
>   ```
>
> The recovery, if the pepper ever has to change again: the code *strings* can
> be re-stored under a new pepper with `beta_service.create_custom_code()` as
> long as you still hold them (the `--out` file), which is what was done here —
> the fifty codes and the owner key kept their spelling and only their stored
> hashes changed, so nothing had to be redistributed. Without that file, they
> are gone.

The code exists in exactly two places: the output of `tools/beta_codes.py
generate`, and the invitation you sent. The database holds a keyed hash and a
four-character label (`ZG-BETA-7K4M-????`) that identifies a code in a listing
without redeeming anything.

### Redemption, and why a guest may hold a grant

A redemption is bound to **an identity string** — the same opaque
`guest:8f2a…` / `user:42` that `games.owner` holds (§13). That is what lets the
landing page work before an account exists: redeeming grants access to the
guest identity, and the visitor plays immediately.

```
landing page → code → POST /api/beta/redeem
                          ↓  claim + grant, one transaction
                     guest plays
                          ↓  signup / sign-in / Google callback
              transfer_to_account() rewrites identity → user:<id>
                     users.beta_access = true
                          ↓
              access now survives sign-out, a new browser, a cleared cookie
```

`_carry_beta_access()` in `auth_api.py` sits beside `_claim_guest_history()`
and runs **before** `_end_guest_identity()` — retiring the guest first would
strand the grant on an identity nothing can reach.

**Signing out locks the browser out again, and that is correct.** Logout issues
a brand-new guest identity (§13), which has redeemed nothing. The access belongs
to the account; signing back in restores it with no code. The landing page
therefore offers **Sign in**, and `/signin` is deliberately reachable while
locked out, or a returning tester on a new laptop could never get in.

`/signup` is deliberately **not** reachable while locked out. Redeeming comes
first, so there is no such thing as an account with no access to explain. The
middleware guards password signup itself; the open Google callback admits a
known account but refuses to create one for an unknown subject unless the
current guest already has access.

### What is asserted, and where

`test_beta_access.py` — 123 checks, ten sections. The claims worth knowing:

1. Guarded routes refuse; the refused request **produces no game state**.
2. Deny by default, **enumerated from the real route table**.
3. No header, body field, query parameter, forged guest cookie or invented
   session token grants access.
4. A code is spent once — including **eight threads racing one code**, of which
   exactly one wins.
5. Disabled, expired, used-up, unknown and malformed codes answer **identically**.
6. Access follows the account across sign-out and onto a fresh browser.
7. Both rate-limit buckets refuse a guesser.
8. A dump of `beta_codes` contains no working invitation, and the hash is not a
   bare SHA-256.
9. **Fail closed**: unset means gated, only the literal `false` opens it, and an
   unset pepper raises.

Every other suite that drives API requests sets `BETA_ACCESS_REQUIRED=false`
at the top. They test what the routes do, not who may reach them — and section
9 above is what proves that opt-out is not the default.

### Refusals say nothing

One message for every bad code — missing, disabled, expired, used up, malformed.
The difference between them is the only thing a guesser is missing, and the
operator can see the real reason in `tools/beta_codes.py list`. **Do not
"improve" this into a helpful "your code expired"**; it is the same trap
`/forgot-password` documents in §13, wearing different clothes.

Two rate-limit buckets, for two attacks: per IP (`limit_beta_redeem`, 10/15min)
bounds one machine, and per identity (`redeem_by_identity`, 20/15min) is the one
a distributed guesser cannot escape, because the grant is written onto an
identity and cycling identities abandons every guess already spent.

### It fails closed, and that is a departure

`has_access()` refuses when the database is missing or unreachable. Everywhere
else in this app a storage outage costs a feature and play carries on (§13,
"no database is not fatal"). Here it would cost the entire access control. An
outage that locks testers out for a minute is recoverable; one that opens the
app to the public is not.

`BETA_ACCESS_REQUIRED` defaults to **on**. Only the literal string `false`
opens the app. Both states are logged at WARNING on every boot, and
`/api/health` reports `beta_required` so an operator can confirm from outside
that a deploy did not ship with the door open.

### Administration

No HTTP route mints, lists, disables or expires anything, and none should be
added. An admin endpoint is a thing with a URL; the operator has a shell.

```bash
cd /mnt/c/Users/David/Documents/chess-app-v3.9
set -a; . ./.env; set +a
/tmp/chessapp/bin/python tools/beta_codes.py generate 50 --by david --out ~/codes.txt
/tmp/chessapp/bin/python tools/beta_codes.py list
/tmp/chessapp/bin/python tools/beta_codes.py usage
/tmp/chessapp/bin/python tools/beta_codes.py disable ZG-BETA-7K4M-2QX9
/tmp/chessapp/bin/python tools/beta_codes.py expire 12 --days 0
/tmp/chessapp/bin/python tools/beta_codes.py revoke user:42
```

`disable` stops a code being redeemed again and leaves existing testers alone;
`revoke` takes access from somebody who already redeemed and **does not** return
the code to the pool. Those are two different intentions and conflating them
would mean tidying up spent codes silently locked out the people holding them.

### Verifying it, and the four things verification found

`tools/verify/beta.mjs` is the browser half, and it **spends the code you give
it** - mint one for the purpose, never one meant for a person:

```bash
set -a; . ./.env; set +a
/tmp/chessapp/bin/python tools/beta_codes.py generate 1 --notes verification
CODE=ZG-BETA-XXXX-XXXX node tools/verify/beta.mjs      # 49 checks
```

It performs the bypasses rather than arguing about them: it writes
`localStorage`, `sessionStorage` and invented cookies, sends a hand-written
`fetch()` from the page's own context, and then **makes the status endpoint lie
to the page** so the app renders in full - and shows every guarded route
answering 403 to it anyway.

> ⚠️ **Every other `tools/verify/*.mjs` needs the gate switched off**, or it
> gets the landing page instead of the app. Same reasoning as the Python
> suites, same escape hatch:
>
> ```bash
> setsid nohup env BETA_ACCESS_REQUIRED=false /tmp/run_backend.sh \
>   > /tmp/backend.log 2>&1 < /dev/null & disown
> ```
>
> `ui.mjs` (118/118), `interaction.mjs` (127/127) and `boardstate.mjs` (22/22)
> all pass that way, unchanged by this work. Note that both browser tools wait
> on `networkidle`, and a Vite that has just restarted serves ~75 modules with
> no 500ms gap between them - so a run started within a minute of a restart
> times out on the first navigation and looks like a failure it is not.

Four real defects were found by verifying rather than by reading, and all four
are fixed:

1. **The code field silently corrupted a correctly typed code.** It held the
   whole code and re-inserted the `ZG-BETA-` prefix on every keystroke, so it
   re-parsed its own output: typed one character at a time,
   `ZG-BETA-DZPM-RYF7` became `ZG-BETA-ZGBE-TADZ` and was refused. The prefix
   is now a fixed adornment beside the box and only the eight characters that
   carry entropy are editable; pasting the whole printed code still works.
   **It only ever failed for someone typing rather than pasting**, which is
   why no server-side test could have caught it. `beta.mjs` now covers typed,
   pasted-whole and pasted-secret-only.
2. **The landing page rendered a black well with unreadable text in light
   mode.** It was written against `--cp-surface-1`, `--cp-text-1`, `--cp-line`
   and friends - **which are declared nowhere in this codebase.** obsidian.css
   defines `--cp-bg`, `--cp-surface`, `--cp-text`, `--cp-border` and the emboss
   shadows, never the numbered variants, so every one of them resolves to
   nothing. `beta.css` now uses the base tokens (`--surface`,
   `--surface-sunken`, `--text-primary`, `--border`, `--ai`, `--danger`),
   which have real values in both themes.

   > ⚠️ **`AccountMenu.css` and `account.css` still reference the dead names**,
   > and every sign-in, sign-up and settings page is built on them. It degrades
   > quietly there rather than visibly, which is exactly why nobody has noticed.
   > Not fixed here - it is a separate change to a separate surface - but it is
   > real, and it is the reason this file does not "stay consistent" with them.
3. **The gate blocked the event loop.** `has_access()` borrows a pooled
   connection and waits on a round trip to Neon, and it was being called
   directly from an async `dispatch` that runs on **every** request - so one
   cold visitor's latency would have been paid by every other request in
   flight. `beta_service.cached_access()` now answers from memory
   synchronously, and only a miss goes to `run_in_threadpool`.
4. **`/api/auth/signup` was open and should not have been.** Redeeming comes
   first, so an account created before an invitation would be an account with
   no access to explain. It is gated; a redeemed guest signing up already has
   access, so the real flow is unaffected.

### A chosen key, and the one character the alphabet gained

`tools/beta_codes.py add ZG-BETA-XXXX-XXXX` stores a code somebody picked
rather than one that was drawn - for the operator's own key, which has to be
memorable, must not expire, and must admit the same person on every device they
ever sit down at. `generate` cannot do that job: it returns randomness, and
randomness is the one thing a memorable key is not.

```bash
set -a; . ./.env; set +a
/tmp/chessapp/bin/python tools/beta_codes.py add ZG-BETA-XXXX-XXXX \
    --by david --max-uses 100000 --notes "owner key"
```

**The owner key is deliberately not written down in this file.** It is a
credential, this file is in git, and a repository being private is not the same
as a secret being kept. It lives in the database as a keyed hash like every
other code; if it is ever lost, `disable` it and `add` another.

Two things about it are worth knowing:

- **`INPUT_ALPHABET = ALPHABET + "U"`.** Crockford leaves `U` out so that a
  random draw cannot spell something unfortunate, which is a rule about
  *generation*. `canonical()` now validates against the wider set so a
  hand-chosen key may contain one. No generated code ever will, so nothing
  becomes ambiguous, and the 40-bit claim above is about `ALPHABET` and is
  untouched.
- **A chosen code carries only the entropy its author gave it**, which is far
  less than eight uniform draws. That is a real reduction, taken knowingly. The
  rate limiter still bounds an online guesser hard, and the recovery if one
  leaks is the same as for any code: `disable`, then `revoke` whoever redeemed
  it. Do not hand chosen codes to testers - `generate` exists for that.

`test_beta_access.py` section 9b covers the whole path: `U` accepted but never
generated, multi-use admitting three devices and refusing the fourth, the key
stored hashed and absent from a table dump, a one-character variant refused
with the standard message, and duplicate or malformed keys refused at storage
time. That section also records a case worth remembering - `ZG-BETA-TOO-SHORT`
is **not** malformed, because the confusable mapping runs before the length
check and turns it into `T00SH0RT`, eight perfectly valid characters.

### Deploying this

**No new environment variable is required** and no new dependency. The pepper
falls back to `SESSION_COOKIE_SECRET`, which Render already sets and already has
to keep stable; `BETA_ACCESS_REQUIRED` is declared `true` in `render.yaml` but
that only restates the code default. Migration 008 runs itself on boot.

Two things to do in this order, because pushing to `master` **is** the deploy:

1. Generate the codes **before** the push if they are to be handed out on day
   one — the local `.env` points at the same Neon database Render reads, so the
   rows are already there when the deploy lands.
2. Set `VITE_CONTACT_EMAIL` on Vercel, or the landing page's Contact and Request
   pages say — honestly — that this deployment has no contact address.

---

## 27. Beta hardening, sprint 1 — what the first tester found

The first real beta tester's feedback, worked through in priority order. The
brief's own ordering is preserved below because it was the right one: **trust
beats feature count**, and the most dangerous item on the list was the one that
sounded smallest.

### The report, and what each item turned out to be

| they said | it was |
|---|---|
| "sometimes I played the best move and it called it bad" | **Three separate causes**, below. Two were real and one was a measurement artefact. |
| pawn promotion only offers a queen | True in all three modes. Now a picker. |
| in PGN review I can't tell who was White | True - the heading named the pairing, nothing named the sides. |
| I want to see the game from Black's side | Review had no rotation at all; Learn had the state and no control. |
| the board is too small, let me change it | One preference, all three modes. |
| part of the AI level selector is hidden | Reproduced and measured: 45px outside its column at 1280x720. |
| natural-language setup fails on middlegames | Structural, not a bug. It now refuses honestly. |
| the AI takes too long | ~1s of engine time, and about as much again of UI that had not asked yet. |

### The move-feedback audit, which is the part worth reading

Three things were wrong, and they are worth separating because only one of them
was a wrong grade.

**1. Play's mid-game chat could invent a verdict, and nothing could stop it.**
This is the important one. `/api/chat` handed Gemini a FEN, a SAN history and a
difficulty number - and nothing else. Asked *"was that a good move?"* the model
had no choice but to answer from its own opinion, about a half-move this app had
already graded with Stockfish and drawn a badge for. Learner Mode's coach and
Post-Mortem's coach were both given the engine's ranking from the start; Play,
the one place a player is most likely to ask, was not.

It now receives the engine's ranked alternatives, its principal variation, and
the grade of the last half-move rendered by `engine_evidence.grade_sentence` -
and, in the same breath as the evidence rather than elsewhere in the prompt, the
rule: **HOW GOOD A MOVE WAS IS NOT YOURS TO DECIDE.** It may explain a grade it
has been given; it may not mint one, and where it has not been given one it says
so instead of supplying its own.

> The general form of this is already written down in §8.6 and it was not
> applied here: **when Stockfish already knows something, give it to the model
> instead of asking the model to work it out.** That section is about mating
> lines. This was the same mistake about grades.

**2. The badge did not say whose move it graded.** It shows the most recent
half-move, which is right and is what chess.com does. But the AI answers in
about a second, so the badge a player sees a moment after moving is usually the
grade of the AI's *reply*, sitting on the AI's square. A red `??` appearing just
then reads as a verdict on the move you just made. There is room for one glyph
on a square, so the answer is a disc in the mover's colour in the badge's corner
- the same white/black disc the player strips already use - plus a tooltip that
names the side. **No grade had to be wrong for the tester's report to be true.**

**3. A horizon mismatch, measured and then mostly exonerated.** `classify_move`
took `best_cp` from a multipv search of the position and `played_cp` from a
search of the position *after* the move at the same nominal depth - which is one
ply deeper in the tree. Subtracting two evaluations from different horizons
measures the horizon as well as the move. Measured over 48 candidate moves in
twelve positions: the two views differed by up to 46cp, disagreed on the grade
in 10% of cases, three quarters of those in the harsher direction, and exactly
one crossed a real move from "good" into "inaccuracy". Fixed by scoring the
played move from the SAME root with `root_moves=[move]`, which costs the same
one extra search it replaced.

It is written down as "mostly exonerated" on purpose: it was a real defect and
it was not big enough to explain the report. **Finding one cause is not finding
the cause.**

### `NOISE_MARGIN`, and why criticism now has to be earned

The most useful measurement in this sprint had nothing to do with the code.
Asking the shared Stockfish the same question at the same depth three times
running moves the answer by a median of 5cp, a p90 of 18cp and a maximum of
25cp - it keeps its hash between searches (§6), so near-equal moves reorder and
rescore between runs. Over 25 candidate moves in six positions, **four changed
grade on repetition alone.**

So `grade_from_scores` holds every critical label - inaccuracy, mistake, blunder
- to clearing its threshold by `NOISE_MARGIN` (25cp, that measured maximum). A
loss inside the band takes the gentler label next door and comes back with
`confidence: "low"` and a `note` saying why. The margin only ever softens; a
test asserts that.

Three fields now travel with every grade: `confidence`, `note`, and
`grade_source` (`stockfish` / `book` / `rules`, and never a model). `depth`
travels too, and a search below depth 10 caps confidence however clear the
numbers look.

> **Do not raise `NOISE_MARGIN` to make grades look more decisive, and do not
> lower it to make them look sharper.** It is a measurement. If it needs to
> change, re-run the repeatability probe and change it to what that says.

### `move_feedback_log` — so the next report is answerable

The reason the first report cost a full audit is that nothing was written down.
Every graded half-move now logs one line under the `move_feedback` logger, in
one field order, from every mode: mode, side to move, player colour, move, SAN,
grade, cpl, engine best, depth, confidence, grade source, **explanation source**
(`engine` or `gemini:wording`, and those are the only legal values), the eval
perspective stated rather than assumed, both evals, and both FENs. It logs at
INFO because the whole point is that it is already there when a report arrives.

### The rest, briefly

- **Promotion** — `PromotionPicker.tsx`, one component on all three boards,
  drawn on the promotion square inside the board frame. Not the library's own
  dialog: that one opens on a drag and not on a click, and §19 has already
  decided those are one interaction. `autoPromoteToQueen` stays on for the
  reason trap 13 now gives.
- **Review's seats** — the two names above and below the board, near seat at the
  bottom, following the rotation. Everything in them is read from the PGN's own
  headers; a missing header falls back to "White"/"Black". The result is
  rendered per player (`won` / `lost` / `draw`) because "1-0" only tells you who
  won if you already know who was White, which is precisely what the tester did
  not know.
- **Rotation** — Review and Learn. Learn already had the `orientation` state and
  had simply never grown a control for it.
- **Board size** — `hooks/useBoardScale.tsx`. One preference for all three
  modes, and `localStorage` rather than the account, because the right board
  size is a fact about the screen in front of you and not about you. It shipped
  broken and was fixed after the tester tried it; the whole story is in
  "Board size, and the two ways it did not work" below.
- **The meta row** — the reported clipping was real and worse than reported: at
  1280x720 the difficulty select sat **45px past the row's right edge**, over
  the gutter and under the coaching panel. The row is `nowrap` with a container
  query that shortens the labels when the column is tight and allows a second
  line only below 470px, where one line is arithmetically impossible. Wrapping
  everywhere was tried first and rejected **with numbers**: it cost 33px of
  board at 1440x900 to save nothing, which is backwards in a sprint whose other
  item is "the board is too small".
- **Learn's honest failure** — `MAX_CONSTRUCTED_PIECES`. Positions are built by
  placing material at RANDOM until it lands somewhere legal, which is fine for
  an endgame and cannot reproduce a middlegame's structure. Past sixteen
  non-king pieces it now refuses in a sentence that offers the two things that
  do work. And `build_from_pasted` makes the first of those true: a pasted FEN
  or PGN is parsed and validated by python-chess with no model involved.
- **Perceived responsiveness** — no context was removed from any API call, and
  that was the constraint. What was removed is UI that had not asked yet: a
  pre-flight `/api/status` on every move (a full round trip before the piece
  moved at all), and a flat 1000ms AI poll that added a uniformly distributed
  0-1000ms of pure waiting on top of the engine's own time. The player's move is
  now applied locally first and reconciled with the server after - the server
  remains the authority and any refusal reloads the board from it. The player's
  own move paints at ~150ms with "Thinking..." in the same frame as the piece.
  (The AI-reply figure first recorded here was measured wrongly - see "Cutting
  the AI's thinking time" below for the real number and what was done about it.)

> ⚠️ **The one thing not to undo here.** The tester's "too long" is about a
> feeling, and the cheap way to fix it is to send the model less. Don't. The
> game history in the prompt is what makes the coaching about *this* game
> instead of about a FEN, and a coach that has been handed only the current
> position invents the history it was not given. Every change in this sprint
> is on the UI side of the wait.

### The follow-ups, done in the same pass

Five items were listed as next-sprint work and then picked up immediately. Two
of them are worth reading because the answer was "measure it first".

**"Great" in Review, and why it is still off.** The scan cannot award the
`Great` grade because that is a claim about the runner-up and the scan does one
single-PV search per position. The obvious fix is `multipv=2`. **Timed over the
34 positions of the Opera Game at depth 12: 2.13s plain against 3.83s, i.e.
1.80x, +80%.** Asking for two principal variations kills the alpha-beta pruning
that makes the first one cheap - the same effect §7's preserved benchmark
records for MultiPV over all legal moves, just smaller. Eighty percent more of
the heaviest thing this backend does, on a free instance, for one cosmetic
label is not a trade worth making by default. So it is `POSTMORTEM_SCAN_MULTIPV`
(default 1), `PositionView` carries the runner-up when it is measured, and
`grades_unavailable` is now CONDITIONAL - claiming Great is missing when it is
not would be the same untruth in the other direction. `test_postmortem_api.py`
asserts against the setting rather than the constant, which is what it should
have done in the first place: pinning `["great"]` made a correct build fail the
moment the flag was turned on.

**`RATE_LIMITS_ENABLED`, and the shape of it.** `dependency.limiter` is a seam
for the TestClient suites, and it does nothing for a tool driving a real browser
against a separate uvicorn - the QA sweep took four 429s from the PGN import
bucket purely for working quickly. The flag copies `BETA_ACCESS_REQUIRED`'s
shape deliberately: **only the literal "false" opens it** (unset, empty, "no",
"1" and a typo all leave limits ON) and turning it off logs a warning at import,
so a deployment cannot reach that state quietly. It is set in `/tmp/run_backend.sh`
and **nowhere in `render.yaml` or either Dockerfile** - checked. Ten cases of it
are in `test_move_feedback.py` §8; the thing being guarded against is not an
attacker but a future session copying a dev command into a deploy config.

- **Figurine PGNs in Learn** now work. `_defigurine` moved to `pgn_text.py` and
  both importers use it; `postmortem_state` re-exports the old names so its
  tests and call sites are untouched. Learn's paste detector de-figurines
  before looking for move numbers, so `1. ♘f3 d5 2. d4 ♘f6` is recognised as a
  PGN instead of being handed to Gemini as a description.
- **`describe_material()`** puts one sentence about a constructed position on
  screen that is read off the finished board - "White has a queen and two
  pawns; Black has a rook. White to move." It does NOT replace the model's
  description (that is the design change §16 calls for), it stands beside it,
  so there is always one line that is true by construction next to one that is
  only hoped to be.
- **Play's Review panel says how confident the grades are.** The per-move
  grades already hedged themselves and the totals did not, which made a column
  of counts read as firmer evidence than the moves it was counting. It now
  names the grading depth and counts the close calls.

### Board size, and the two ways it did not work

Shipped in the first pass, reported broken by the user, and worth writing down
because both failures are the same shape: **a number was being changed that
nothing downstream was reading.**

**"Large in Review does nothing."** Correct, and measured: at 1440x900 auto,
small, medium and large all produced a 369px board. The control was a
MULTIPLIER on `useBoardSize`'s width ambition, and that ambition is immediately
capped at `height - chrome`. Review passes the largest chrome figure of the
three modes, so the height cap binds at nearly every viewport and the scaled
number was discarded before it reached the board. Even where it survived, the
fitter existed to remove ALL page overflow, so it undid any growth it had not
chosen itself within a frame.

The fix is a pixel DELTA applied to what the fitter actually converges on:

- **Growing** goes through a new `allowance` on `useFittedBoardSize` - the page
  is permitted to scroll by 110px and the fitter stops trimming there. On a
  layout with no slack left, a scrollbar is the only currency a bigger board
  can be bought with, and someone choosing "Large" has decided it is worth it.
- **Shrinking cannot use the same route**, and finding that out cost a third
  iteration. `scrollHeight - clientHeight` is **clamped at zero**: a page that
  fits reports no overflow, never negative slack. Asking the fitter to converge
  on -90px is asking for a number it can never measure, and it duly shrank the
  board to its 240px floor looking for one - "Small" produced a 264px board
  where 549px fits. So a shrink is subtracted from the fitted result instead,
  which is safe for precisely the reason the negative allowance was not: the
  fitter cannot see the slack that creates, so it has nothing to grow back into.

All four steps now live in one hook, `useBoardSizing(columnRef, chrome)`, for a
plain reason: the two halves are asymmetric, and that is exactly the sort of
detail that gets copied correctly into two call sites and wrongly into the
third.

**"Taking over in Learn minimises the board."** Also correct. The hint that
appears when you take the board over was rendered conditionally, so pressing
the button added a row to the column - and the fitter answered by shrinking the
board. Measured at 1440x900: **17px of new chrome cost 41px of board.** That
amplification is the whole subject of §11's warning (a 19px strip once cost
70px of board the same way), and the rule it states is the fix: the hint is
always in the layout now, reserved at two lines' height so neither its arrival
nor its wrapping moves anything.

> Ten invariants in `ui.mjs` cover both, per mode. Per MODE matters: Play's
> size control worked the whole time and Review's never did, so a check that
> only drove one of them would have passed against a build the user could see
> was broken.

### Cutting the AI's thinking time without cutting its context

The brief was explicit: make the move faster, do not send the model less. Both
halves matter - the game history in the prompt is what makes the coaching about
*this* game rather than about a FEN - so everything here is either connection
management or scheduling. **Not one byte was removed from a request, a response,
or the processing of either.**

#### Measure first, and be ready to be wrong

The first hypothesis was engine contention: every search in the app queues
behind one Stockfish, so the AI's ranking must be waiting on the grade for the
move the player just made. **It was not.** Stage timing (`⏱ ai_move` in the
log) settled it in one run:

    stage1=188ms stage2=313ms gemini=14040ms total=14693ms
    stage1=247ms stage2=392ms gemini=19092ms total=20059ms
    stage1=186ms stage2= 77ms gemini= 2800ms total= 3212ms

Stockfish is 400-700ms of a move, consistently. **Gemini is everything else**,
and its spread was 2.5s to 19s. The engine was never the problem.

> The earlier "~460-535ms AI reply" figure in this section was wrong, and worth
> saying so plainly: that browser measurement raced - it read `move_count`
> after submitting rather than before, so a move that had already landed
> counted as instant. The honest baseline was **a 5.5s median with a 20s tail.**

#### Two causes, both fixed

**1. A TLS handshake per call.** Every Gemini service opened its own
`httpx.AsyncClient` per request and threw the connection away. Measured against
the real endpoint, five calls each way, identical payload: **1917ms with a new
client, 1388ms pooled - a median 530ms, 28%, spent on setup rather than on
thinking.** `gemini_http.py` now owns one pooled client for all six callers,
warmed at startup so even the first move of a session does not pay for it.

The key moved from the query string into the `x-goog-api-key` header on the way
past. §7 records it leaking through a logged URL once; this makes the URL safe
to log even if somebody re-enables httpx's INFO logging.

**2. A hung model blocking the ones behind it.** The 19s was not a slow model:
it was `GEMINI_MOVE_TIMEOUT` (6s) paid twice by two models that hung, before a
third answered normally. The chain is a fallback list and tried strictly in
order, so *discovering* that a model has hung costs the full timeout, once per
hung model.

`gemini_move_service` now **hedges**: it starts the lead model, and if there is
no answer within `GEMINI_MOVE_HEDGE_DELAY` (1.4s) it starts the next one
*alongside* rather than waiting the timeout out. First usable answer wins, the
rest are cancelled. Nothing is cut - every request carries the identical full
payload, every reply is parsed in full, and a model that was merely slow still
wins if it answers first. Set the delay to 0 and it degrades to exactly the
sequential chain it replaced.

> The extra request goes to a DIFFERENT model, so it does not compete for the
> quota of the one already struggling - which is the same argument §5 makes for
> six chains with six different leads. `GEMINI_MOVE_MAX_IN_FLIGHT` (3) caps it.

#### The result

    before   median 5474ms   min 3591ms   max 20300ms
    after    median 2033ms   min 1744ms   max  3397ms
    again    median 1743ms   min 1479ms   max  3101ms

**The median is down about 65% and the 20-second tail is gone** - which matters
more than the median, because that is the move a player remembers.

#### One bug this introduced, and how it presented

Lifting the timing helper into `decide_ai_move` put its definition *after* the
no-API-key early return, so that path raised `UnboundLocalError`. It surfaced
as `test_decide_integration` **hanging for ten minutes** rather than failing -
which is trap 6 exactly: the exception skipped the suite's
`app.stockfish_service.close()`, and the non-daemon engine thread then held the
process open forever. **A suite that hangs instead of failing is this trap
until proven otherwise.** It was also invisible because the run was piped to
`tail`, which prints nothing until the process ends.

### What this sprint did NOT close

- **The scan's own horizon mismatch remains.** `classify_move` is now
  root-anchored and `analyse_single_ply` takes a third search to be exact, but
  the whole-game scan still pairs neighbouring positions - that reuse is what
  makes it affordable at all (§14). `NOISE_MARGIN` is what stops it reading as
  a verdict.
- **`description` is still written before the position exists** for generated
  scenarios - §16's remaining P3. `describe_material` reduces the damage rather
  than fixing it; the real fix is still to write the description FROM the built
  position, which needs a second model call and is a design change.
- **A `SIGTERM` to uvicorn does not always kill it.** Restarting the backend
  during this pass left the old process alive alongside the new one, with only
  the new one holding `:8081` - `kill -9` was needed. Almost certainly the
  non-daemon engine thread trap 6 describes. **Check with `ss -ltnp | grep
  :8081` rather than `ps` after a restart**: two processes in `ps` and one in
  `ss` is this, and the one in `ss` is the real server.

---

## 28. The hardening sprint — headers, CSP, CSRF, and what was left alone

An OWASP pass over the whole application, run before external beta testers.
This section is the record of it, and it is written to be useful in two
different situations: when somebody wants to change a header (the tables say
what each one is for and what it will break), and when somebody's scanner
reports a finding this pass already considered (the last part says what was
deliberately not done, and why).

**The rule the sprint was run under, and it is worth keeping:** a change had
to mitigate a real attack against *this* application, follow current OWASP
guidance, or be rejected with a reason. Satisfying a scanner was not a
justification on its own. Two of the rejections below are headers a scanner
will flag as missing, and they should stay missing.

### What was actually wrong

Four holes, in the order they matter. Each is a real attack, not a warning.

1. **The application sent no security headers at all.** Not from the backend,
   not from Vercel, not from the Docker nginx. The only one arriving anywhere
   was Vercel's own default HSTS. So every page — including sign-in and the
   beta code box — could be framed by any site, which is a clickjacking
   primitive against the exact two surfaces a stranger is pointed at.
2. **CSRF was reachable.** Production cookies were `SameSite=None`, and about
   twenty POST routes take *no request body* — `/api/reset`, `/api/ai-move`,
   `/api/auth/logout`, every `/api/ai-vs-ai/*` control, all the post-mortem
   navigation. FastAPI never inspects Content-Type when there is no body
   model, so a plain HTML form on any website could submit to them. A form
   POST is a "simple request": no preflight, so CORS never sees it, and the
   response being unreadable is irrelevant because the *effect* has happened.
3. **Every rate limit was bypassable.** `rate_limit.client_ip()` read the
   **leftmost** `X-Forwarded-For` entry, which is the one the caller writes.
   *(Counting from the right, described below, did NOT close this. The real
   cause was uvicorn's own proxy-header handling - see "The independent
   audit" at the end of this section. Read that before trusting this item.)*
   A different value per request meant a fresh bucket per request: unlimited
   `/api/move` (Gemini quota, billed to us), unlimited `/api/auth/login`
   (password guessing), unlimited `/api/beta/redeem`. Every bucket in
   `rate_limit.py` was correct and none of them applied.
4. **No request body ceiling.** Every size check in the app — `MAX_PGN_BYTES`,
   `profile_api.MAX_BODY_BYTES` — runs on a string FastAPI has already
   materialised. A 500MB POST was 500MB resident on a 512MB instance before
   anything got to refuse it.
5. **`GET /` published a route map**, unauthenticated, to anybody. It sits
   outside `/api/`, so `beta_gate.py` never saw it: the application answered
   403 to every request without an invitation and then listed its own
   endpoints at the front door. It also made hiding the interactive docs
   decorative — `DOCS_ENABLED` exists precisely because a complete endpoint map
   "is a convenience on a laptop and an invitation on a public URL", and this
   route was publishing an abbreviated one beside it. It follows the same flag
   now and answers 404 in production. Nothing calls it: the browser talks to
   the Vercel origin, where `/` is the SPA.

### The headers, and what each is doing

Two policies, because there are two origins and they are not the same kind of
thing. The **API** (`security_headers.py`, on every response from this
process) serves JSON to a program. The **document** (`chess-frontend/vercel.json`,
mirrored in `chess-frontend/nginx.conf`) is HTML a browser renders. A policy
that is right for one is wrong for the other, which is why they are separate
files rather than one constant.

| header | API | document | why |
|---|---|---|---|
| `Content-Security-Policy` | `default-src 'none'` | see below | The API should never cause a browser to load anything on its behalf. |
| `frame-ancestors` | `'none'` | `'none'` | Clickjacking. Nothing in Zugzwang is meant to be framed, by us or anyone. |
| `X-Frame-Options` | `DENY` | `DENY` | The same statement to a browser too old to honour CSP. Kept consistent with `frame-ancestors` so there is no ambiguity to resolve. |
| `X-Content-Type-Options` | `nosniff` | `nosniff` | Stops a browser deciding a JSON body that contains attacker-influenced text is really HTML and running it on our origin. |
| `Referrer-Policy` | `no-referrer` | `strict-origin-when-cross-origin` | The API has nothing downstream that needs to know our paths, and several of them carry credentials (a post-mortem game id, a reset token). The document needs the softer value so ordinary in-app navigation works; cross-origin it still sends only the origin, so a reset token in the URL bar never leaves. |
| `Cross-Origin-Opener-Policy` | `same-origin` | `same-origin` | Severs `window.opener`, so a page that popped one of ours cannot reach into it. |
| `Cross-Origin-Resource-Policy` | `same-site` | `same-origin` | `same-origin` on the document because nothing should embed our assets. `same-site` on the API deliberately: a browser talking straight to Render rather than through the Vercel rewrite is legitimate, and `same-origin` would break it silently. |
| `Permissions-Policy` | all off | all off | Camera, microphone, geolocation and the rest. A chess app needs none of them and saying so costs nothing. |
| `Strict-Transport-Security` | production only | Vercel sends it | See the warning below. |

> ⚠️ **HSTS is a two-year promise a browser cannot be talked out of.** Send it
> from `http://localhost` and that browser will refuse plain HTTP on localhost
> — for **every project on the machine**, not just this one — and the only way
> back is `chrome://net-internals`. Hence `hsts_enabled()` follows
> `is_production()`, with `HSTS_ENABLED` as the override for an HTTPS
> deployment that is not Render. Do not "fix" its absence locally.

### The document's CSP, source by source

    default-src 'self'; script-src 'self'; style-src 'self' https://fonts.googleapis.com;
    font-src 'self' https://fonts.gstatic.com; img-src 'self' data:; connect-src 'self';
    frame-ancestors 'none'; frame-src 'none'; object-src 'none'; base-uri 'none';
    form-action 'self'; worker-src 'self'; manifest-src 'self'; upgrade-insecure-requests

**There is no `unsafe-inline` and no `unsafe-eval`, and both are achievable
rather than aspirational** — checked against the built bundle, not assumed:

- **`script-src 'self'`.** Vite's production build emits one `<script
  type="module" src="/assets/…">` and no inline script at all. Nothing in the
  app calls `eval` or `new Function`.
- **`style-src 'self' https://fonts.googleapis.com`.** The Google Fonts
  stylesheet is the only external one. React's `style={{…}}` props are applied
  through the CSSOM (`node.style.x = …`), which CSP does not govern, so the
  components using inline style props need nothing. There are no `<style>`
  tags and no CSS-in-JS library in the tree — checked: no styled-components,
  no emotion. The one `createElement("style")` in the bundle is React 19's own
  `<style precedence>` resource support, which nothing here renders.
- **`font-src https://fonts.gstatic.com`.** Where the Google Fonts CSS points.
- **`img-src 'self' data:`.** `data:` is for exactly one thing: the inline SVG
  favicon in `index.html`. Every chess piece is inline SVG in the document, not
  a fetched image.
- **`connect-src 'self'`.** The whole API is same-origin through the Vercel
  rewrite, so nothing has to be opened for it. **This is the directive that
  will break if anybody points the frontend straight at Render** — add the
  Render origin here in the same edit as the `VITE_API_BASE` that caused it.
- **`form-action 'self'`.** Every form in the app is an `onSubmit` handler
  doing a `fetch`; none has an `action` to anywhere. Google sign-in is a link
  to a same-origin `/api/auth/google/start`, which redirects — a redirect is
  not a form submission and is unaffected.
- **`upgrade-insecure-requests`** on Vercel only. `nginx.conf` omits it
  because the Docker build is `http://localhost:3000` and the directive would
  rewrite its own asset requests to a scheme nothing there answers.

**The two files must be edited together.** `nginx.conf` mirrors `vercel.json`
so that the build you can test locally is telling the truth about its own
defences. The two deliberate differences are HSTS and
`upgrade-insecure-requests`, both noted in `nginx.conf` itself.

### CSRF: two locks, and why neither alone

**`SameSite=Lax` is now the default everywhere**, production included. The old
`None` rested on a belief this file had already corrected once: that Vercel and
Render are "genuinely cross-site". They are not — `vercel.json` rewrites
`/api/:path*` **server-side**, so the browser only ever sees the Vercel origin
and these cookies are first-party. Verified by request rather than read from
config: `https://chess-app-rho-swart.vercel.app/api/health` answers 200 from
the Vercel origin with `x-render-origin-server: uvicorn`. So `None` bought
nothing and cost the free CSRF protection.

**`csrf.py` is the second lock** — an Origin check on every unsafe method,
refusing anything whose `Origin` (or, failing that, `Referer`) is not an
origin this deployment serves. It exists because the first lock is one
environment variable from being undone (`COOKIE_SAMESITE=none`, set by
somebody debugging a cookie that is not arriving) and a `SameSite` regression
is completely silent.

**A request with no `Origin` at all is allowed, and that is a decision.** A
browser cannot produce one — a page cannot suppress the header, `fetch` cannot
override it, a form cannot omit it. The callers that legitimately send none are
the ones that cannot be CSRF'd: `curl`, the TestClient suites, Render's health
check. Refusing them would break every suite in the repository to defend
against an attacker who, being able to set arbitrary headers already, does not
need the victim's cookie at all.

> ⚠️ **`csrf.SAFE_METHODS` is only correct while GET is genuinely read-only.**
> A GET that mutates re-opens this hole silently. There has been one —
> `GET /api/reset` destroyed a game in progress, and became a POST in §22.

### The middleware stack, outermost first

    SecurityHeaders -> BodyLimit -> Identity -> CORS -> Csrf -> BetaGate -> routes

Every position is load-bearing and **none of them fails loudly when wrong**,
which is why `test_security.py` §7 asserts the order:

- **SecurityHeaders outermost**, or the headers miss precisely the responses
  most likely to lack them: a CORS rejection, the gate's 403, the body limit's
  413, an unhandled 500. None of those reaches an inner `call_next`.
- **BodyLimit next**, because it has to refuse an oversized body before
  anything downstream buffers it — before Identity mints a cookie for it and
  before the router builds a Pydantic model from it.
- **Identity before CORS and the gate**, or `request.state.identity` does not
  exist for either of them to authorize.
- **CORS outside Csrf and BetaGate**, or their 403s reach a cross-origin
  caller stripped of CORS headers and present in the browser as a network
  error with no status at all.
- **Csrf outside BetaGate**, because a forged request should be refused as
  forged whether or not the forger also holds beta access.

Remember Starlette runs the **last-added** middleware outermost. That
inversion is easy to get backwards — it was got backwards once while writing
this, putting the CSRF check outside CORS, and nothing failed except the test
that asserts the order.

### The body ceiling

`body_limit.py`, in front of everything. 64KB by default — the largest
ordinary body in the app is a chat message — with two exceptions taken from
the constants their own parsers already enforce, so a body that clears the
guard is one the route can actually accept:

| route | ceiling | from |
|---|---|---|
| `/api/postmortem/import` | `MAX_PGN_BYTES` + 32KB | one game, plus its JSON envelope |
| `/api/profile/games` | `profile_api.MAX_BODY_BYTES` + 32KB | 50 games in one request |
| everything else | 64KB | |

It is a **pure ASGI middleware, not a `BaseHTTPMiddleware`**, because the only
place a byte can be counted before it is buffered is around the raw `receive`
channel. And when the ceiling is passed it **truncates the stream and replaces
the response**, rather than raising: FastAPI wraps the whole body read in
`except Exception: raise HTTPException(400, "There was an error parsing the
body")`, so an exception from `receive` is swallowed and the caller is told
their perfectly well-formed JSON was malformed. That cost real time; it is
written down in the module too.

### Proxy trust

`TRUSTED_PROXY_HOPS`, default `1`, declared explicitly in `render.yaml`.
`client_ip()` counts back from the **right** of `X-Forwarded-For` by that many
hops. `1` is correct for Render — `Dockerfile.backend` runs uvicorn directly,
with no nginx of its own, so their edge is the only appending hop — and
correct for the Docker stack, where nginx is. Raise it only if another
appending proxy is genuinely put in front.

### What was checked and found to need nothing

The half of an audit worth writing down, so nobody re-derives it. All of this
was read closely and is correct as it stands:

- **The beta gate.** Deny-by-default middleware over a `/api/` prefix, with a
  short exact-path exception list; nothing in the decision comes from the
  client. No bypass was constructible from DevTools, a forged cookie, a direct
  `fetch`, or curl. §26 has the design; `test_beta_access.py` has 123 checks.
- **Authentication.** PBKDF2-HMAC-SHA256 at 600,000 iterations with a 16-byte
  per-user salt; session and reset tokens stored **hashed only**; the session
  token is freshly minted at login, so there is no fixation window; login and
  `forgot-password` answer identically regardless of whether the account
  exists; OAuth `state` compared with `compare_digest`; the redirect URI is
  configuration and never derived from a `Host` header.
- **Authorization.** Every router resolves the caller with
  `identity_of(request)` and scopes on it. Post-mortem and sandbox answer
  **404, not 403**, for somebody else's id — distinguishing them would confirm
  a guessed id is real. No endpoint takes a beneficiary parameter; there is no
  IDOR to find because there is no client-supplied owner anywhere.
- **SQL injection.** Every query is parameterised. The only f-strings in SQL
  are schema *identifiers* in `db.py`, which come from `DATABASE_SCHEMA` — an
  operator's environment variable, never a request.
- **XSS.** One `dangerouslySetInnerHTML` in the tree (`pieceThemes.tsx`), fed
  from a hardcoded table of piece SVGs with no interpolation. React escapes
  everything else, and the CSP above is the backstop.
- **PGN upload.** 512KB, 800 plies, headers truncated to 120 characters, and a
  game that cannot be replayed move-for-move is refused rather than truncated.
  Malformed input is a 400 with a sentence for a chess player.
- **Secrets.** Nothing in the repository, nothing in the bundle — the only
  `VITE_` variable is `VITE_CONTACT_EMAIL`, which is meant to be public. `.env`
  is ignored. `httpx` request logging is pinned to WARNING because the Gemini
  REST URL carries the key as a query parameter (§1).
- **Rate limiting coverage.** Every endpoint that spends Gemini quota or
  Stockfish time has a bucket; so do login, signup, both password-reset routes,
  and beta redemption (per IP *and* per identity). The buckets were fine — only
  the key was wrong, which is finding 3 above.
- **`/api/health`.** Publishes only what an operator needs to compare a deploy:
  status, engine presence, database reachability, the commit, `beta_required`,
  and an email *reason code* — never which variables are missing.

### Rejected, with reasons

- **`X-XSS-Protection`.** Scanners still ask for it. The filter it enabled was
  itself exploitable, Chrome removed the auditor outright, and every current
  browser ignores the header. `0` is the only defensible value and it changes
  nothing. `test_security.py` asserts its **absence**, so restoring it from a
  scanner report requires deleting a test that says why not.
- **A CSRF synchroniser token.** The textbook answer, and the wrong one here.
  It needs per-session storage, an endpoint to hand it out, a change to every
  write in `chessService.ts`, and a rotation story — all to establish a fact
  the browser already tells us for free in `Origin`. OWASP lists origin
  verification as a legitimate primary defence, not merely a supplement.
- **CSP `report-uri` / `Content-Security-Policy-Report-Only`.** There is no
  endpoint to receive reports and nobody watching for them. A report
  destination nobody reads is a header that looks like monitoring and is not.
- **`Cross-Origin-Embedder-Policy: require-corp`.** It would buy cross-origin
  isolation, which this app has no use for (no `SharedArrayBuffer`, no precise
  timers), at the cost of breaking Google Fonts unless every font response
  carries CORP. Cost with no benefit.
- **`Clear-Site-Data` on logout.** It would drop the frontend's caches and any
  service-worker state along with the cookie. Logout here means "end this
  session", not "evict the application". Revisit if a real
  logout-on-a-shared-device requirement appears.
- **Moving to Argon2id.** PBKDF2 at 600k iterations is OWASP's own current
  figure for this construction, and `hashlib` is in the standard library.
  Argon2 would be better in the abstract and would add a compiled dependency to
  the image for a margin nothing here is close to needing.

### Future uploads — the rules to start from

There are no file uploads today. The PGN path is JSON text, deliberately: the
browser has already read the dropped file, so no multipart dependency is in the
image. Avatars and attachments are the obvious next ones, and they change the
threat model rather than extending it, so the rules go here **before** anybody
builds one:

1. **Never trust the filename.** Store under a server-generated id; keep the
   original name as a display-only string, escaped, never as a path component.
   Nothing user-supplied may reach a filesystem path — that is path traversal
   and it is the oldest one there is.
2. **Never trust the declared Content-Type.** Sniff the actual bytes and
   accept an explicit allowlist of formats. Re-encode images rather than
   storing what arrived; that also strips EXIF, which for an avatar can carry
   the user's GPS coordinates.
3. **Serve from a different origin, or force a download.** An SVG is a
   document and can carry script, so an SVG avatar served from the app's own
   origin is stored XSS with the CSP unable to help — same origin, so
   `'self'` covers it. Either serve user files from a separate host, or send
   `Content-Disposition: attachment` with `X-Content-Type-Options: nosniff`
   and refuse SVG outright.
4. **A size limit at the edge, and a per-account quota behind it.**
   `body_limit.py` needs an entry for the route, and `profile_service`'s
   `MAX_GAMES_PER_OWNER` is the shape of the second one: without a quota, one
   account can fill the disk.
5. **The store is not the database.** Render's filesystem is ephemeral (§2), so
   an upload written to disk is gone on the next deploy. It needs object
   storage, and that decision belongs in the design rather than after it.
6. **Ownership on read, not just on write.** Every read must go through the
   same `identity_of(request)` check every other router uses. An unguessable
   URL is not authorization — it is the thing post-mortem ids get away with
   *because* the object is worthless to anyone else.


### The independent audit, and the two things it found

The hardening above was then audited adversarially by a second agent (Codex)
against the running `:3001` build, and re-verified by a third pass. That audit
is the reason this subsection exists, and it earned its place: **the headline
fix in the section above was wrong.**

#### Finding 1 — the rate-limit bypass was never fixed by the hop counting

Reported as release-blocking, reproduced independently, and correct.

Through `localhost:3001`, twelve failed logins with a **fixed**
`X-Forwarded-For` gave `401 x10, 429 x2` — the limiter working. The same twelve
with a **different** value each time gave `401 x12`. Unlimited password
guessing, from outside, against a limiter that was behaving perfectly.

The section above claims this was fixed by counting back from the right of
`X-Forwarded-For`. It was not, and the reason is worth stating plainly because
it is invisible in the application's own source:

> **uvicorn ships `ProxyHeadersMiddleware` ENABLED BY DEFAULT, and it rewrites
> `scope["client"]` from `X-Forwarded-For`.**

So `request.client.host` — the "socket peer", the unforgeable fallback that
every version of `client_ip()` has leaned on, including the hardened one — was
*itself* the attacker's header value. The application was carefully choosing
between a trusted value and a poisoned one, and both were poisoned.

Proof, on the running build with nothing else changed:

```
rotating X-Forwarded-For, uvicorn default          : 401 x12
rotating X-Forwarded-For, uvicorn --no-proxy-headers: 401 x10, 429 x2
```

**The fix is `--no-proxy-headers` at every launch site.** `Dockerfile.backend`
(Render), `Dockerfile` (the all-in-one image), and the dev runner in §12 all
carry it now, and `test_security.py` §5b asserts that they do — a static check,
because no in-process test can see uvicorn at all. With it off, uvicorn leaves
`scope["client"]` as the real TCP peer and this application has exactly **one**
place that decides what the caller's address is: `rate_limit.client_ip()`,
governed by `TRUSTED_PROXY_HOPS`.

> ⚠️ **Never remove that flag to "fix" a wrong client IP.** Set
> `TRUSTED_PROXY_HOPS` instead. Removing it silently restores unlimited
> password guessing, beta-code guessing and Gemini spend.

Two smaller corrections came with it:

* **`TRUSTED_PROXY_HOPS` now defaults to `0`**, not `1`. Too low only means
  callers behind a proxy share a bucket — annoying, never insecure. Too high
  means the limiter reads an entry the caller wrote — silent, and total. The
  default is now the harmless direction and a deployment opts in: `render.yaml`
  sets `1`, `docker-compose.yml` sets `1` (its nginx appends).
* **A chain shorter than the promised hops is no longer used at all.** The
  first version clamped the index at `0` and returned the leftmost entry, which
  is the caller's own text. Written as an `IndexError` guard, it quietly meant
  "return the attacker's value rather than raise". It falls back to the socket
  peer now.

Two consequences of `--no-proxy-headers` that had to be handled, because
uvicorn also stops rewriting the *scheme*:

* `csrf.py` compares the request's own origin allowing **either scheme** for
  its own host. Behind Render's TLS terminator `request.url` says `http` while
  the browser correctly says `https`, and comparing full origins would reject a
  genuine same-origin request in production only. The host is the
  security-relevant half; an attacker cannot make their page's origin carry our
  host.
* `_google_redirect_uri`'s fallback now builds an `http://` URI behind a
  terminator, and Google requires an exact match. **Set `GOOGLE_REDIRECT_URI`
  explicitly in any deployment** — noted at the function and in `render.yaml`.

#### A second limit on login, that no header can move

Because the whole class of defect above is "the address was never trustworthy",
the login endpoint no longer relies solely on one:

`login_by_username` (20 failures per 15 minutes) is keyed on the **account
being guessed**, which is in the request body, is the thing the attack is for,
and cannot be varied by someone working through one person's passwords. It
counts failures only and is checked *before* the password is verified — a
correct password spends nothing, so ordinary use can never lock anyone out, and
a refusal costs no PBKDF2 (600,000 iterations is a CPU attack if an attacker
can keep buying them past the limit).

`RateLimiter` gained `refuse_if_full()` and `record()` for this; `check()` is
unchanged and is still what every other caller uses.

**The trade-off, stated rather than glossed:** an attacker who knows a username
can spend that bucket deliberately and lock the account out of *password*
sign-in for up to fifteen minutes. Accepted knowingly — the alternatives are
unlimited guessing or unlimited PBKDF2 — and it does not touch an existing
session, Google sign-in, or password reset.

#### Finding 2 — the per-account bucket has two keys per account (Low, open)

Found while auditing the fix above, and **not fixed**.

The key is the identifier as submitted, normalised the same way
`verify_password` normalises it, so `alice`, `ALICE` and ` alice ` share one
bucket. But sign-in accepts a username **or** an email for the same account,
and those are different strings. Measured on the live keys:

```
per-account bucket keys: {'alice': 3, 'alice@example.invalid': 1}
```

So one account has two buckets and a determined attacker gets 40 attempts per
fifteen minutes rather than 20.

Left open deliberately. This is the *second* limit on the endpoint; the per-IP
bucket (10 per 5 minutes) is the primary and is unaffected, and 40 online
guesses per quarter hour against an eight-character minimum goes nowhere. The
fix, if it is ever wanted, is to resolve the identifier to a canonical account
key before bucketing — one indexed lookup, ahead of the PBKDF2 — rather than
keying on what the caller typed.

#### What the audit confirmed, reproduced independently

Re-driven against the running build rather than taken on trust (29/29 in a
fresh harness, on top of the suites): a forged or MAC-tampered `zw_guest`
cookie is refused and replaced; a planted `zw_session` never authenticates;
signup and login each mint a fresh session (no fixation) and retire the old
guest identity; a session replayed after logout is dead; a password change
invalidates other browsers' sessions; a second account gets **404** on read,
drive and delete against another identity's Review and Learn objects and sees
an empty library; a retired guest cookie reaches no account data; malformed,
empty, over-long and oversized PGNs are refused (400/400/400/413); duplicate
library imports add nothing; and account deletion works.

Also probed and clean: SQL injection through the login and forgot-password
bodies (generic 401 / generic 200, no error, no oracle), path traversal in a
game id (404), identity injection via request headers (no effect), and error
responses on 4xx (no traceback, no internals). Password-reset links are built
from `FRONTEND_URL` alone — `_reset_link` takes no request object at all — so
there is no host-header poisoning path.

#### The dev launcher is not a safe configuration, and that is on purpose

`/tmp/run_backend.sh` exports `BETA_ACCESS_REQUIRED=false` **and**
`RATE_LIMITS_ENABLED=false`. Both are correct for local work and both are
disastrous if that command shape is ever copied into a deployment. The audit
flagged the running dev configuration as unsafe to expose, and it is right —
but it is the dev stack, not what ships. `render.yaml` sets
`BETA_ACCESS_REQUIRED=true` and never sets `RATE_LIMITS_ENABLED`, and both
flags fail safe: only the literal string `false` opens either.

**When auditing rate limiting, start the backend with limits ON** or every
probe reads as a bypass:

```bash
sed 's/RATE_LIMITS_ENABLED=false/RATE_LIMITS_ENABLED=true/' /tmp/run_backend.sh > /tmp/run_backend_limits.sh
```

#### The one thing that cannot be verified from here

`TRUSTED_PROXY_HOPS=1` is correct for Render **on the stated assumption that
their edge appends exactly one entry to `X-Forwarded-For`**. That was not
verifiable from this machine: the currently deployed build reads the leftmost
entry, so probing production tells you nothing about how many hops it has.

Verify it once, after the deploy, with the same two runs that found the bug:

```bash
# N+1 failed logins with a FIXED header, then again with a ROTATING one.
# Both must end in 429. If the rotating run never does, the number is too high.
```

Until that check is run, treat the production hop count as declared rather than
confirmed. Everything else in this section was reproduced on a running build.

### Tests

`test_security.py`, **61 checks**, the eighteenth suite. It needs neither
Stockfish nor `DATABASE_URL` — deliberately, so a security regression is
catchable in four seconds by anybody who has not set up an environment:

```bash
DISABLE_LANGFLOW=true /tmp/chessapp/bin/python -u test_security.py
```

It asserts properties rather than spellings — "the policy forbids framing", not
"the policy is this string" — so tightening the CSP does not break the suite
that guards it.

---

## 29. The unified Chat + Actions panel — one conversation, fewer buttons

A UI/UX sprint, not a feature: nothing was added to what the coach can do,
and nothing it could do was removed. Two changes, in every mode.

**There is no Coach tab any more, in any mode. The coach speaks in Chat.**
Play used to show Gemini's explanation of its latest move on a Coach tab -
only the latest, replaced on every move, on a different tab from the box you
would ask "why?" in. Learn's Coach tab listed the current line with the
narration under each move and was rewritten every time the line was stepped
back. Review's tab was the chat already, labelled Coach. Now:

- **Play**: each AI move's explanation is a turn in the conversation,
  `**{san}** — {explanation}`, appended **server-side** by
  `PlayerSession.note_coach_turn` on all three AI-move paths (background,
  manual, AI-vs-AI). Two things follow: the history replayed to Gemini on the
  next question carries every explanation, so "why?" three moves later still
  has its subject; and `/api/status` hands the whole transcript back on a
  reload. **The browser never composes a coach bubble** - the server owns the
  transcript and the component mirrors it (`toChatMessages` in
  `ChessBoard.tsx`, fed from every `/api/status` read and from `/api/chat`'s
  `history`). If the server's format changes, the bubble changes with it;
  there is no second copy to drift. A question in flight is drawn from
  `chatPending`, and a failure goes under the log (`chatError`) rather than
  into it, so the log never carries a bubble the server never saw.
- **Learn**: a move the coach has something to say about (an AI move's own
  reasoning, or a narration that was asked for) becomes a `coach` transcript
  entry, once, in the order played (`coachedRef` keyed on session and node).
  The narration is *not* stored on the entry - it arrives asynchronously and
  is read from the narration map by node id at render time, exactly as the
  Coach tab read it. Stepping back and forward does not repeat a move; a
  different move from an earlier position is a new node and a new message.
  The transcript still survives a reset and a rebuild across a rule-divider,
  as before - the user chose to keep that over the spec's "clear on reset".
- **Review**: the tab is called Chat. Nothing else changed.

**Actions is the last tab in every mode**, holding what used to sit under the
board and is reached for once a game, if that. The user chose what moved:

| mode | moved into Actions | stayed under the board |
|---|---|---|
| Play | Watch AI play, Engine numbers, Coordinates, Board size, **Clear chat** | New game (above), Make AI move, Play as Black, difficulty; the AI-vs-AI transport (Pause/Resume/Next/Exit) still appears under the board while that mode runs |
| Learn | Eval bar, Coach my moves, Board size, **Clear chat** | AI move, Take over, Back, Forward; then **Reset board / View as Black as a second two-button transport row** (the same shape as Play's Make AI move / Play as Black); then difficulty |
| Review | Board size, **Clear chat** | Close game (above), Previous, Next, Try a move, Back to the game, View as Black |

The second pass (the user's call, after seeing the first): the display
switches and the board size went to Actions in every mode, and Learn's
eight-control display row became two big buttons plus the difficulty
select. **Difficulty is alone on the meta row in Play and Learn**, right
aligned and now carrying a visible "Difficulty" label - alone, it has the
room. The 471-560px container band in `shell.css` that swapped meta-row
labels for short spellings is gone with the crowded row that needed it; the
`.ws-label-tight` spans remain in the markup and stay hidden. Learn's
`.sandbox-takeover-row`, `.sandbox-check`, `.sandbox-row-divider` and
`.sandbox-board-actions` rules were deleted as dead.

**Clear chat is new, and it reaches the server**: `POST /api/chat/clear`,
`DELETE /api/sandbox/session/{id}/chat`, `DELETE /api/postmortem/game/{id}/chat`.
The server's transcript is the one replayed to Gemini, so a clear that only
emptied the screen would leave the coach remembering what the reader had just
watched disappear, and a reload would bring it all back. Play's is a
body-less destructive POST, so `test_security.py` asserts the origin check
refuses it cross-site.

Persisted tab names: a stored `chess-active-section` of `analysis` or a
`sandbox-panel` of `coach` fails validation and lands on Chat.

Verified on `:3001` by `tools/verify/chat.mjs` (50/50): the coach's
explanation lands in Chat with the pending dots before it; "why?" is answered
under it; the conversation survives tab switches, the next move and a reload;
Clear chat empties both sides and leaves the board; New game clears; Watch AI
play from Actions starts AI-vs-AI; the Coordinates switch in Actions still
toggles the labels; and the same for Learn's narration-in-chat and Review's
rename. Plus 118/118 UI, 127/127 interaction, 22/22 board-state, and
`tools/verify/overlap.mjs` 300/300 - a geometric sweep of every mode, every
tab, 1920/1440/1280/1024/390 wide, both themes, asserting that no two visible
elements intersect (rects clipped to their scroll containers, ancestor pairs
excluded) and nothing reaches past the viewport.

**The eval bar (third pass, the user's call):** vertical, beside the board
on its left, black and white, the board's height. One component for Play's
"Engine numbers" and Learn's "Eval bar" - `components/EvalBar.tsx`, styled in
`styles/shell.css`. Three promises and how each is kept:

- **It matches the board's size.** `height: var(--board-size)` - the very
  number the board is drawn at - and it is positioned against the board FRAME
  (`position: absolute; right: 100%; top: <frame padding>`), so its top edge
  is the board's top edge at every size. Probed on :3001: bar 462/372/572px
  tall at Auto/Small/Large against a board interior of 462/372/572.
- **Toggling it moves nothing.** The workspace reserves `--ws-eval-slot`
  (26px: an 18px bar and an 8px gap) to the left of the board at all times in
  `.chess-container` and `.sandbox` (0 in Review, which has no bar). The grid's
  board track and the board column are widened by it, and the column carries
  it as `padding-left`, so the board and every row under it sit exactly where
  they would without the bar, one slot further right; the bar hangs into the
  padding and is `visibility: hidden` when off. Probed: board x identical on
  and off.
- **Standard colours.** `#f2f2f2` on `#1a1a1a`, fixed rather than the theme's
  board squares - the bar says who is ahead and white and black are the two
  answers - with a hairline in `--border-strong` so the white half does not
  vanish into the light ground. White's share scales from the bottom
  (`transform`, not `height`, since it moves on every half-move) and from the
  top when Black is at the bottom of the board (`is-flipped`, from
  `playerColor` / `orientation`; probed both ways - the fill anchors to the
  bottom edge with White at the bottom and to the top edge after Play as
  Black / View as Black). The score is printed UNDER the bar at `--text-xs`
  in `--text-secondary`: inside the bar at 9px it could not be read.

The horizontal strips (`.game-eval` on Play's `.game-strip`, `.sandbox-eval`
on Learn's `.sandbox-strip`) and their CSS are gone. `ui.mjs`'s Learn
centring check now measures from the board column's left edge, which is the
slot's edge, because the slot is part of the layout.

One CSS fix on the way: `.chat-canvas-inner` was `height: auto` inside a
block canvas, so its `flex: 1` did nothing and the log hugged its content,
leaving the lower half of the panel empty under the composer. It is
`height: 100%` now; the log fills the panel and the composer sits at its
foot. Chat is the first tab, so that empty half was the first thing on screen.

---

## 30. Barry validation readiness — honest coverage, a measurable loop, and grade audit provenance

This branch is a narrow follow-up to Barry's 2026-09-11 beta reassessment. It
does not implement the parallel direct-playtester UI requests. Its product
boundary is Post-Mortem trust, correction-cycle validation, and demo readiness.

### What “1 move graded” actually meant

The scan was not stopping after one move: `_run_scan()` visits every mainline
ply. `move_quality.summarize_accuracy()` intentionally excludes `book` and
`forced` labels from the percentage denominator. In an eight-half-move opening,
three moves per side can therefore be checked and labelled as book while only
one engine-judged decision contributes to each percentage. “1 move graded”
made that denominator look like scan coverage.

The API now reports both concepts: `coverage` has analysed/total/skipped plies
and skip reason; each side has analysed/total, scored decisions, and exclusions
from the score. The report calls the percentage **Decision accuracy**, states
that book/forced moves were checked but excluded, and states whether every move
was checked or an engine failure left a partial report. Keep those concepts
separate in any future copy.

### First-party validation events

`learning_events.py` accepts a fixed vocabulary and a fixed scalar property
allowlist. It rejects unknown event names and discards unknown properties. It
must never accept raw PGNs, filenames, intent/chat/explanation text, email, or
credentials. The stored `actor` is an HMAC of the internal identity using
`ANALYTICS_HASH_KEY`, falling back to `SESSION_COOKIE_SECRET`; without either,
the per-process key is deliberately unstable and the summary says so.

Events stay in a bounded in-memory buffer and are also best-effort persisted to
`product_events` when Postgres exists. `/api/learning-loop/funnel` returns only
aggregates: step counts/unique testers, multiple-import and 14-day-return
counts, timing averages/maxima, and top error categories. The maintainer JSON
or CSV path is `tools/export_beta_metrics.py`.

The canonical loop covers import, analysis start/completion, decision select,
intent, correction generation/view, better-move attempt, practice open/hint/
attempt/completion, card completion, disagreement, abandonment, later import,
and correction-flow errors. Stage events cover engine, LLM, AI reply, browser
move render, and browser explanation render. Keep instrumentation best-effort:
an analytics failure must never interrupt coaching.

### Grade audit

Migration 009 adds `move_grade_audits`. `move_feedback_log.log_grade()` writes
FEN before/after, played/best move, side/player colour, evaluations and
perspective, depth, beta grade, flow, key-decision/correction link, provider/
model, fallback/timeout and timestamp. `tools/audit_move_grades.py` can export
records or re-run 1–50 stored positions at a deeper depth and report beta vs
deeper label, CPL and agreement without mutating the original. FEN is retained
because this is the minimum reproducible engine input; raw PGN is not.

### User-visible timing and trust behavior

Expensive paths immediately say what is happening: importing/analyzing,
finding decisions, checking an alternative, choosing the reply, preparing a
correction, asking the coach, and generating practice. Status rows reserve
height so the board and controls do not jump. A correction card always names
the engine depth and says close calls can change at deeper search; completion
ends with the next-time rule. Selecting a report decision opens that exact
move in Correct only after navigation completes.

“Let me play on the board” goes back to the position before the diagnosed
move, because that is where the engine alternative is legal. The correction
card and its original move label remain visible through the player's branch
and the coach reply, so fresh practice is still the next step rather than a
reset. One branch is de-duplicated to one `alternative_move_played` event even
when the card object refreshes after practice.

### Repository quality gate completed with this sprint

The repository-wide frontend lint baseline is now clean: **zero errors and
zero warnings**. The fix did not suppress rules. Broad `any` response shapes
became explicit chess/game/learning contracts; promotion helpers moved to
`components/promotion.ts`; the board-size control moved out of the shared hook
module; `AuthShell` and `useAuthConfig` got component-safe module boundaries;
and the two real hook dependency omissions were corrected. This is why those
small files are part of `b4d96e2` even though they are not Post-Mortem features.
The build and all browser suites were rerun after the split.

### Operational boundary

RevenueCat is not implemented and invitations are not subscriptions.
`docs/SHIPATON_READINESS.md` records the likely isolation boundary and release
checks without claiming eligibility. The new optional `ANALYTICS_HASH_KEY` is
documented; the existing stable session secret is a valid fallback. Migration
009 applies on normal startup. The implementation was committed as
**`b4d96e2`** and pushed as a fast-forward to `origin/master` on 2026-09-11;
that push started the configured auto-deploys. The live rollout was not probed
afterwards, so pushed and deployed remain two separate claims.

Verification on 2026-09-11: `test_postmortem_api.py` 82/82,
`test_learning_loop.py` 87/87, `test_learning_loop_api.py` 89/89,
`test_move_feedback.py` 100/100,
`test_security.py` 61/61, frontend production build, migration plus real
event/audit persistence and both exports in a disposable schema, focused live
browser pass 40/40, UI 118/118, and interaction 127/127. The complete matrix
then passed **1402/1402 across all 18 suites** against a disposable schema,
which was dropped after the run. Frontend lint passes with zero warnings or
errors. The dev backend used the configured default `public`
database schema at restart, so the additive 009 migration is already present
there. The first ten exact synthetic browser-probe games were cleaned from the
new tables (140 event rows and 79 audit rows). The final post-lint verification
created fourteen more exact synthetic games; their 95 event rows and 358 audit
rows were also deleted, with zero of those ids remaining. Cohort metrics do not
include either QA batch. No Docker build was performed. The verified tree was
committed and pushed, but production deployment health remains unverified.

---

## 31. Guided Play — the coach trains attention, without giving the answer

A beta tester playing Merciless said the explanation *"only says things about
the current move. It does not tell you what to look out for."* Two things
came out of that, and this section is both.

**Guided Play** is a switch. Off (the default), an AI move's explanation is
exactly what it was. On, the same explanation gains a **Watch out** block:
one to three attention prompts for the human - *is the piece I just moved
loose, is your e4 pawn still defended, look at checks, captures and threats
for both sides* - and never a move. The rule is the product's: train
attention, do not play for the user. The prompt forbids recommending,
naming or hinting at a reply, and `tools/verify/guided.mjs` asserts the live
text against a spoon-feeding regex.

### How it is built, and the decisions behind it

- **One Gemini call, not two.** The user chose this explicitly over a
  second post-move narration call: simpler, cheaper, and the explanation
  and the coaching stay coherent because one reply wrote both. The cost is
  ~40 more output tokens on the latency-critical move request; measured
  guided moves came back in 1.0-3.9s of Gemini time, inside the 6s timeout
  with the hedge unchanged. `decide_ai_move(..., guided=True)` is the whole
  switch; every other caller (sandbox, Post-Mortem, AI-vs-AI) passes nothing
  and gets the byte-identical standard prompt - `test_guided_play_api.py`
  asserts that.
- **The facts come from python-chess, the prose from Gemini.** For each of
  the ≤3 shortlisted moves, `guided_play.candidate_facts()` computes what
  the move does once played: gives check, which enemy pieces the moved piece
  attacks, which of those are undefended, and whether the mover itself lands
  loose. `describe_candidates()` puts that in the guided prompt as "these
  are true; do not invent threats they do not list". Engine stays the
  source of truth; the FEN and shortlist context is unchanged.
- **The section rides inside the reply and is split back out.**
  `guided_play.split_watch_out()` takes the model's `Watch out:` line off
  the explanation. `PlayerSession.note_coach_turn(san, body,
  watch_out=...)` files it under its own key on the turn AND on the end of
  `text`, so the chat model replaying the transcript knows what the coach
  already told the player to look at. The move list's hover text and the
  learning record get the body only. `settle_ai_explanation()` in `app.py`
  is the one place both Play routes do this.
- **The flag rides on the request**, not the session: `POST /api/move
  {move, guided}` and `POST /api/ai-move {guided}` (body optional - older
  clients and curl still work). No new endpoint. The preference has one
  owner, the browser's settings.
- **Persistence** is the existing pattern: `chess-guided-play` in
  `localStorage` via `preferences.ts`, synced to the account as
  `guidedPlay` (`settings_service.ALLOWED/DEFAULTS`; jsonb, no migration),
  and listed on the account Settings page.
- **Events**, through `learning_events` (§30's vocabulary):
  `guided_play_enabled` / `guided_play_disabled` (from the browser via
  `/api/learning-loop/events`, with `difficulty` and `ply_index`) and
  `ai_move_explanation_generated` / `guided_watchout_generated` (server-side,
  with `difficulty`, `ply_index`, `side_to_move`, `source`
  gemini/stockfish_fallback, `guided`, `duration_ms`, `game_id`,
  `source_mode=Play`). `difficulty`, `guided` and `source` joined the
  property allow-list. No explanation text is ever recorded.

### Where it lives on screen

The full switch with its subtitle - *After the AI moves, show what to watch
for before your reply* - is in Play's **Actions** tab, after Coordinates,
where the other set-once settings are. Because the tester who wanted this
would not have opened Actions, the same switch (the Moves header's grading
toggle, reused) sits on one short line above the Chat composer with an
On/Off word beside it, and the Chat empty state mentions it. The Watch out
block is drawn inside the coach's own bubble under the explanation, in the
AI's colour, so nothing new competes with the board: toggling changes no
layout at all (`guided.mjs` asserts `--board-size` before and after).

### The difficulty control

It was visible and unclipped (§27 fixed that), but labelled **Difficulty**,
and the AI player strip said "difficulty 20". Both now say **AI strength**
/ "strength 20", and the subtitle under the title still spells out the band
and its blurb. Copy only; the control did not move.

### What is not done

- The section's tone is the model's. The regex in `guided.mjs` catches
  "best move" / "you should play" / "play e4"-shaped answers; it does not
  catch a subtler give-away. Read a few live ones after any prompt change.
- A long section pushes the explanation above it out of a short chat list
  (the list auto-scrolls to the newest turn, as it always has). The prompt
  caps the section at 40 words for exactly this reason.
- Langflow's older chooser does not know the guided prompt; with
  `DISABLE_LANGFLOW` unset and no Gemini key, Guided Play silently yields a
  standard explanation.

---

## 32. "Review this game" — Play hands its finished game to Review

The end-of-game layer in Play (checkmate, stalemate, draw - the three
endings the real game has; there is no resignation or clock) carries a
second button under **New game**: **Review this game**, with the hint
*Analyze the game you just played and find your key decision.* One click
and Review opens on that exact game, already analysing. No export, no
paste, no upload.

### How the game gets there

- **The server is the source.** `POST /api/postmortem/from-play` (in
  `app.py`, beside the other Play routes) reads the finished game from the
  session's own `ChessGame.board` and writes a PGN from its `move_stack`
  with python-chess (`play_game_pgn`): `Event "Zugzwang Play"`, `White` /
  `Black` = **You** / **Gemini** by `player_color`, `Result` from
  `board.result(claim_draw=True)`, `Termination`, `Date`, and `AIStrength`
  / `Source "Play"` for anyone reading the file. Nothing about the game
  passes through the browser - the request has no body.
- **The same pipeline as a dropped file.** The PGN goes to
  `postmortem_games.create()`, so it is replayed and validated exactly as
  an import is, owned by the same identity, and every existing
  `/api/postmortem/*` route accepts it. The route then calls the same
  idempotent `start_scan` the Review UI fires on import, and yields once so
  the returned state already says `scan.status: "running"`. The review is
  tagged `origin: "play"` with `player_color` set, both in `to_dict()`.
- **409 while the game is live** ("The game is not over yet"), and 409 in
  AI-vs-AI. A replay failure (which should be impossible for a game the
  server itself played) is a 500 with the chess-player message and a
  `play_game_review_handoff_failed` event.
- **The Play game is untouched.** It stays on its final position; New game
  is still the way to leave it.

### The browser's half

`ChessBoard` posts, then hands `{gameId, playerColor}` to App
(`onReviewGame`); App sets `reviewHandoff` with a nonce and switches the
mode (mounting Review if it never was). `PostMortem` takes `handoff` as a
prop rather than reading `localStorage`: it may already be mounted with
another game open, and its resume effect only runs on mount. The handoff
effect fetches the review, orients the board to the player's colour,
remembers the id under the usual `postmortem-game` key (so a refresh
resumes it like any review) and re-fires the idempotent scan start. While
it fetches, Review shows a landing card - *Analysing the game you just
played…* - in the dropzone's footprint, never the dropzone; if it fails,
the same card offers **Back to Play** and **Paste a PGN instead**. On the
Play side the button reads *Opening review…* while busy and shows the
server's message under itself if refused.

`BoardEndState` gained an optional `secondary` action (label, hint, busy,
error) so the layer stays one component across the three modes; only Play
passes it. The hint hides under 560px, where the board has no room for it.

### Events

Browser, via `/api/learning-loop/events`, `source_mode: Play`:
`play_game_completed` (once per ending, keyed on the move it ended at),
`review_this_game_clicked`, `play_game_review_handoff_started` /
`_completed` / `_failed`. Server: `play_game_analysis_started` (from the
route, with result, termination, difficulty, player colour, ply count) and
`play_game_analysis_completed` (from the scan runner, only for
`origin == "play"`). `result` and `termination` joined the allow-list.
No PGN, no moves.

### Verifying it

`tools/verify/review-handoff.mjs` plays a real game through the UI at
strength 1 - the shortlist is the engine's three worst moves, so a greedy
mate-in-one / Scholar's-pattern / biggest-capture driver reaches mate in
9-40 plies - then takes the handoff into the analysed review and checks
seats, result, orientation, moves, navigation, the completed scan, a
reload, and that Play still holds the ended game. Endings the real game
cannot be steered onto (stalemate, bare kings) are served through a
stubbed `/api/status`, as `interaction.mjs` does, for the layout checks at
1366, 1280, 1920 and 420 wide. The failure paths are stubbed too.

### Limitations

- The greedy driver in the probe is not deterministic; it has always mated
  within the 240s budget so far, but a run that does not is a probe
  problem, not a product one - the served-endings parts still run.
- A review is server-side, capped at 20 and swept after an hour idle
  (§14). "Review this game" spends one of those slots like an import does.
- Only `human_vs_ai` games are offered; AI-vs-AI shows no button.

---

## 33. The loop, verified end to end — and what it turned up

After `92c85f4` shipped, the question was whether a game played *here* runs
the whole learning loop, not just the review. It does, and
`tools/verify/loop.mjs` now proves it on every run:

1. **Play**: the human plays the move that hangs the most (chess.js counts
   attackers), against strength 20, until mated - a queen or rook hung
   within ten plies, every run so far.
2. **Play's own grade** on that move is `blunder`, from Stockfish, with a
   depth.
3. **Review this game** -> the scan finishes -> the same ply is `blunder`
   in the review's evidence packet, with depth and the engine's preferred
   move, and it is listed under *Worth a second look*.
4. **Correct**: opening that decision asks the intent question with presets;
   nothing calls the game unsupported; *Show me what I missed* produces a
   card with a theme, what was missed, a rule, and an engine caveat with
   depth.
5. **Test me on a fresh position** loads a 64-square practice board
   labelled *A different position, same idea*; *Give me a hint* answers; a
   move on the board is judged and the practice tally is shown.

Play-sourced games are not special-cased anywhere in that chain - the
review is an ordinary `PostMortemGame`, which is the whole point of §32's
design.

### Two things found and fixed

- **The engine-fallback correction quoted a mate-scale loss as
  "9561 centipawns."** When every model in the diagnosis chain fails or is
  rejected (it happened once during this sprint: a ReadTimeout, a 429, a
  503, then two answers thrown out for citing an illegal move - the
  validator doing its job), `diagnosis_service.fallback_diagnosis` writes
  the card from the evidence. Its cost sentence now follows
  `moveQuality.lossText`'s scale: pawns below ten, *"threw away a forced
  win, or allowed one"* above, *"missed a forced mate"* for a miss.
- **"2 judged" under the accuracy figure read as the whole game graded.**
  Play's Review tab now says *"2 non-book moves judged · 3 book/forced
  left out · 1 not graded"*, the AI strip's tooltip says the same, and the
  note under the breakdown says moves played while grading was off are not
  counted until *Grade missing moves* grades them. `summarizeSide` carries
  `excluded` and `ungraded` for it.

### Layouts checked

1366x768 and 1280x720 through the whole loop (the correction card scrolls
inside its panel at those heights, which is the panel's job; the practice
board is whole and on screen); 1920x1080 and 420x860 for the endings and
the Review tab; plus `layout-stress.mjs` across ten viewports and six zoom
levels.

### Remaining risk before more testers

- **The diagnosis coach is model-dependent.** One of two live runs fell
  back to the engine card because every model in the chain failed on that
  request. The fallback is honest and now worded right, but a tester who
  hits it twice in a row will reasonably think the coach is broken. The
  chain and its timeouts are `GEMINI_*` env knobs (§5).
- **Practice positions are only offered when the bank has one for the
  theme** and the engine re-certifies it; the "No practice position yet"
  path is honest but a dead end for that card.
- **The loop probe's game is not deterministic.** It has always produced a
  hung piece worth ≥5 and a mate inside 40 plies; if the coach ever declines
  the bait the probe's later checks do not run.

---

## 34. Readability and scroll architecture — every panel reachable

Barry noticed *"Correction Cards are slightly out of view."* That one card
was a symptom of two structural holes, both fixed, and the fix is now
measured by an audit that runs over every tab of every mode at every
viewport and zoom the stress probe covers.

### Root cause

The side panel in every mode is a fixed-height box whose content box hides
overflow (`.rail-canvas-content` in Play, `.pm-canvas-inner` in Review,
`.sandbox-canvas-inner` in Learn). That is deliberate - it is what keeps a
long explanation from growing the row and pushing the composer off screen
(§11). The rule that makes it work is that **each tab body inside it must
own its scroll region** (`flex: 1 1 auto; min-height: 0; overflow-y:
auto`). Two bodies did not:

1. **`.corr-panel`** (Review's Correct tab) had no overflow rule at all,
   while its siblings `.pm-report`, `.pm-moves` and `.pm-chat-log` each
   did. A card taller than the panel - most of them once the evidence is
   open or the practice board is up - was cut at the panel's bottom edge.
   Worse, the practice step's `scrollIntoView` scrolled that *hidden* box
   programmatically (browsers allow it), so the top of the card went out of
   view too, and nothing the user could do brought it back. That is the
   "slightly out of view".
2. **Play's `.rail-canvas-inner`** was correctly `overflow-y: auto` in one
   rule and then overridden by a later `height: auto` rule whose comment
   said it was for the stacked layout - but it sat outside the media query.
   Its parent was also a plain block, so the inner's `min-height: 0` meant
   nothing. Result: Play's Review and Actions tabs grew to their content
   and lost their bottom controls - *Grade missing moves*, *Guided Play*,
   *Board size*, *Clear chat* - at 1280x720 and 1366x768 and at 125% zoom.

### What changed

- `.rail-canvas-content` is a flex column; `.rail-canvas-inner` is THE
  scroll region of Play's panel, in every layout (`ChessBoard.css`).
- `.corr-panel` scrolls like its siblings (`CorrectionPanel.css`).
- Learn's Line, Board and Actions bodies and Review's Actions body are
  content-sized, shrinkable and scrollable (`shell.css`) - a long line in
  Learn was the next thing that would have clipped.
- `html { scroll-padding-top }` equal to the sticky header's height (80px;
  116px when the header stacks under 760px), so anything the browser
  scrolls to the top edge - a focused control, an anchor, `scrollIntoView`
  - lands below the header rather than under it (`App.css`).
- Scrollbars were already the theme's (thin, `--border-strong` thumb,
  `obsidian.css`); no new chrome, no new colours, no new wrappers.

### The audit (`tools/verify/layout-stress.mjs`)

`reachability(root)` takes every visible control (button, input, select,
textarea, link) and text block inside a panel and, for each: (1) walks up
for an `overflow: hidden/clip` ancestor it has outgrown with no scrolling
ancestor in between - **clipped**; (2) brings its scroller onto the page
(clear of the sticky header) and itself into view, then hit-tests inside
the part of it that is showing - **covered** if something else is on top.
Intentionally ellipsised one-line labels are judged on their box. It runs
for every tab of every mode in every viewport/zoom case, and again in a
second page with long content served deterministically: a long coach
explanation with a Watch out section in Play's chat, and the whole
correction lifecycle in Review (intent, a long card, evidence open,
practice board, hint, an attempt), each at Auto and at Large board size.
`--quick` runs five cases in ~3 minutes; the full sweep takes ~8.

### Known, deliberately left

- **Leaving the Correct tab abandons a correction in progress**
  (`CorrectionPanel` is unmounted; §30's `correction_flow_abandoned`
  fires). Board size lives in Actions, so "change the board size while a
  card is open" cannot be done without losing the card. Not changed here:
  it is a product semantic, and the user has been asked. The audit sets
  the size *before* the flow for that reason.
- At laptop heights the card and the report scroll inside their panel; the
  panel is the height of the board column by design (§11). Both are
  reachable; neither is all on screen at once.
- A run of this probe against `public` writes guest rows (every context is
  a new guest); point the dev backend at a disposable schema first, as the
  QA note in AGENTS.md says.

---

## 35. One board size in all three modes

The user noticed Review's *Large* board was smaller than Play's and Learn's.
Measured at Auto / Large / Small before the fix: 1366x768 Review 240 / 325 /
240 against Play 330 / 440 / 330; 1920x1080 Review 527 / 637 / 527 against
620 / 730 / 530. About 100px short at every setting.

Two causes, both in `PostMortem.tsx`:

1. The **"Build improvement profile" card sat under the whole layout** in the
   loaded review. `useFittedBoardSize` removes *all* page overflow by
   shrinking the board, so those ~100px of page below the board column came
   straight off the board - the fitter doing exactly its job on the wrong
   thing. The card is still on the empty canvas (where it is the second
   thing a visitor wants); in a loaded review it is a row in the **Actions**
   tab (`.actions-profile-link`, styled as an action button).
2. `useBoardSizing(boardColumnRef, 360)` capped Review's ambition at
   `height - 360`; Play passes 180 and its column carries the same furniture.
   Review passes 180 now.

After: 1920x1080 all three modes 620 / 730 / 530; 1366x768 Review 348 / 458
/ 348 against Play 330 / 440 / 330 (the fitter measuring slightly less
column chrome in Review). `scratchpad`-style measurement: read `--board-size`
on `.chess-container`, `.sandbox` and `.pm` after switching modes.

---

## 36. The latency pass — faster without sending the model one byte less

The brief: cut responsive latency, keep every piece of context the coach is
given and every piece of the coaching it gives back. Everything here is
sequencing, connection management, or moving work nobody was waiting for off
the path of work somebody was. **No prompt, payload, model instruction or
response was shortened.** The one model change - which model leads the
diagnosis chain - was the user's explicit choice, with the trade written down
below.

### Measure first: two probes, kept

`tools/latency_probe.py` drives the real API (`:8081`) flow by flow and
prints what each stage cost; `tools/latency_browser.mjs` does the same from a
real browser on `:3001` - click, piece drawn, thinking shown, reply on the
board, explanation in the chat, tab switch while thinking, the Play → Review
handoff. Both are diagnostics, not tests; they spend real Gemini quota, so
run them deliberately (`--flows play,review`, `--moves 4`). The backend's
own numbers are the `⏱` lines in `/tmp/backend.log`: `ai_move` now also
reports `queued=` - how long the AI waited for the engine.

### What the profile said (dev stack, depth 15, one Neon round trip ≈ 145ms)

| flow | stage | before | after |
|---|---|---|---|
| Play | `/api/move` server ack | **306ms** median (max 632) | **4ms** |
| Play | move → AI reply on the board | **2204ms** median | **1468ms** median (Gemini 600-1000ms of it) |
| Play, guided | move → AI reply | 2528ms | 1952ms |
| Play | your own piece drawn after the click (browser) | 290ms | 160ms |
| Review | `/analyse` ack | 302ms | 4ms |
| Review | whole-game scan, 33 plies | **7315ms** (engine 1111ms) | **1489ms** |
| Play → Review | click → Review mounted → 9-ply scan done → report | not measured before; each ply carried a 145ms blocking write | 160ms → 316ms → 331ms |
| Correction | `/diagnose` | 11534ms (10620 in the model) | 5.5-8s, see below |
| Practice | `/practice/start` | 154ms | 4ms |
| Learn | `/session` from FEN | 4ms | 3ms |

**The engine was not the problem, and neither was the model, mostly.** Three
things were:

1. **Order.** After a human move the server graded the move (two depth-15
   searches), refreshed the eval bar (a third), wrote the learning row to
   Neon, answered the request, *and only then* scheduled the AI. The AI's own
   ranking then queued behind whatever grade was still running. 300-600ms per
   move of engine time spent on decoration before Gemini had been asked
   anything - while Gemini's own 0.6-3s of thinking, during which the engine
   sits idle, was where all of that work fits for free.
2. **Synchronous instrumentation on the event loop.** `learning_events.emit`
   and `move_grade_audit.record` each did a Neon INSERT inline. The Review
   scan does one per ply: 33 × ~150ms = 5.6s of a 6.8s scan, every one of
   them blocking every other request in the process. `/diagnose` did six or
   seven. Every AI move did two between the move landing and the eval bar
   refreshing.
3. **A model that thinks.** The diagnosis chain led with `gemini-3.6-flash`,
   which answered in 10.6s (§7 had already measured it at ~8s and hanging).

### What changed

**The engine gate** (`stockfish_service._EngineGate`). The engine's RLock is
now a re-entrant lock with priority: a caller that says so goes ahead of
every non-priority caller already waiting, and `stockfish_service.priority()`
holds the engine for a run of searches with nothing slipping in between.
`decide_ai_move` runs both stages inside one such section. Non-priority
callers (grades, eval refreshes, the Review scan, the profile worker) wait
while a priority caller is waiting. Starvation is bounded by the product:
there is one priority section (~0.5s) per player per move. `test_latency_paths`
holds the ordering.

**`/api/move` answers first.** The AI's task is created before the grade is
even submitted (with an `await asyncio.sleep(0)` so its engine work reaches
the gate first); the grade and the eval refresh run behind it, while Gemini
thinks; the learning row is written by a detached `_settle_player_move` once
the eval it needs exists. The response carries the previous eval; the bar and
the grade badge arrive through the status poll that is already watching for
the AI's move - `startAiMovePolling` now carries `eval` and `history` across
on every tick, not only on the tick that saw the move. The AI's own record
reads its `eval_before` from an `eval_ready` future the settle task resolves.

> **The one bug this found.** `make_ai_move_async` used to *skip* when its lock
> was held. Once the AI was scheduled the instant the player's move landed,
> a second move played the moment the AI replied found the previous task
> still writing its record - and the reply was silently skipped, leaving the
> board on the player's move until the 90s deadline. It now waits for the
> lock (the turn/position checks inside are what prevent a double move, not
> the skip) and releases it before the eval refresh and the record. Tested.

**`refresh_eval_async(s, fen)`** pins an evaluation to the position it was
asked for and discards it if the board has moved on - the search runs behind
the response now, so a reset or a fast reply can land first. Whoever changed
the board refreshes the bar for the new position.

**`db_writer.py`.** One daemon thread, one FIFO queue. `learning_events`
and `move_grade_audit` queue their INSERTs there; `link_correction` queues its
UPDATE on the same queue so it runs after the row it updates. A failing write
is logged at WARNING with its label; a full queue (2000) drops and counts
rather than blocking. `has_prior` checks the in-memory buffer before the
database, `funnel` flushes before it reads, shutdown flushes before closing
the pool. The game record itself - `record_move`, `finalize_game`,
`reweight_candidates` - is **not** on this queue: those are read back, so they
stay synchronous, but every async caller now runs them via
`asyncio.to_thread` instead of on the loop.

**The diagnosis chain** (`diagnosis_service.py`) leads with `gemini-3.5-flash`,
uses the pooled `gemini_http` connection like every other caller (it was the
last one opening a client per call, with the key in the URL), and hedges
after `GEMINI_DIAGNOSIS_HEDGE_DELAY` (6s). Measured on the real payload:

    gemini-3.5-flash          5.4-12.8s   thoughtsTokenCount ≈ 1100
    gemini-3.6-flash         10.7-12.8s   thinks
    gemini-flash-lite-latest  1.3-1.8s    no thinking
    gemini-3.5-flash-lite     1.2-1.5s    no thinking

The big models *think* for a diagnosis and the lite ones do not - and the
lite diagnosis was visibly shallower on the same position (it filed a missed
forcing capture under "premature attack"). So the hedge is 6s, not the move
service's 1.4s: a normal 3.5-flash answer wins, and the 10-13s tail is capped
at about 7.5s by a lite answer. **A shorter hedge would quietly make the lite
model the usual diagnoser.** The user was shown this trade and decided:
*keep the 6s hedge, do not lead with a lite model, and do not cap the
thinking budget unless a side-by-side first proves the diagnoses stay just as
good.* That is a standing decision, not a default to revisit for speed. For the same reason the lead is only demoted
when it *fails*, never when it loses a race. The move service does demote
on a fallback win (`_last_good_model`); that is older behaviour and left
alone. Note the diagnosis lead is now the move chain's lead too, which §5
argued against; one diagnosis per correction against a move every few
seconds was judged an acceptable share of one model's quota, and a 429 falls
through to the next model.

**Frontend.** `animationDuration` 300 → 150 on all four boards (the user's
choice; it was the first 290ms after every click). `keepIfSame` in
`ChessBoard.tsx`: every `/api/status` read used to replace eval, history and
transcript with fresh-but-equal objects and re-render the mode up to eight
times a second while the coach thought; a structurally equal value now keeps
its reference and React skips the render. Review's scan poll ramps
(250/600/1200ms) instead of a flat 1200ms, and keeps looking for a few polls
when the first read lands before the scan has started.

### What did not change, on purpose

- Every prompt, every field in every payload, every response. Guided Play's
  facts, the FEN history, the candidate window, the engine facts, the grade
  provenance - all as before. `test_guided_play_api` asserts standard mode is
  byte-identical.
- Depths, MultiPV, `NOISE_MARGIN`, the staged search. §7 stands.
- Rate limits, the middleware stack (§28 asserts its order - which is why the
  route timing went into the routes rather than into a middleware).

### Remaining risks, measured or seen

- **Gemini's tail.** The move service still saw 8-13s replies during this
  pass: two models timing out at 6s each before a third answered, or a 503.
  The hedge caps it; nothing here can remove it. `GEMINI_MOVE_MAX_IN_FLIGHT`
  and `GEMINI_MOVE_TIMEOUT` are the knobs, and each one costs quota.
- **Neon.** One DNS blip during this pass (`Temporary failure in name
  resolution`) wedged the pool for good - trap 15, restart the backend. With
  `DATABASE_POOL_MAX` at 5, the 5-second `/api/learning/summary` poll (two
  queries) plus the writer plus the game record share five connections; the
  writer makes the instrumentation no longer *block* on a slow database, but
  a dead one still stalls `record_move` and `start_game`.
- **The grade badge is later than it was** by design: ~1s after your move
  (behind the AI's stages) instead of ~300ms. It still lands before the reply.
- **`/api/reset` is ~200-300ms** (a learning-game row in Neon plus an eval).
  Left as is: the row's id is needed before the first move can be recorded.
- **`queued=` in the `⏱ ai_move` line is not zero when a move follows the AI's
  reply within ~300ms** - the previous reply's own grade or eval still holds
  the engine. A human is slower than that; the probes are not.


---

## 37. Opponent profiles — the 1–20 slider is gone

**The complaint:** a tester met *"Gemini, 99% accuracy"* at the default
setting, and the low settings were not weak players, they were the three
*worst* legal moves on the board - `select_candidates_by_difficulty` slid a
3-move window along Stockfish's ranked list, so level 1 was the bottom of
the list: hang the queen, walk into mate, abandon the king. Nobody has
played that opponent.

**The model now:** seven profiles, ids `beginner casual improving club
advanced expert master`, ~400 to ~2400. `opponent_profiles.py` is the table;
`chess-frontend/src/opponentProfiles.ts` mirrors `id/label/approxElo/blurb`
and `test_opponent_profiles.py` parses the `.ts` and fails on drift. The UI
says *"Club — about 1500"* and never claims an exact Elo. Default `club`;
`get_profile()` never raises - `None`, an old integer, a typo all mean club,
logged once.

### The pipeline (`decide_ai_move`)

1. Stage 1 as before: one shallow MultiPV search orders every legal move.
2. `candidate_selection.annotate()` - python-chess facts per move: `cpl`
   against the best (mates on a ±10000 scale), `gives_check`, `is_capture`,
   `hangs_piece` (a minor or better of ours left en prise), `answers_threat`,
   `walks_into_mate`, `forced`, `rank`, `san`.
3. `candidate_selection.select_pool()` - the profile's pool of three:
   - *seeing the mate*: when the best move mates, a profile below expert
     sees it with probability `tactical_awareness`; when it does not, the
     mating moves are off its board and the rest are re-based. (Without this
     a beginner could never miss a mate in one - every other move is 9000cp
     worse and a softmax would never offer one.)
   - *eligible*: `cpl ≤ max_cpl`. A pool of one is a real answer; only when
     nothing qualifies are the least bad three taken.
   - *blunders*: a move that hangs a piece or walks into mate survives with
     probability `blunder_tolerance`; walking into mate survives only for
     beginner/casual.
   - *weights*: `exp(-|cpl − target_cpl| / temperature)` - **around the
     profile's typical cost, not down from zero.** Weighted from zero, a
     three-move pool always holds a near-best move and the fallback (and an
     eval-reading Gemini) plays it: measured "beginner" at 22cpl mean. Then
     three bends: checks, non-pawn captures and threat answers are pulled up
     by `1 + 2·(1 − awareness)`; a *quiet* threat answer is pulled down by
     `awareness` for the three weak profiles; the engine's own top move,
     when not obvious, is pulled down by `awareness` for beginner/casual.
   - sample three without replacement; each entry carries its `weight`.
4. Stage 2 deep-searches the pool **plus the engine's best move as a hidden
   reference** when it was not sampled, so the recorded `cpl` is
   deep-accurate; `refine_candidates` now merges the annotation keys onto
   the refined entries and re-derives `cpl`. The reference is stripped
   before anything reaches Gemini, the panel or the fallback.
5. Gemini, one call, the prompt now carrying *"You are playing as a Beginner
   opponent, roughly 400 strength. {commentary_style} … If a move delivers
   checkmate, play it."* and, for beginner/casual/improving, *"Do not simply
   take the highest evaluation."* The list is still the ONLY allowed moves;
   `_parse` still refuses anything else.
6. Fallback = **the highest-weight pool move** (the one this profile is most
   likely to play), not the pool's strongest - otherwise every fallback
   quietly plays above the chosen level. Reasons recorded: `no_llm`,
   `gemini_error`, `gemini_invalid_move`, `gemini_failed`.

`decide_ai_move` returns a **fourth value**, the decision record: profile,
Elo, `rank_in_pool`, `rank_overall`, `cpl`, `n_candidates`, `n_eligible`,
`selected_by`, `fallback_reason`, depths, `engine_ms`/`llm_ms`, `model`.
`app._decision_props()` puts the allowlisted slice on
`ai_move_explanation_generated` / `guided_watchout_generated` (Play, guided
Play, and now AI vs AI with `source_mode="AI vs AI"`). Never a move, FEN or
text. `learning_events.ALLOWED_PROPERTIES` lost `difficulty` and gained
`opponent_profile approx_elo rank_in_pool rank_overall cpl n_candidates
n_eligible selected_by fallback_reason rank_depth search_depth`.

### Guided Play

`guided_play.candidate_facts` gained `own_loose` - the mover's *other* pieces
left en prise - and `describe_candidates` says so. For the three weak
profiles the line-3 rules add: the coach *may* say its own move left
something loose ("that is what such a player notices one move too late"),
still never naming the move that punishes it. Club and up get the unchanged
rule.

### Everything that carried the integer

`PlayerSession.opponent_profile` (was `ai_difficulty`), `SandboxSession.profile`,
`GET/POST /api/difficulty` (`{profile, label, approx_elo, blurb, profiles:[…]}`;
POST `{profile}`; unknown id → `success:false` with `allowed`), every payload
that said `difficulty` now says `opponent_profile` + `approx_elo`
(`/api/status`, `/api/ai-move`, `/api/set-color`, sandbox `/session`), PGN
`AIStrength "Club (~1500)"` + new `OpponentProfile "club"`, chat contexts
(`opponent_level`), the scenario generator asks the model for a profile id,
`postmortem_api` branches at `master`, `learning_service.record_move(...,
opponent_profile=)` into the new column. The pure `archive/patches/*` were
not touched.

### Does it work - `tools/profile_sanity.py`

Engine-only (no key: the fallback plays the highest-weight pool move), the
profile as White against master, 2 games × 30 plies, cpl capped at 1000:

| profile | mean cpl | median | top-move share |
|---|---:|---:|---:|
| beginner | 363 (one walk into mate) | 80 | 10% |
| casual | 80 | 85 | 13% |
| improving | 46 | 40 | 13% |
| club | 21 | 20 | 10% |
| advanced | 10 | 0 | 57% |
| expert | 6 | 0 | 60% |
| master | 2 | 0 | 97% |

Strictly ordered, no illegal move. Re-run after changing any knob in
`opponent_profiles.py`; the knobs are exactly `max_cpl target_cpl
temperature tactical_awareness blunder_tolerance` and nothing else.

### Open

- The knobs are a first pass. Club at 21cpl mean is about right for ~1500;
  casual/beginner may want a higher target once real games are played -
  Gemini, seeing the evaluations, tends to take the pool's best, so the
  live level sits a little above the sanity numbers.
- The numbers above are engine noise at 2 games. `--games 5` before trusting
  a difference of ten centipawns.

---

## 39. Advanced Coach Settings / Behavioral Space (2026-09-13)

The Actions panel in Play, Learn and Review now includes **Coach style →
Advanced…**. It opens one shared responsive modal with a two-dimensional
point, precision sliders, reset, and five built-in style presets. X is
directness and Y is creativity, both 0–10 and defaulting to 5. The graph,
sliders and presets write the same state immediately; the point also supports
arrow keys (Shift = a whole point). Escape, backdrop click, Done and the close
button all dismiss the dialog.

Guest values live under `chess-coach-bluntness`, `chess-coach-creativity` and
`chess-coach-style-preset`. They use the existing `preferences.ts` bridge, so
signed-in values are also allowlisted in `settings_service.py` and follow the
account. Missing, non-numeric and out-of-range coordinates resolve to balanced
5,5. The frontend mapping and preset list are in `coachBehavior.ts`.

`coach_style.py` is intentionally chess-free. Each axis is cut into **five
bands** - `band()`: 1–2 extreme low, 3–4 moderate low, 5–6 balanced, 7–8
moderate high, 9–10 extreme high, with the cut points halfway between (2.5,
4.5, 6.5, 8.5) - and every band has its own instruction block in
`DIRECTNESS` / `CREATIVITY`, not an adjective on the balanced one. The
extremes were widened on 2026-09-13 because the first mapping produced nearly
the same sentence at 1 and 10: each block now carries a required/forbidden
wording list (gentle bans imperatives and requires "One thing to notice";
blunt bans hedges like "keep an eye on"/"might" and requires a ≤10-word first
sentence plus a flat consequence; plain caps sentences at twelve words and bans
imagery; vivid *requires* one image tied to a real square or piece, within two
sentences and ~40 words) and a **tone example from a different position**
("a different position - copy the tone, never the content", a rook / back
rank, never e4) - those examples are the strongest lever on the live model
and are deliberately off-position so QA on the e4 position cannot be
contaminated. The block opens "Coach style settings (PHRASING ONLY…" and
closes with the respect rule, the banned-wording list and the no-invention
rule at every setting. `styleBand()` in `coachBehavior.ts` mirrors the cut
points so the modal's preview and the prompt switch voice at the same
coordinate.

Creativity maps to sampling **only on the explanation-only chat calls**
(`gemini_chat_service`): temperature 0.12 at 0 → 0.40 at 5 → 0.80 at 10
(piecewise linear), top-p 0.78 → 0.90. The combined Play move call still
sends no `generationConfig`; see below.

The modal shows a **live coach preview** (`coachPreview()` - a 5×5 matrix of
one fact, "your e4 pawn is under pressure", said 25 ways, plus a voice label
such as "blunt · vivid") that changes as the point is dragged. Presets:
Balanced 5/5, Gentle Explainer 1.5/3, Blunt Tactician 9.5/2, Creative Mentor
3/9, Sharp Story Coach 8.5/8.5, Minimal Analyst 7/1 - style points, not
characters. Live check on 1.e4 e5 2.Nf3 (Black plays Nf6 in every cell):
1/1 *"I'm developing my knight and attacking your e4 pawn. It may be worth
checking how your e4 pawn is protected."*; 10/1 *"I'm attacking your pawn on
e4. Defend it or you lose material."*; 10/10 *"…that pawn is the keystone of
your center: if it drops, your whole structure sags and you lose
material."* Known tuning risk: the model latches onto nouns from the vivid
example, and gentle+vivid is the cell most prone to drifting long.

Style is passed to `GeminiMoveService` only as a clearly labelled
**phrasing-only** block for the explanation in its combined move/reason call.
The move call's temperature/top-p are deliberately unchanged: applying the
creativity sampling values there could change which candidate wins. Stockfish,
candidate selection, grading, Correction Card diagnosis, PGN/FEN and practice
generation remain outside the style path; the selected UCI is still accepted
only when it belongs to the prevalidated profile pool.

Verification: `test_coach_style.py` (72) owns the bands, the gap between the
extremes (shared vocabulary under 20%, opposite required/forbidden lists),
sampling, safety, grounding, the Play/Guided prompt integration (evidence
block byte-identical across all styles, style between profile and output
constraints) and account validation; `tools/verify/coach-style.mjs` (27) owns
open/close, point drag, slider sync, the four preview quadrants (distinct,
same fact, low shared wording, no banned words), the six presets, reset,
refresh persistence, desktop and narrow layout. The
existing UI, overlap, layout-stress, interaction and Guided Play suites remain
the regression gates.

---

## 40. Play move speaker stance (2026-09-13)

`gemini_move_service._build_prompt()` now has five literal blocks in this
order: `SPEAKER IDENTITY`, `CHESS EVIDENCE — AUTHORITATIVE`, `OPPONENT
PROFILE`, `COACH STYLE — PHRASING ONLY`, and `OUTPUT CONSTRAINTS`. The model
is the opponent who is choosing now and, on line 2, the opponent who just
moved. It is explicitly told to use first person/present tense and never say
“As Black/White”, speak hypothetically, narrate a color in third person, or
name the AI/bot/Gemini as actor.

Every candidate gets the existing engine score plus `guided_play`'s
python-chess facts even when Guided Play is off. This gives the standard
explanation the same factual check/attack/loose-piece evidence the Watch Out
section already had. FEN, SAN history and the complete prevalidated shortlist
remain present. The profile commentary strings now describe sophistication,
not characters; no profile changes its numeric selection knobs.

`sanitise_move_explanation()` runs only after `_parse()` has found a candidate
UCI. It narrowly removes known stance/tone failures (`As Black/White`, `If I
were`, own-side `would`, “gladly punish/exploit”, “self-destruction”, “bizarre
opening”, stupid/terrible/pathetic, “invited disaster”). It does not generate
or rewrite chess content and cannot touch the chosen move. The prompt remains
the primary enforcement; the sanitizer is a final denylist, not a second
coach.

The browser now sends `{bluntness, creativity}` with `/api/move`,
`/api/ai-move`, `/api/set-color` and `/api/ai-vs-ai/start`; `PlayerSession`
holds the bounded pair so chained AI moves keep the style. Creativity changes
the prompt's phrasing direction but not this combined call's generation
temperature/top-p. Open-ended coach chat remains on the separate chat service,
where the safe sampling mapping does apply.

Tests: `test_move_speaker_stance.py` covers block separation, first-person and
respect rules, FEN/history/candidate preservation, all seven profiles, both
Guided states, high-direct/high-creative style text, sanitizer samples, parser
cleanup and Watch Out separation. Existing Gemini move, Guided Play and
opponent-profile suites cover shortlist validation and unchanged selection
knobs.

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).
