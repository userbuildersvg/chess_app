# Zugzwang — agent instructions

**Read `CLAUDE.md` in this directory before doing anything else. It is the
source of truth for this repository, and it applies to every agent working
here, not only to Claude Code.**

This file is deliberately short. A second full copy of the project's
instructions would drift from the first one the moment either was edited, and
the drift would be discovered by whichever agent happened to trust the stale
one. So everything lives in `CLAUDE.md`; this is a pointer to it plus the
handful of things that are dangerous enough to state twice.

## The app is PRIVATE, and that will be the first thing that confuses you

Zugzwang is behind a closed-beta gate (`CLAUDE.md` §26). Every `/api` route
except health, the auth routes and `/api/beta/*` answers **403** to a caller
who has not redeemed an invitation, and the browser draws a landing page
instead of the board. It is **fail-closed**: with `BETA_ACCESS_REQUIRED` unset,
the door is shut.

So, in order of how likely each is to waste your morning:

1. **A test suite that drives the app must set
   `os.environ.setdefault("BETA_ACCESS_REQUIRED", "false")` before importing
   `app`**, exactly as the existing sixteen do. Without it every request in it
   is answered 403 by middleware before your route ever runs, and the failures
   read like the feature under test is broken.
2. **`tools/verify/*.mjs` needs the backend started with the gate off** —
   `setsid nohup env BETA_ACCESS_REQUIRED=false /tmp/run_backend.sh ...` — or
   the browser gets the landing page and your first `waitForSelector` times out
   on a board that was never going to render. The exception is
   `tools/verify/beta.mjs`, which tests the gate and wants it on.
3. **`BETA_CODE_PEPPER` must never change.** It keys the hash of every
   invitation, the database holds hashes rather than codes, so changing it
   destroys every outstanding invitation irrecoverably. It is set explicitly in
   `.env` and on Render and must be **identical** in both; it used to fall back
   to `SESSION_COOKIE_SECRET`, whose two copies turned out to differ, and that
   cost a deploy where every code was refused in production.
4. **There is no admin endpoint and there must not be.** Codes are managed from
   a shell with `tools/beta_codes.py`, which is deliberately not copied into
   the image.

## Before your first write

1. **Know which of the three builds you are looking at** — the section at the
   top of `CLAUDE.md`, which the user has asked twice to be the first thing
   every agent knows. Dev is `:3001` + `:8081`, the stable Docker build is
   `:3000` + `:8080` with its source baked into the image, and the deployed
   build is whatever is on `origin/master`. Work and verify on `:3001`.
   Confusing them has cost whole sessions.

   > This item used to say there was a mandatory stop-block question at the top
   > of `CLAUDE.md` about the post-mortem analytics feature, to be asked every
   > session. **There is no such block, and Post-Mortem shipped long ago
   > (`CLAUDE.md` §14).** The instruction outlived what it referred to and was
   > costing a pointless exchange at the start of every session. Checked before
   > removing, and recorded here so nobody restores it from memory.
2. **Another agent may be working in this tree right now.** There are no locks.
   Run `git status` and `git log --oneline -3` first; a dirty tree you did not
   dirty means somebody else is mid-task. Commit early, work on a branch, and
   see "Working alongside another agent" in `CLAUDE.md`.
3. **Do not push, merge to `master`, or deploy without asking.** `master` is
   what Render and Vercel serve. Local commits on a branch are expected;
   anything leaving this machine is not.
4. **The app now sends security headers, and a CSRF origin check refuses
   cross-site writes** (`CLAUDE.md` §28). Two things that will look like bugs
   and are not:

   * **A 403 with `"That request did not come from Zugzwang"`** means the
     `Origin` on an unsafe request is not in `ALLOWED_ORIGINS`. Serving the app
     from a new local port is the usual cause - add it to `_default_origins` in
     `app.py`, as `:4173` was added when Vite's preview server hit it.
   * **The dev server's CSP is deliberately weaker than the shipping one**
     (`vite.config.ts`): dev needs `'unsafe-inline'` and `ws:` for HMR and the
     React Refresh preamble. Do NOT reconcile them by loosening
     `vercel.json`/`nginx.conf`. To exercise the real policy in a browser,
     `npm run build && npm run preview` - that server sends the shipping set.

   Also: **do not add `Strict-Transport-Security` locally.** It is a two-year
   promise that poisons plain-HTTP localhost for every project on the machine.

   **And never start uvicorn without `--no-proxy-headers`.** uvicorn enables
   its proxy-header handling by DEFAULT and rewrites the client address from
   `X-Forwarded-For`, which makes `request.client.host` caller-controlled and
   silently defeats every rate limit in the app - unlimited password guessing,
   beta-code guessing and Gemini spend. An independent audit reproduced exactly
   that. Both Dockerfiles, the dev runner in `CLAUDE.md` §12 and
   `test_security.py` §5b all carry or assert the flag. If a client IP looks
   wrong, set `TRUSTED_PROXY_HOPS` - never remove the flag. `CLAUDE.md` §28.

5. **Never print `.env` or any secret in it.** That is no longer only the
   Gemini key: `.env` now also holds `DATABASE_URL` (with the database
   password in it), `SESSION_COOKIE_SECRET`, `BETA_CODE_PEPPER`,
   `MAILJET_API_KEY` and `MAILJET_SECRET_KEY`. The pepper is the one whose
   loss is unrecoverable rather than merely expensive — every outstanding beta
   invitation is a hash taken under it. No `echo $GEMINI_API_KEY`, and beware `${VAR:-...}`
   fallbacks, which print the value when you meant to test for it. The Gemini
   key has been leaked into a terminal once already and had to be rotated.
   Check for presence by length, not by value: `v=$(grep -m1 '^KEY=' .env);
   echo ${#v}`.
6. **Never run the test suites against the live database.** They create
   accounts and games. Export a disposable schema first — `export
   DATABASE_SCHEMA="zwtest_$$"` — and drop it after; the exact block is in
   `CLAUDE.md` §6. Without it a run scatters rows into real user data.
7. **Post-Mortem browser verification now writes validation data.** Migration
   009 persists privacy-bounded `product_events` and reproducible
   `move_grade_audits`. Point the dev backend at a disposable schema for a
   browser probe when practical. If it is using `public`, record the exact
   synthetic game ids and delete only their linked event/audit rows afterwards;
   never truncate either table. Raw PGNs, intent/chat text, emails and
   credentials do not belong in analytics. See `CLAUDE.md` §30.

## Shared resources, all of which are singletons

One Stockfish process behind one lock, one Gemini API key across six model
chains, one dev stack on `:3001` / `:8081`, one Docker stack on `:3000` /
`:8080`, one venv at `/tmp/chessapp`, and — the most destructible —
**one Neon Postgres database holding real accounts, game history and every
beta invitation**. The invitations are the newest thing in it and the only
thing in it that cannot be reconstructed: they are stored as keyed hashes,
so a lost code is lost.

The runner scripts begin with `pkill`, so **restarting the backend kills the
one another agent is using**. Count real processes with

```bash
ps -eo pid,args --no-headers | grep "[u]vicorn app:app" | grep -v "bash -c"
```

not `pgrep -f 'uvicorn app:app'`, which also matches the shell command doing
the asking and reports phantom duplicates. That artifact wasted real time.

## Where things are

| | |
|---|---|
| The full guide | `CLAUDE.md` — long, and every part of it was paid for |
| Design system | `OBSIDIAN_DESIGN.md` — read before touching any CSS |
| Deployment | `DEPLOY.md` |
| Backend tests | **twenty-one** suites, **1,502 checks**, listed in `CLAUDE.md` §6. The full matrix passed on 2026-09-11 against a disposable schema, which was dropped. Several suites need `DATABASE_URL`; all but `test_beta_access.py` switch the beta gate off. `test_security.py` needs neither, so it is the one to run first when the change is a header, a cookie flag, a middleware or a limit |
| Frontend invariants | `tools/verify/ui.mjs` (118), `interaction.mjs` (127), `boardstate.mjs` (22), `beta.mjs` (49), `guided.mjs` (52, calls Gemini), `review-handoff.mjs` (87, calls Gemini), `loop.mjs` (51, calls Gemini), `layout-stress.mjs` (226), against the running app |
| Accounts and storage | `CLAUDE.md` §13; correction analytics and move-grade audits are in §30 |

## How the user works

Evidence, not claims. Concise replies, solution first. Ask when uncertain
rather than executing blind. Say it loudly and up front when something has
degraded — especially the LLM dropping out of the move loop. Verify in the
running app, not only in tests.
