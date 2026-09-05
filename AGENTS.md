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
4. **Never print `.env` or the `GEMINI_API_KEY`.** No `echo $GEMINI_API_KEY`,
   and beware `${VAR:-...}` fallbacks, which print the value when you meant to
   test for it. The key has been leaked into a terminal once already and had to
   be rotated.

## Shared resources, all of which are singletons

One Stockfish process behind one lock, one Gemini API key across six model
chains, one dev stack on `:3001` / `:8081`, one Docker stack on `:3000` /
`:8080`, one venv at `/tmp/chessapp`. The runner scripts begin with `pkill`, so
**restarting the backend kills the one another agent is using** — check with
`pgrep -af 'port 8081'` and reuse a healthy server instead.

## Where things are

| | |
|---|---|
| The full guide | `CLAUDE.md` — long, and every part of it was paid for |
| Design system | `OBSIDIAN_DESIGN.md` — read before touching any CSS |
| Deployment | `DEPLOY.md` |
| Backend tests | ten suites, listed in `CLAUDE.md` §6 |
| Frontend invariants | `node tools/verify/ui.mjs` — 58 checks against the running app |

## How the user works

Evidence, not claims. Concise replies, solution first. Ask when uncertain
rather than executing blind. Say it loudly and up front when something has
degraded — especially the LLM dropping out of the move loop. Verify in the
running app, not only in tests.
