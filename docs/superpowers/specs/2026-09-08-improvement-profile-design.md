# The Improvement Profile — design

**Date:** 2026-09-08
**Branch:** `production-surfaces`
**Status:** approved, implementing

Scopes **B**, **C** and **D** of the longitudinal-learning milestone: bulk PGN
import with persistent storage, an asynchronous analysis pipeline, and the
first version of recurring-pattern detection. Scope **E** — correction cards,
practice, retest, the player model — is not built, but every seam here is
placed so it can be.

The goal is not storing games. It is saying *"you consistently…"* with evidence
behind it.

## What this is not

**Review does not change.** Upload a PGN, get an immediate analysis: that flow
is untouched, and this is a second experience reached from a button at the
bottom of Review. It is **not** a fourth top-level mode — the three-mode
navigation is deliberate and stays as it is.

## The constraints that shape it, measured rather than assumed

- **One Stockfish process behind one lock.** Every search in the app queues
  behind every other one. A bulk scan that does not yield starves live play.
- **Measured cost:** 0.16s per position at depth 12 locally, so ~13s for a
  40-move game; roughly 3× that on a throttled Render instance. Fifteen games
  is ~10 minutes of continuous engine time.
- **The free instance sleeps after ~15 minutes idle**, so a job held in memory
  dies mid-run. Every piece of job state is therefore a row, and the worker
  resumes from the database rather than from anything it remembers.

## B — storage

Migration `006_improvement_profile.sql`, two tables.

> ⚠️ **Imported games must NOT go in `games`/`moves`.** `reweight_candidates()`
> reads `owner LIKE 'user:%'` to steer what the AI plays against everyone. Put
> imported PGNs there and anyone could poison the global move pool by
> importing a pile of grandmaster games. `imported_games` is deliberately
> outside that query, and it is the single most important line in this design.

- **`imported_games`** — one row per PGN: owner, the PGN text, the headers
  worth showing (players, result, date, event), which colour the account
  played, ply count, and the analysis state machine
  (`pending → analysing → done | failed`).
- **`game_findings`** — one row per detected ply: game, owner, ply, theme,
  severity, centipawn loss, the position and both moves. This is the evidence,
  and it is what every claim in the profile is counted from.

There is no third table for the profile itself. **The profile is aggregated on
read** from `game_findings`, because a cached profile is a cache to invalidate
every time a game finishes analysing, and the aggregation is one grouped query.

`owner` is on both tables so a delete is single-table and every read filters on
it, exactly as `games.owner` already works.

## C — the pipeline

`profile_worker.py`: one asyncio task started at boot, alongside the retention
sweep.

- Claims the oldest `pending` game, marks it `analysing`, scans it ply by ply,
  writes findings, marks it `done`. One game at a time, process-wide.
- **Yields the engine between plies** (`await asyncio.sleep`), so a live game's
  move never waits behind a whole backlog — it waits behind at most one
  position.
- **Resumable.** On boot, any row left `analysing` goes back to `pending`: it
  belonged to a process that no longer exists. That single line is what makes
  the instance sleeping a non-event instead of a stuck job.
- Backs off when there is nothing to do, so an idle instance is idle.

## D — detection

`pattern_detectors.py`, pure, no engine and no database.

**A broader taxonomy than the learning loop's eight.** The eight existing
themes cover the tactical half and are reused verbatim so a correction card
built later speaks the same language. Four are added for the recurring
patterns the eight cannot express:

| new theme | what the evidence has to show |
|---|---|
| `FLANK_PAWN_COMMITTAL` | a wing pawn push, not a capture, that loses material or evaluation while the centre is unresolved |
| `ENDGAME_CONVERSION` | a clearly winning endgame evaluation given away |
| `OPENING_UNCERTAINTY` | evaluation lost inside the opening phase, off book |
| `TIME_PRESSURE` | a bad move played with little clock left — **only when the PGN carries `[%clk]`**, and silent otherwise |

**Deliberately not built: "misses discovered attacks."** It is a subset of
`TACTICAL_OVERLOOK`, and separating it needs a motif classifier this evidence
does not support. A theme that cannot be detected honestly is worse than a
theme that is missing, because a person will act on it.

**Time management is honest about being unavailable.** Most PGNs have no clock
data. When absent, the theme is never claimed rather than guessed at from move
numbers.

### Turning findings into a claim

`profile_service.aggregate()`:

- **Evidence count** — findings of that theme.
- **Games** — how many distinct games it appears in. This, not the raw count,
  is what gates a claim: **10 games analysed minimum, and the theme present in
  at least 3 of them**, so one catastrophic game cannot invent a pattern.
- **Confidence** — `low | medium | high`, from how many games carry it and how
  consistently.
- **Representative games and moves** — the worst few, so *"you consistently"*
  can always be checked against *"here, here and here"*.
- **First seen / most recent** — by game date where the PGN has one, by import
  order otherwise.
- **Trend** — the rate in the older half of the games against the newer half:
  `improving | stable | worsening`, and `stable` when there is not enough to
  say. A trend claimed from two games is noise with a label on it.

Wording is **template per theme**, not generated. *"You frequently push wing
pawns before the centre is settled"* is a sentence that must not drift from
what the detector actually counted, and a template is the only way to keep
those two in step and testable.

## Growing into Scope E

The seam is `game_findings`. A correction card is a finding plus a position to
practise; a retest is a finding whose later occurrences stop appearing. Both
read the same table, and neither needs this design to change.
