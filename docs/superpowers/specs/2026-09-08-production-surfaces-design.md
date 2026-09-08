# Production surfaces — design

**Date:** 2026-09-08
**Branch:** `production-surfaces`, off `master` (accounts deployed)
**Status:** approved, implementing

This is **Scope A** of the "Longitudinal Learning + Production Readiness"
milestone. The milestone decomposes into five subsystems and this is the only
one with no dependencies:

| | subsystem | depends on |
|---|---|---|
| **A** | **production surfaces — this document** | nothing |
| B | bulk PGN import + persistent storage of imported games | accounts |
| C | asynchronous analysis pipeline | B |
| D | recurring-pattern detection + Improvement Profile | C |
| E | correction cards → practice → retest → player model | D |

B through D get their own spec. E is the long-term thesis and is deliberately
not designed yet.

## What this is, and what it is not

Accounts are live in production. That changes what the product owes a person:
somewhere to read what is kept and for how long, an honest account of what
deleting an account does, and messaging that does not promise a password reset
the deployment cannot perform.

**Out of scope, by explicit decision:** privacy policy, terms of service, cookie
notice, contact page. The first three need a named data controller, a
jurisdiction and a contact address; none of those are facts this codebase
holds, and a policy written around invented ones is worse than no policy. The
contact page was dropped because there is no support address yet and a contact
form is unbuildable while email is off — it would silently discard messages.

The footer is built so that adding the legal pages later is two links and two
routes, not a redesign.

## The layout constraint that shapes this

**No footer inside the app shell.** The three-mode shell is height-fitted:
`useFittedBoardSize` measures the space the board is allowed to occupy, and
CLAUDE.md traps 8 and 11 each record an occasion where adding an element to
that layout pushed the board frame out of its own column — once under the
coaching tab strip, once into the analysis panel.

So the footer appears only on surfaces that already scroll and have no board:
`/signin`, `/signup`, `/settings`, `/about`, `/forgot-password`,
`/reset-password`. On the main app, About and the build version go in the
account dropdown, which exists already.

This is not a compromise. It costs nothing a user would notice and it means
this change cannot regress the layout invariants at all.

## Components

### `pages/About.tsx` — new, route `/about`

Two jobs, and no marketing:

1. **What Zugzwang is.** Stockfish proposes and Gemini decides — the product's
   actual mechanism, in a paragraph. The three modes, named, so someone who
   has only used Play knows Learn and Review exist.
2. **"Your data" — the retention explanation.** Facts the codebase already
   enforces, written down where a person can read them:
   - guest history lives 30 days (`GUEST_RETENTION_DAYS`) unless an account
     claims it first, then it belongs to the account
   - account data lives until the account is deleted
   - session tokens and password-reset tokens are stored hashed only, never in
     the clear
   - what is deliberately **not** saved: sandbox sessions, Post-Mortem
     reviews, learning-loop corrections and practice attempts. All in memory,
     all gone on restart. Saying so matters more than the rest of the page,
     because the app currently implies more permanence than it has.

### `components/SiteFooter.tsx` — new

About · version. One line, quiet, on the pages listed above.

### `Settings.tsx` — changed

- A **"Your data"** card carrying the same retention facts as About, because
  the person most likely to want them is the one already looking at their
  account.
- **Deletion wording** that enumerates what goes — the account, every game and
  every move on it, saved preferences, and every signed-in session — and says
  it happens immediately and cannot be undone. The typed-username confirmation
  is unchanged; it already works and is the right shape.

### `AuthPages.tsx` (signup) — changed

When email is unavailable, say so where the address is collected: the field
stays, and the note says password recovery is not available yet. Collecting an
address while implying a recovery path that does not exist is the specific
dishonesty this fixes.

### `PasswordReset.tsx` — changed

Show the unavailable notice **before** the form is submitted rather than after.
Today a person types their address, waits, and is then told the mechanism is
off.

### `auth_api.py` — changed

`/api/auth/config` gains `email_available: bool`.

> **This is safe, and the reason is worth stating.** `/api/auth/forgot-password`
> must answer identically for a registered address, an unregistered one, a
> malformed one, a Google-only account and a dead provider — every difference
> is a free way to check whether somebody has an account here. `email_available`
> does not touch that: it is derived from **configuration**, before any address
> exists, and is the same value for every caller. It is the same fact the
> endpoint already returns as `email_available` in its own response, moved
> earlier so the UI can be honest up front.

### `app.py` — changed

`/api/health` gains `version`, read from a `BUILD_SHA` environment variable and
defaulting to `"dev"`. Unauthenticated, so it carries the short SHA and nothing
else — no branch name, no build host, no timestamps.

### `vite.config.ts` — changed

Inject the build SHA at build time from `VERCEL_GIT_COMMIT_SHA` (Vercel sets it
itself), falling back to `dev`. The footer reads it.

## Testing

- `tools/verify/ui.mjs` sweeps `/about` for the invariants it already owns:
  console errors, horizontal overflow, AA contrast on every text style, 44px
  touch targets under a coarse pointer, both themes.
- `test_accounts_postgres.py`: `email_available` appears in `/api/auth/config`,
  tracks configuration in both directions, and — the one that matters —
  `/api/auth/forgot-password` still answers identically for a registered and an
  unregistered address with it present.
- Frontend typecheck is `npm run build` (`tsc -b`), never a bare `tsc --noEmit`.

## What this deliberately does not do

No cookie banner (the two cookies are strictly functional — identity and
session — and there is no analytics or advertising cookie to consent to). No
account-export. No roles. No enterprise anything. Each would be a feature
invented to look finished rather than because a user is blocked without it.
