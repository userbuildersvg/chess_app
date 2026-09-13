# Elo-bracket opponent profiles — design

Date: 2026-09-13. Branch: `barry-validation-readiness`, on top of the
uncommitted latency pass (§36 of CLAUDE.md).

## Goal

Playing against Zugzwang should feel like a believable opponent at a chosen
level. The 1–20 "AI strength" integer is replaced, end to end, by seven named
Elo-style profiles. Stockfish still proposes and Gemini still decides; what
changes is *how the shortlist is built* (a weighted human-like bracket
instead of a window slid to the bottom of the list) and *how Gemini is told
who it is* (a short profile block, in the same single call).

Non-goals: new modes, new personalities beyond the profile block, any
persisted preference (the setting stays per session, as today), removal of
FEN/history context, shorter coaching.

## 1. Profiles — `opponent_profiles.py`, `chess-frontend/src/opponentProfiles.ts`

| id | label | approxElo | maxCpl | temperature | tacticalAwareness | blunderTolerance | poolSize |
|---|---|---:|---:|---:|---:|---:|---:|
| beginner | Beginner | 400 | 350 | 160 | 0.25 | 0.60 | 3 |
| casual | Casual | 800 | 250 | 110 | 0.45 | 0.40 | 3 |
| improving | Improving | 1200 | 160 | 70 | 0.65 | 0.20 | 3 |
| club | Club | 1500 | 100 | 45 | 0.80 | 0.08 | 3 |
| advanced | Advanced | 1800 | 60 | 28 | 0.90 | 0.03 | 3 |
| expert | Expert | 2100 | 30 | 15 | 0.96 | 0.01 | 3 |
| master | Master-like | 2400 | 12 | 6 | 1.00 | 0.00 | 3 |

Fields: `id`, `label`, `approx_elo`, `blurb` (one UI line), `max_cpl`
(centipawn loss ceiling for the eligible pool), `temperature` (softmax scale
for `exp(-cpl/temperature)`), `tactical_awareness` (0–1: probability weight
of "obvious" moves — checks, captures, threat answers, mate-in-1),
`blunder_tolerance` (0–1: probability a move that hangs material or walks
into mate stays eligible), `pool_size`, `commentary_style` (one sentence for
the prompt). The numbers are tuning knobs and are expected to move after
the sanity tool (§6) is run; they are not Elo claims.

`get_profile(id) -> OpponentProfile` returns `club` for `None`, unknown ids,
or non-strings, and logs once per bad value. `DEFAULT_PROFILE_ID = "club"`.
`PROFILE_IDS` is the ordered tuple above. The TypeScript file is a literal
copy of `id/label/approxElo/blurb` and `test_opponent_profiles.py` asserts
the two tables agree (it parses the `.ts` file).

## 2. Candidate bracket — `candidate_selection.py`

Replaces `select_candidates_by_difficulty` in `app.py`. Pure Python, no
engine access, deterministic given an injected `random.Random`.

Input: `fen`, the stage-1 ranked list (`[{move, score, mate_in, pv}]`, best
first, every legal move present), `profile`, `rng`.

### 2.1 Annotation (`annotate(fen, ranked) -> list[Candidate]`)

Per move, from python-chess:

- `rank` (0-based in the ranked list), `san`
- `cpl`: centipawn loss against the best entry. Mates are mapped to a cp
  scale (`mate_in n` → `±(10000 - n)`) before subtracting, so a move that
  misses a mate has a large cpl and a move that walks into one is huge.
- `mate_in` (ours, positive) / `mated_in` (theirs, negative) copied through
- `gives_check`, `is_capture`, `is_promotion`
- `hangs_piece`: after the move, one of the mover's non-king pieces is
  attacked and undefended and worth ≥ a minor piece, *or* the moved piece is
  captured for free (`guided_play.candidate_facts` `mover_loose`)
- `answers_threat`: before the move a mover's piece was attacked and
  undefended; after the move it is not (moved, defended, or the attacker is
  gone)
- `walks_into_mate`: `mated_in` is set and the best move is not mated
- `forced`: the ranked list has exactly one move

### 2.2 Eligibility

1. `forced` → the pool is that one move; stop.
2. Best move is a mate for us (`mate_in > 0`): with probability
   `tactical_awareness` the pool is `[best]` plus up to `pool_size-1`
   other eligible moves (Gemini will nearly always take the mate, and the
   profile block tells it to). `master` and `expert` always include it.
3. Eligible = `cpl ≤ max_cpl`. If fewer than `pool_size` moves qualify, the
   pool is the top `pool_size` by cpl (this covers "every move loses" and
   short endgame lists).
4. For each eligible move with `hangs_piece` or `walks_into_mate`: keep with
   probability `blunder_tolerance`, otherwise drop. A move that walks into
   mate when the best move does not is dropped for every profile above
   `casual`. Never drop below `min(pool_size, len(eligible))` moves: if the
   drops would, the last dropped are restored in cpl order.

### 2.3 Weighting and sampling

`w = exp(-cpl / temperature)`, then:

- obvious moves (`gives_check`, `is_capture` of a non-pawn, `answers_threat`)
  are multiplied by `1 + 2 * (1 - tactical_awareness)` — low levels are
  drawn to checks and captures more than their quality warrants; high levels
  are not distorted.
- quiet moves that `answers_threat` are multiplied by `tactical_awareness`
  at profiles ≤ `improving` (weak players miss quiet defence).
- `rank == 0` is multiplied by `tactical_awareness` at profiles ≤ `casual`
  (a beginner rarely finds the engine move unless it is a check/capture).

Sample `pool_size` moves without replacement, proportional to `w`. Return
them in ranked order (best first, so the deterministic fallback is the
strongest sampled move) along with the annotation.

### 2.4 Reference move

If the true best move (`rank == 0`) is not in the pool it is appended to the
stage-2 search as a *reference* (`is_reference=True`) so the recorded `cpl`
is deep-accurate. Reference entries are stripped before the list reaches
Gemini, the analysis panel, or the fallback.

### 2.5 Decision record

`decide_ai_move` returns a fourth value, `decision: dict`:

```
profile_id, approx_elo, move, rank_in_pool, rank_overall, cpl, mate_in,
n_candidates, n_eligible, chooser ("gemini" | "langflow" | None),
selected_by ("gemini" | "fallback"), fallback_reason (str | None),
rank_depth, search_depth, engine_ms, llm_ms, total_ms, model, source
```

Callers: `app.py` (Play, AI-vs-AI, manual `/api/ai-move`), `sandbox_api.py`,
`postmortem_api.py` (branches pass `profile="master"`).

## 3. Gemini — one call, one prompt block

`gemini_move_service._build_prompt` gets `context["profile"]`:

> You are playing as a {label} opponent, roughly {approx_elo} strength.
> {commentary_style} The shortlist below was already narrowed to moves such
> a player might consider - choose the one that best fits that player, and
> explain it the way that player would: plainly, about the idea, without
> engine numbers.

`commentary_style` per profile, e.g. beginner: "You explain in simple terms
what you were trying to do and may admit you did not check everything";
club: "You explain the plan and one thing your opponent should watch";
expert: "You explain the plan and the key question the position now poses."
Not a character — a level.

Contract unchanged: line 1 must be a UCI from the list; `_parse` validates
against `candidate_ucis`. Invalid or failed → the top of the pool, with
`decision.fallback_reason` set (`"gemini_invalid_move"`, `"gemini_error"`,
`"gemini_unavailable"`, `"no_llm"`).

Guided Play: `guided_play.candidate_facts` gains `own_loose` (mover's other
pieces left attacked and undefended after the move). For profiles
≤ `improving` the line-3 instruction adds: "If your own move left something
loose, you may say so — that is what you would notice a move later." The
existing rule (never name or hint at the opponent's move) stays verbatim.

## 4. API and storage

- `PlayerSession.ai_difficulty: int` → `opponent_profile: str` (default
  `club`). `SandboxSession.difficulty` likewise.
- `GET /api/difficulty` → `{profile, label, approx_elo, profiles: [{id,
  label, approx_elo, blurb}]}`. `POST /api/difficulty {profile}`; unknown id
  → 400 with the allowed list. The path is kept so nothing else moves.
- Every payload that carried `difficulty` (`/api/status`, `/api/move`,
  `/api/ai-move`, `/api/new-game`, AI-vs-AI, sandbox `/session`, review
  handoff, scenario constraints) carries `opponent_profile` and
  `approx_elo` instead. `scenario_service`'s generated-scenario schema asks
  Gemini for a profile id.
- PGN: `AIStrength` header becomes `"Club (~1500)"`; new `OpponentProfile`
  header with the id.
- Migration `010_opponent_profile.sql`: `alter table moves add column
  opponent_profile text;` `learning_service.record_move` writes it; the
  integer `difficulty` column stays, unwritten (old rows keep their meaning).
- Chat contexts (`gemini_chat_service`) say "Opponent level: Club (~1500)".
- `learning_events.ALLOWED_PROPERTIES` gains `opponent_profile, approx_elo,
  rank_in_pool, rank_overall, cpl, n_candidates, selected_by,
  fallback_reason, rank_depth, search_depth`; the `ai_move_explanation_
  generated`, `guided_watchout_generated`, game-start/end and handoff events
  carry them. `difficulty` is removed from the allowlist. No PGN or FEN is
  added to any event.

## 5. UI

- `difficulty.ts` → `opponentProfiles.ts`; `profileLabel(id)` = `"Club —
  about 1500"`.
- Play: the same `ws-meta` `<select>`, label "Opponent level", options are
  the seven profiles, `title` = blurb. Game-over and handoff copy say the
  profile name. Sandbox's select is the same component data.
- Review (Post-Mortem) header shows "vs Club (~1500)" when the game came
  from Play.
- No layout change: the select is the same element in the same slot.

## 6. Tests and QA

- `test_opponent_profiles.py` (new): table parity with the `.ts` file;
  `get_profile` defaults; annotation on hand-built positions (check,
  capture, hangs, answers threat, walks into mate, forced); eligibility and
  sampling with a seeded rng — beginner pool contains a >100cpl move
  frequently, master pool never exceeds 12cpl when a 0cpl move exists;
  mate-in-1 handling; pool never empty, never illegal, never contains a
  reference; real-Stockfish end-to-end for a mate-in-1, a stalemate-adjacent
  position and a forced move.
- Updated: `test_sandbox_api`, `test_sandbox_state`, `test_player_state`,
  `test_guided_play`, `test_guided_play_api`, `test_play_review_handoff`,
  `test_accounts`, `test_accounts_postgres`, `test_scenario`,
  `test_gemini_move`, `test_latency_paths`, `test_postmortem_api`,
  `test_move_feedback`, `test_learning_loop_api`, `test_beta_access`.
- `tools/profile_sanity.py` (new): plays N engine-only games per profile
  against master (Gemini faked to "top of pool"), prints mean cpl, blunder
  rate (≥200cpl), share of moves at rank 0. Acceptance: mean cpl strictly
  decreases beginner→master; beginner rank-0 share < 40%; master mean cpl
  < 10.
- Browser: `tools/verify/{ui,guided,review-handoff,layout-stress,overlap}.mjs`
  on `:3001`; the select shows the seven labels, unclipped, at 1366×768 and
  400px.
- CLAUDE.md: replace the difficulty paragraphs (§1 intro, §5 knobs, file
  map, API list, §11) with the profile model; add a section for
  `candidate_selection.py`.
