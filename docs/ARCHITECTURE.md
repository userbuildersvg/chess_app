# Architecture

How Zugzwang is put together, and which part is allowed to decide what. The
short version: **deterministic code owns the chess, the model owns the
words.** Everything below follows from that.

## The system

```
                    browser
        ┌──────────────────────────────┐
        │  React 19 + TypeScript (Vite)│
        │  Play · Learn · Review       │
        └───────────────┬──────────────┘
                        │  same origin: the browser only ever
                        │  talks to the Vercel URL
                        ▼
        ┌──────────────────────────────┐
        │  Vercel (static frontend)    │
        │  rewrites /api/* ───────────────┐
        └──────────────────────────────┘  │
                                          ▼
                       ┌─────────────────────────────────────┐
                       │  Render — FastAPI (Python 3.11)     │
                       │                                     │
                       │  beta gate → CSRF → rate limit →    │
                       │  identity → route                   │
                       └───┬───────────┬───────────┬─────────┘
                           │           │           │
              ┌────────────┘           │           └─────────────┐
              ▼                        ▼                         ▼
   ┌────────────────────┐   ┌────────────────────┐   ┌────────────────────┐
   │ Stockfish          │   │ Gemini             │   │ PostgreSQL (Neon)  │
   │ (local binary)     │   │ (HTTP, pooled)     │   │                    │
   │                    │   │                    │   │ accounts, games,   │
   │ legal moves, eval, │   │ explanation,       │   │ moves, saved       │
   │ best move, cpl,    │   │ narration, and a   │   │ lessons, practice  │
   │ the whole-game     │   │ move CHOSEN FROM   │   │ results, imports   │
   │ scan               │   │ Stockfish's list   │   │                    │
   └────────────────────┘   └────────────────────┘   └────────────────────┘

                       ┌─────────────────────────────────────┐
                       │  RevenueCat (Web Billing, SANDBOX)  │
                       │  paywall in the browser;            │
                       │  entitlement verified server-side   │
                       └─────────────────────────────────────┘
```

Two things worth reading twice:

- **The browser never calls Render directly.** `chess-frontend/vercel.json`
  rewrites `/api/*` server-side, so the API is first-party and the identity
  cookie is a first-party cookie.
- **Stockfish proposes, Gemini decides.** For an AI move the engine ranks the
  legal moves, an opponent profile samples a few a player of that strength
  might consider, and the model picks one from that list and explains it in
  that player's voice. The model never invents a move, a FEN, or an
  evaluation.

## The learning loop

This is the product. Everything else exists to serve it.

```
  a game you played
  (PGN upload · Chess.com / Lichess import · a game played here)
        │
        ▼
  REVIEW ─ every move graded by Stockfish; the decision the game
        │  turned on is picked deterministically (turning_point.py)
        ▼
  "what were you trying to do?"  ← asked BEFORE any diagnosis exists
        │
        ▼
  SAVED LESSON ─ your intention against what the position needed,
        │  filed under one of eight controlled themes. One lesson per
        │  theme, so a repeated mistake meets the same lesson again
        ▼
  PRACTISE ─ an engine-verified position testing the same idea,
        │  played on the main Review board
        ▼
  MY IMPROVEMENT ─ after ten analysed games, the mistakes that
                   actually recur, each with the games as evidence
```

Signed in, lessons and practice results are saved to the account. As a guest
they last for the session — see [Limitations](#limitations-that-matter-here).

## Request path

Every `/api` request passes through the same stack, outermost first:

| Layer | File | What it does |
| --- | --- | --- |
| Beta gate | `beta_gate.py` | Deny by default; a caller without an invitation gets 403 |
| Security headers / CSP | `app.py` | Set on every response |
| CSRF | `csrf.py` | Origin check on state-changing requests |
| Body ceiling | `body_limit.py` | Caps request size before parsing |
| Rate limit | `rate_limit.py` | Per-IP, on anything that spends CPU or model quota |
| Identity | `identity.py` | One opaque cookie-borne string: `guest:…` or `user:…` |

## Backend map

| Area | Files |
| --- | --- |
| Play | `app.py`, `game_logic.py`, `player_state.py` |
| Move choice | `stockfish_service.py`, `candidate_selection.py`, `opponent_profiles.py`, `gemini_move_service.py` |
| Grading | `move_quality.py`, `move_feedback_log.py` |
| Review | `postmortem_state.py`, `postmortem_analysis.py`, `postmortem_api.py`, `turning_point.py` |
| Learn | `sandbox_state.py`, `sandbox_api.py`, `scenario_service.py` |
| Learning loop | `learning_loop.py`, `learning_loop_api.py`, `correction_history.py`, `retest_bank.py` |
| Improvement profile | `pattern_detectors.py`, `profile_service.py`, `profile_worker.py`, `profile_api.py` |
| Accounts | `auth_service.py`, `auth_api.py`, `identity.py`, `settings_service.py` |
| Imports | `external_games.py`, `imported_games.py`, `pgn_text.py` |
| Storage | `db.py`, `db_writer.py`, `migrations/` |
| Billing | `billing_api.py` (sandbox; see the README) |

## Frontend map

| Area | Files |
| --- | --- |
| Shell and modes | `src/App.tsx`, `src/pages/Home.tsx` |
| Play | `src/components/ChessBoard.tsx` |
| Learn | `src/components/Sandbox.tsx` |
| Review | `src/components/PostMortem.tsx`, `PostMortemReport.tsx`, `PostMortemMoveList.tsx`, `PostMortemChat.tsx` |
| Saved lessons | `src/components/CorrectionPanel.tsx` |
| Improvement profile | `src/pages/ImprovementProfile.tsx` |
| One reading of the position | `src/boardState.ts` |
| Board sizing | `src/hooks/useBoardScale.tsx`, `useBoardSize.ts`, `useFittedBoardSize.ts` |
| Design tokens | `src/styles/obsidian.css` (see `OBSIDIAN_DESIGN.md`) |

All three modes are mounted at once and hidden rather than unmounted: each
holds state — a game, a move tree, a conversation — that switching modes must
not destroy.

## What is deterministic, and what is not

| Question | Answered by |
| --- | --- |
| Is this move legal? | `python-chess`, server-side, always |
| How good was it? | Stockfish, at a stated depth, with the depth shown |
| Which decision mattered most? | `turning_point.py` — a rule over the scan |
| Which theme is this mistake? | `pattern_detectors.py` — twelve detectors, pure functions |
| What should the coach say? | Gemini, given the above as facts to quote |
| Which move does the AI play? | Gemini, choosing from Stockfish's shortlist |

If Gemini is unavailable, the app still plays, still grades and still
reviews; the wording becomes engine-derived instead. It is visible when that
happens (`source: "stockfish_fallback"`).

## Limitations that matter here

- **Billing is RevenueCat sandbox.** Stripe test cards only, no live payments,
  no webhooks, no enforced usage quotas.
- **Guest work is temporary.** Reviews, saved lessons and practice results for
  a guest live in server memory and are gone when the instance restarts or the
  session is swept; signing up claims the games played as a guest.
- **Review sessions are capped** (20, one hour idle) and sandbox sessions are
  in memory, so both end on a deploy.
- **The improvement profile needs ten analysed games** before it claims a
  recurring pattern, on purpose: one game is an anecdote.
- **Closed beta.** Every guarded route answers 403 without an invitation.
