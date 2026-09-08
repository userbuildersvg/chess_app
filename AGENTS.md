# Zugzwang — agent instructions

**Read `CLAUDE.md` in this directory before doing anything else. It is the
source of truth for this repository, and it applies to every agent working
here, not only to Claude Code.**

This file is deliberately short. A second full copy of the project's
instructions would drift from the first one the moment either was edited, and
the drift would be discovered by whichever agent happened to trust the stale
one. So everything lives in `CLAUDE.md`; this is a pointer to it plus the four
things that are dangerous enough to state twice.

## Before your first write

1. **There is a mandatory question at the top of `CLAUDE.md`** — a stop block
   about the post-mortem analytics feature. The user asked, emphatically, to be
   asked about it every session. Ask it before starting on whatever else you
   were given, even if that seems unrelated or urgent.
2. **Another agent may be working in this tree right now.** There are no locks.
   Run `git status` and `git log --oneline -3` first; a dirty tree you did not
   dirty means somebody else is mid-task. Commit early, work on a branch, and
   see "Working alongside another agent" in `CLAUDE.md`.
3. **Do not push, merge to `master`, or deploy without asking.** `master` is
   what Render and Vercel serve. Local commits on a branch are expected;
   anything leaving this machine is not.
4. **Never print `.env` or any secret in it.** That is no longer only the
   Gemini key: `.env` now also holds `DATABASE_URL` (with the database
   password in it), `SESSION_COOKIE_SECRET`, `MAILJET_API_KEY` and
   `MAILJET_SECRET_KEY`. No `echo $GEMINI_API_KEY`, and beware `${VAR:-...}`
   fallbacks, which print the value when you meant to test for it. The Gemini
   key has been leaked into a terminal once already and had to be rotated.
   Check for presence by length, not by value: `v=$(grep -m1 '^KEY=' .env);
   echo ${#v}`.
5. **Never run the test suites against the live database.** They create
   accounts and games. Export a disposable schema first — `export
   DATABASE_SCHEMA="zwtest_$$"` — and drop it after; the exact block is in
   `CLAUDE.md` §6. Without it a run scatters rows into real user data.

## Shared resources, all of which are singletons

One Stockfish process behind one lock, one Gemini API key across six model
chains, one dev stack on `:3001` / `:8081`, one Docker stack on `:3000` /
`:8080`, one venv at `/tmp/chessapp`, and — new, and the most destructible —
**one Neon Postgres database holding real accounts and game history**.

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
| Backend tests | fourteen suites, listed in `CLAUDE.md` §6. Several need `DATABASE_URL` and a disposable `DATABASE_SCHEMA` |
| Frontend invariants | `tools/verify/ui.mjs` (73), `interaction.mjs` (119), `boardstate.mjs` (22), against the running app |
| Accounts and storage | `CLAUDE.md` §13 — and its "If you are auditing this branch" block in §0 |

## How the user works

Evidence, not claims. Concise replies, solution first. Ask when uncertain
rather than executing blind. Say it loudly and up front when something has
degraded — especially the LLM dropping out of the move loop. Verify in the
running app, not only in tests.
