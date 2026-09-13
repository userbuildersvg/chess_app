# Elo-Bracket Opponent Profiles Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the 1–20 difficulty integer with seven Elo-style opponent profiles that change how the Stockfish shortlist is built and how Gemini is briefed, end to end (backend, storage, events, Play/Learn/Review UI).

**Architecture:** `opponent_profiles.py` is the source of truth for the seven profiles. `candidate_selection.py` turns the stage-1 ranked list into a profile-weighted pool of `pool_size` moves (annotated, eligibility-filtered, softmax-sampled) which stage 2 deep-refines and Gemini picks from, in the same single call, now with a profile block in the prompt. `decide_ai_move` returns a fourth `decision` dict that feeds instrumentation. Frontend mirrors the table in `opponentProfiles.ts` and renders the same `<select>` slot with seven labels.

**Tech Stack:** Python 3 / FastAPI / python-chess / Stockfish, React + TypeScript (Vite), Postgres migrations in `migrations/`, plain-script test suites run with `/tmp/chessapp/bin/python -u test_x.py`.

**Spec:** `docs/superpowers/specs/2026-09-13-elo-opponent-profiles-design.md`

## Global Constraints

- Work in `/mnt/c/Users/David/Documents/chess-app-v3.9`, branch `barry-validation-readiness`, on top of the uncommitted latency pass. **Do not commit** — the user reviews before every commit (do not `git add`/`git commit` in any step).
- Dev stack is `:3001` (Vite) + `:8081` (uvicorn). Do not restart the backend without checking `pgrep -af 'port 8081'`; uvicorn on `:8081` reloads Python on save only if started with `--reload` — it was not, so after backend edits restart it with the §12 runner script in CLAUDE.md.
- Tests are plain scripts: `DISABLE_LANGFLOW=true /tmp/chessapp/bin/python -u test_x.py`, exit code non-zero on any FAIL. Suites needing storage need `set -a; . ./.env; set +a; export DATABASE_SCHEMA="zwtest_$$"` and end with `/tmp/chessapp/bin/python -c 'import db; db.drop_schema()'`.
- Never assert an exact engine line; assert properties. Check `chess.Board(fen).is_valid()` before any constructed FEN reaches Stockfish.
- Default profile id is `"club"`; unknown ids resolve to `club`. Profile ids: `beginner, casual, improving, club, advanced, expert, master`.
- The 1–20 integer is removed everywhere except the unwritten legacy `moves.difficulty` column. `archive/patches/*` is dead code and is not touched.
- Gemini is one call per move (no second call for commentary).
- No PGN or FEN in any analytics event.

---

### Task 1: Profile table (backend + frontend mirror)

**Files:**
- Create: `opponent_profiles.py`
- Create: `chess-frontend/src/opponentProfiles.ts`
- Delete: `chess-frontend/src/difficulty.ts` (in Task 9, once no importer remains)
- Test: `test_opponent_profiles.py` (new)

**Interfaces:**
- Produces: `OpponentProfile` dataclass (`id, label, approx_elo, blurb, max_cpl, temperature, tactical_awareness, blunder_tolerance, pool_size, commentary_style`), `PROFILES: dict[str, OpponentProfile]`, `PROFILE_IDS: tuple[str, ...]`, `DEFAULT_PROFILE_ID = "club"`, `get_profile(value) -> OpponentProfile`, `profile_summary(profile) -> dict` (`{id,label,approx_elo,blurb}`), `display_label(profile) -> str` ("Club (~1500)"), `all_summaries() -> list[dict]`.
- TS: `OPPONENT_PROFILES: OpponentProfile[]`, `DEFAULT_PROFILE_ID`, `profileById(id)`, `profileLabel(id)` → `"Club — about 1500"`, `profileShort(id)` → `"Club (~1500)"`.

- [ ] **Step 1: Write the failing test file**

```python
"""Opponent profiles: the table, its defaults, and the TS mirror."""
import re, sys
import opponent_profiles as op

results = []
def check(name, ok, detail=""):
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if not ok and detail else ""))
    results.append(ok)

check("seven ids in order", op.PROFILE_IDS == ("beginner","casual","improving","club","advanced","expert","master"))
check("default is club", op.DEFAULT_PROFILE_ID == "club" and op.get_profile(None).id == "club")
check("unknown id -> club", op.get_profile("grandmaster").id == "club")
check("non-string -> club", op.get_profile(17).id == "club")
check("known id round-trips", op.get_profile("beginner").approx_elo == 400)
elos = [op.PROFILES[i].approx_elo for i in op.PROFILE_IDS]
check("elo strictly increases", elos == sorted(elos) and len(set(elos)) == 7)
cpls = [op.PROFILES[i].max_cpl for i in op.PROFILE_IDS]
check("max_cpl strictly decreases", cpls == sorted(cpls, reverse=True) and len(set(cpls)) == 7)
check("display label", op.display_label(op.get_profile("club")) == "Club (~1500)")
check("summary keys", set(op.profile_summary(op.get_profile("club"))) == {"id","label","approx_elo","blurb"})
check("every profile has commentary style", all(op.PROFILES[i].commentary_style for i in op.PROFILE_IDS))

ts = open("chess-frontend/src/opponentProfiles.ts", encoding="utf-8").read()
ts_rows = re.findall(r"id:\s*'(\w+)',\s*label:\s*'([^']+)',\s*approxElo:\s*(\d+)", ts)
check("ts mirror has seven rows", len(ts_rows) == 7, str(ts_rows))
check("ts mirror matches python", [(i, op.PROFILES[i].label, op.PROFILES[i].approx_elo) for i in op.PROFILE_IDS]
      == [(i, l, int(e)) for i, l, e in ts_rows])
check("ts default is club", "DEFAULT_PROFILE_ID = 'club'" in ts)

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
```

- [ ] **Step 2: Run it** — `cd /mnt/c/Users/David/Documents/chess-app-v3.9 && /tmp/chessapp/bin/python -u test_opponent_profiles.py` — expect `ModuleNotFoundError: opponent_profiles`.

- [ ] **Step 3: Write `opponent_profiles.py`**

```python
"""
The seven opponent profiles - the only strength setting the app has.
...(module docstring: why profiles replace the 1-20 integer; numbers are
tuning knobs, not Elo claims; get_profile never raises)...
"""
from dataclasses import dataclass
import logging
logger = logging.getLogger(__name__)

@dataclass(frozen=True)
class OpponentProfile:
    id: str
    label: str
    approx_elo: int
    blurb: str
    max_cpl: int          # centipawn-loss ceiling for the eligible pool
    temperature: float    # softmax scale: weight = exp(-cpl / temperature)
    tactical_awareness: float  # 0-1, pull toward checks/captures/threat answers/mates
    blunder_tolerance: float   # 0-1, chance a hanging/mated move stays eligible
    pool_size: int
    commentary_style: str

_TABLE = (
    OpponentProfile("beginner", "Beginner", 400, "Misses tactics and leaves pieces loose. Good for learning how the pieces work.",
                    350, 160, 0.25, 0.60, 3,
                    "Explain in simple words what you were trying to do. You may admit you did not check everything."),
    OpponentProfile("casual", "Casual", 800, "Sees obvious captures and checks, misses anything deeper.",
                    250, 110, 0.45, 0.40, 3,
                    "Explain the one idea behind the move. You often notice only the most direct threats."),
    OpponentProfile("improving", "Improving", 1200, "Develops sensibly and spots one-move tactics; misses quiet defence.",
                    160, 70, 0.65, 0.20, 3,
                    "Explain the idea and what you hope it leads to. You sometimes overlook a quiet reply."),
    OpponentProfile("club", "Club", 1500, "Reasonable, coherent chess with the occasional strategic slip.",
                    100, 45, 0.80, 0.08, 3,
                    "Explain the plan and mention one thing your opponent should keep an eye on."),
    OpponentProfile("advanced", "Advanced", 1800, "Coherent plans and solid defence; errors are subtle.",
                    60, 28, 0.90, 0.03, 3,
                    "Explain the plan and the positional reason behind it."),
    OpponentProfile("expert", "Expert", 2100, "Strong tactics and sound positional play.",
                    30, 15, 0.96, 0.01, 3,
                    "Explain the plan and the key question the position now asks of your opponent."),
    OpponentProfile("master", "Master-like", 2400, "Near-best moves nearly every time. A very strong training partner.",
                    12, 6, 1.00, 0.00, 3,
                    "Explain precisely why this move, and what the critical continuation is."),
)
PROFILES = {p.id: p for p in _TABLE}
PROFILE_IDS = tuple(p.id for p in _TABLE)
DEFAULT_PROFILE_ID = "club"
_warned = set()

def get_profile(value) -> OpponentProfile:
    if isinstance(value, OpponentProfile):
        return value
    if isinstance(value, str) and value in PROFILES:
        return PROFILES[value]
    if value is not None and value not in _warned:
        _warned.add(value)
        logger.warning(f"⚠️ Unknown opponent profile {value!r} - using {DEFAULT_PROFILE_ID}")
    return PROFILES[DEFAULT_PROFILE_ID]

def display_label(profile) -> str:
    p = get_profile(profile)
    return f"{p.label} (~{p.approx_elo})"

def profile_summary(profile) -> dict:
    p = get_profile(profile)
    return {"id": p.id, "label": p.label, "approx_elo": p.approx_elo, "blurb": p.blurb}

def all_summaries() -> list:
    return [profile_summary(i) for i in PROFILE_IDS]
```

- [ ] **Step 4: Write `chess-frontend/src/opponentProfiles.ts`** with the same seven rows (`id, label, approxElo, blurb`), `DEFAULT_PROFILE_ID = 'club'`, `profileById(id: string): OpponentProfile` (falls back to club), `profileLabel(id)` → `` `${p.label} — about ${p.approxElo}` `` (master: `"Master-like — about 2400+"`), `profileShort(id)` → `` `${p.label} (~${p.approxElo})` ``. Header comment: it mirrors `opponent_profiles.py` and `test_opponent_profiles.py` fails if the two drift.

- [ ] **Step 5: Run the test** — expect all PASS.

---

### Task 2: Candidate annotation (`candidate_selection.annotate`)

**Files:**
- Create: `candidate_selection.py`
- Modify: `guided_play.py:58-96` (add `own_loose` to `candidate_facts`)
- Test: append to `test_opponent_profiles.py`

**Interfaces:**
- Produces: `Candidate` dict shape: `{move, san, score, mate_in, pv, rank, cpl, gives_check, is_capture, is_promotion, hangs_piece, answers_threat, walks_into_mate, forced, is_reference}`; `annotate(fen: str, ranked: list[dict]) -> list[Candidate]` (ranked order preserved); `cp_value(entry) -> int` (mate → `±(10000 - |n|)`).
- `guided_play.candidate_facts(fen, uci)` gains `own_loose: list[str]` (mover's other pieces attacked and undefended after the move).

- [ ] **Step 1: Failing tests** (append to `test_opponent_profiles.py`)

```python
import chess, candidate_selection as cs, guided_play
def ranked_from(fen, scored):  # scored: [(uci, cp or ('mate', n))]
    out = []
    for uci, s in scored:
        if isinstance(s, tuple): out.append({"move": uci, "score": None, "mate_in": s[1], "pv": [uci]})
        else: out.append({"move": uci, "score": s, "mate_in": None, "pv": [uci]})
    return out

# Scholar's mate position: white to move, Qxf7# available.
MATE_FEN = "r1bqkb1r/pppp1ppp/2n2n2/4p2Q/2B1P3/8/PPPP1PPP/RNB1K1NR w KQkq - 4 4"
assert chess.Board(MATE_FEN).is_valid()
r = ranked_from(MATE_FEN, [("h5f7", ("mate", 1)), ("c4f7", 120), ("h5e5", -300), ("h5h7", -900)])
a = cs.annotate(MATE_FEN, r)
check("annotate keeps order", [c["move"] for c in a] == ["h5f7", "c4f7", "h5e5", "h5h7"])
check("mate has cpl 0 and mate_in 1", a[0]["cpl"] == 0 and a[0]["mate_in"] == 1)
check("missing the mate is a huge cpl", a[1]["cpl"] > 5000)
check("check detected", a[1]["gives_check"] is True and a[0]["gives_check"] is True)
check("capture detected", a[1]["is_capture"] is True and a[2]["is_capture"] is True)
check("san filled", a[0]["san"] == "Qxf7#")
check("Qxh7 hangs the queen", a[3]["hangs_piece"] is True)
check("Qxf7# does not hang", a[0]["hangs_piece"] is False)
check("rank numbered", [c["rank"] for c in a] == [0, 1, 2, 3])
check("not forced", all(c["forced"] is False for c in a))

# Threat answer: white knight on c3 attacked by pawn b4, undefended. Nc3-e2 answers it.
THREAT_FEN = "4k3/8/8/8/1p6/2N5/8/4K3 w - - 0 1"
assert chess.Board(THREAT_FEN).is_valid()
r = ranked_from(THREAT_FEN, [("c3e2", 0), ("e1d1", -250)])
a = cs.annotate(THREAT_FEN, r)
check("moving the attacked knight answers the threat", a[0]["answers_threat"] is True)
check("king shuffle leaves it hanging", a[1]["answers_threat"] is False and a[1]["hangs_piece"] is True)

# Forced: only one legal move.
FORCED_FEN = "k7/8/8/8/8/8/8/K5R1 b - - 0 1"  # ...Ka8 in check? no - use a genuine one-move position
FORCED_FEN = "7k/5Q2/6K1/8/8/8/8/8 b - - 0 1"  # black king h8, Qf7, Kg6: only ...Kh8-? check python-chess
b = chess.Board(FORCED_FEN); assert b.is_valid()
if len(list(b.legal_moves)) == 1:
    only = list(b.legal_moves)[0].uci()
    a = cs.annotate(FORCED_FEN, ranked_from(FORCED_FEN, [(only, -9000)]))
    check("single legal move is forced", a[0]["forced"] is True)
else:
    check("forced fixture has exactly one legal move", False, f"{len(list(b.legal_moves))} moves")

# walks_into_mate: black to move, Kg8 vs white Qd1 + Rh1... construct: black can play ...Kh8 into Qd8# or ...f6 safe
WIM_FEN = "6k1/5ppp/8/8/8/8/5PPP/3Q2K1 b - - 0 1"
assert chess.Board(WIM_FEN).is_valid()
r = ranked_from(WIM_FEN, [("h7h6", 0), ("g8h8", ("mate", -1))])
a = cs.annotate(WIM_FEN, r)
check("walks_into_mate flagged", a[1]["walks_into_mate"] is True and a[0]["walks_into_mate"] is False)

facts = guided_play.candidate_facts(THREAT_FEN, "e1d1")
check("own_loose lists the knight", any("knight on c3" in s for s in facts["own_loose"]))
facts = guided_play.candidate_facts(THREAT_FEN, "c3e2")
check("own_loose empty after answering", facts["own_loose"] == [])
```

(Verify each fixture with python-chess before relying on it: `Board(fen).is_valid()`, the mate is real, the forced position has exactly one legal move. Adjust the fixture FEN, not the assertion, if a fixture is wrong.)

- [ ] **Step 2: Run** — expect `AttributeError`/`ModuleNotFoundError`.

- [ ] **Step 3: Implement `candidate_selection.annotate` and `guided_play.own_loose`**

```python
# candidate_selection.py
MATE_CP = 10000
def cp_value(entry) -> int:
    m = entry.get("mate_in")
    if m is not None:
        return (MATE_CP - abs(m)) if m > 0 else -(MATE_CP - abs(m))
    s = entry.get("score")
    return int(s) if s is not None else -MATE_CP  # unscored sorts last

MINOR = 3
_VAL = {chess.PAWN: 1, chess.KNIGHT: 3, chess.BISHOP: 3, chess.ROOK: 5, chess.QUEEN: 9}

def _loose_pieces(board, color):
    """Squares of `color`'s non-king pieces attacked by the other side and not defended."""
    out = []
    for sq, piece in board.piece_map().items():
        if piece.color != color or piece.piece_type == chess.KING: continue
        if board.is_attacked_by(not color, sq) and not board.is_attacked_by(color, sq):
            out.append((sq, piece))
    return out

def annotate(fen, ranked):
    board = chess.Board(fen)
    legal_count = board.legal_moves.count()
    best_cp = cp_value(ranked[0]) if ranked else 0
    best_mated = ranked and (ranked[0].get("mate_in") or 0) < 0
    loose_before = {sq for sq, _ in _loose_pieces(board, board.turn)}
    out = []
    for rank, e in enumerate(ranked):
        move = chess.Move.from_uci(e["move"])
        c = dict(e)
        c.update(rank=rank, cpl=max(0, best_cp - cp_value(e)), forced=(legal_count == 1), is_reference=False)
        c["san"] = board.san(move)
        c["is_capture"] = board.is_capture(move)
        c["is_promotion"] = move.promotion is not None
        c["gives_check"] = board.gives_check(move)
        after = board.copy(stack=False); after.push(move)
        mover = board.turn
        loose_after = _loose_pieces(after, mover)
        landed_loose = any(sq == move.to_square for sq, _ in loose_after)
        c["hangs_piece"] = any(_VAL.get(p.piece_type, 0) >= MINOR for _, p in loose_after) or (
            landed_loose and _VAL.get(board.piece_at(move.from_square).piece_type, 0) >= MINOR)
        c["answers_threat"] = bool(loose_before) and not {sq for sq, _ in loose_after} & (loose_before - {move.from_square}) \
            and not landed_loose
        c["walks_into_mate"] = (e.get("mate_in") or 0) < 0 and not best_mated
        out.append(c)
    return out
```

`guided_play.candidate_facts`: after computing `mover_loose`, add
`own_loose = [_describe(board, sq) for sq, p in board.piece_map().items() if p.color == mover and p.piece_type != KING and sq != landed and board.is_attacked_by(enemy, sq) and not board.is_attacked_by(mover, sq)]`; add `"own_loose": own_loose` to the returned dict and to `EMPTY_FACTS` (`[]`). In `describe_candidates`, append `"leaves its own " + ", ".join(own_loose) + " undefended"` when non-empty.

- [ ] **Step 4: Run** — all PASS. Also run `/tmp/chessapp/bin/python -u test_guided_play.py` — must still pass (the facts dict gained a key; if a test asserts the exact key set, update that assertion to include `own_loose`).

---

### Task 3: Eligibility, weighting, sampling (`candidate_selection.select_pool`)

**Files:**
- Modify: `candidate_selection.py`
- Test: append to `test_opponent_profiles.py`

**Interfaces:**
- Produces: `select_pool(fen, ranked, profile, rng=None) -> tuple[list[Candidate], dict]`. Returns the pool (subset of `annotate()` output, ranked order, `len ≤ profile.pool_size`, never empty when `ranked` is non-empty) and `info = {"n_eligible": int, "n_legal": int, "weights": {uci: float}}`. `rng` defaults to `random.Random()`.
- `with_reference(pool, annotated) -> list[Candidate]`: if `rank 0` is absent, append a copy with `is_reference=True`.
- `strip_reference(cands) -> list`.

- [ ] **Step 1: Failing tests**

```python
import random
from opponent_profiles import get_profile
# 12-move synthetic list, cpl 0..1100 in steps of 100, with move 3 a hanging capture, move 1 a check.
SYN_FEN = "r1bqkbnr/pppp1ppp/2n5/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq - 2 3"
b = chess.Board(SYN_FEN); legal = [m.uci() for m in b.legal_moves]
syn = ranked_from(SYN_FEN, [(u, 100 - 100 * i) for i, u in enumerate(legal[:12])])

def pools(pid, n=200, seed=1):
    rng = random.Random(seed); out = []
    for _ in range(n):
        pool, info = cs.select_pool(SYN_FEN, syn, get_profile(pid), rng); out.append(pool)
    return out

for pid in ("beginner", "club", "master"):
    ps = pools(pid)
    check(f"{pid}: pool never empty, size <= 3", all(0 < len(p) <= 3 for p in ps))
    check(f"{pid}: pool only legal moves", all(c["move"] in legal for p in ps for c in p))
    check(f"{pid}: pool in ranked order", all([c["rank"] for c in p] == sorted(c["rank"] for c in p) for p in ps))
    check(f"{pid}: no reference in pool", all(not c["is_reference"] for p in ps for c in p))

master_max = max(c["cpl"] for p in pools("master") for c in p)
check("master never exceeds max_cpl when better exists", master_max <= 12, str(master_max))
beg_bad = sum(1 for p in pools("beginner") if any(c["cpl"] >= 100 for c in p))
check("beginner pools usually contain a >=100cpl move", beg_bad > 120, str(beg_bad))
beg_top = sum(1 for p in pools("beginner") if p[0]["rank"] == 0)
check("beginner rarely has the engine move in the pool", beg_top < 100, str(beg_top))
club_worst = max(c["cpl"] for p in pools("club") for c in p)
check("club stays within 100cpl", club_worst <= 100, str(club_worst))

# Mate-in-1 available: master always includes it; beginner includes it about tactical_awareness of the time.
r = ranked_from(MATE_FEN, [("h5f7", ("mate", 1)), ("c4f7", 120), ("h5e5", -300), ("h5h7", -900), ("d2d3", -400)])
rng = random.Random(3)
m_has = sum(1 for _ in range(100) if any(c["move"] == "h5f7" for c in cs.select_pool(MATE_FEN, r, get_profile("master"), rng)[0]))
b_has = sum(1 for _ in range(300) if any(c["move"] == "h5f7" for c in cs.select_pool(MATE_FEN, r, get_profile("beginner"), rng)[0]))
check("master always has the mate", m_has == 100, str(m_has))
check("beginner finds the mate sometimes, not always", 30 < b_has < 180, str(b_has))

# Forced move
only = list(chess.Board(FORCED_FEN).legal_moves)[0].uci()
pool, info = cs.select_pool(FORCED_FEN, ranked_from(FORCED_FEN, [(only, -9000)]), get_profile("beginner"))
check("forced move is the whole pool", [c["move"] for c in pool] == [only] and info["n_eligible"] == 1)

# Walks into mate never offered above casual
r = ranked_from(WIM_FEN, [("h7h6", 0), ("g7g6", -5), ("f7f6", -10), ("g8h8", ("mate", -1))])
rng = random.Random(5)
imp = sum(1 for _ in range(200) if any(c["walks_into_mate"] for c in cs.select_pool(WIM_FEN, r, get_profile("improving"), rng)[0]))
check("improving never walks into mate", imp == 0, str(imp))

# Short list: fewer moves than pool_size
pool, _ = cs.select_pool(WIM_FEN, r[:2], get_profile("master"))
check("short list returned whole", len(pool) == 2)

# Reference handling
a = cs.annotate(SYN_FEN, syn)
sub = [a[4], a[5], a[6]]
wr = cs.with_reference(sub, a)
check("reference appended when best absent", len(wr) == 4 and wr[-1]["is_reference"] and wr[-1]["rank"] == 0)
check("strip_reference removes it", [c["move"] for c in cs.strip_reference(wr)] == [c["move"] for c in sub])
check("no reference when best present", len(cs.with_reference([a[0], a[1]], a)) == 2)
```

- [ ] **Step 2: Run** — expect `AttributeError: select_pool`.

- [ ] **Step 3: Implement**

```python
import math, random
LOW = ("beginner", "casual", "improving")

def select_pool(fen, ranked, profile, rng=None):
    rng = rng or random.Random()
    cands = annotate(fen, ranked)
    info = {"n_legal": len(cands), "n_eligible": 0, "weights": {}}
    if not cands:
        return [], info
    k = profile.pool_size
    if cands[0]["forced"]:
        info["n_eligible"] = 1
        return cands[:1], info
    best = cands[0]
    # 1. eligibility by cpl ceiling
    eligible = [c for c in cands if c["cpl"] <= profile.max_cpl]
    if len(eligible) < k:
        eligible = sorted(cands, key=lambda c: c["cpl"])[:k]
    # 2. blunder filter
    kept, dropped = [], []
    for c in eligible:
        risky = c["hangs_piece"] or c["walks_into_mate"]
        if c["walks_into_mate"] and profile.id not in ("beginner", "casual"):
            dropped.append(c); continue
        if risky and rng.random() >= profile.blunder_tolerance:
            dropped.append(c); continue
        kept.append(c)
    need = min(k, len(eligible))
    if len(kept) < need:
        kept += sorted(dropped, key=lambda c: c["cpl"])[:need - len(kept)]
    info["n_eligible"] = len(kept)
    # 3. weights
    weights = {}
    for c in kept:
        w = math.exp(-c["cpl"] / profile.temperature)
        obvious = c["gives_check"] or (c["is_capture"] and not _captures_pawn(fen, c)) or c["answers_threat"]
        if obvious:
            w *= 1 + 2 * (1 - profile.tactical_awareness)
        if c["answers_threat"] and not c["gives_check"] and not c["is_capture"] and profile.id in LOW:
            w *= profile.tactical_awareness
        if c["rank"] == 0 and profile.id in ("beginner", "casual") and not obvious:
            w *= profile.tactical_awareness
        weights[c["move"]] = max(w, 1e-9)
    info["weights"] = weights
    # 4. mate-in-1 for us
    pool = []
    if best.get("mate_in") and best["mate_in"] > 0 and best in kept:
        if profile.id in ("master", "expert") or rng.random() < profile.tactical_awareness:
            pool.append(best)
    # 5. weighted sample without replacement
    remaining = [c for c in kept if c not in pool]
    while len(pool) < k and remaining:
        total = sum(weights[c["move"]] for c in remaining)
        r = rng.random() * total
        for i, c in enumerate(remaining):
            r -= weights[c["move"]]
            if r <= 0:
                pool.append(remaining.pop(i)); break
        else:
            pool.append(remaining.pop())
    pool.sort(key=lambda c: c["rank"])
    return pool, info

def _captures_pawn(fen, c):
    b = chess.Board(fen); m = chess.Move.from_uci(c["move"])
    p = b.piece_at(m.to_square)
    return p is not None and p.piece_type == chess.PAWN

def with_reference(pool, annotated):
    if any(c["rank"] == 0 for c in pool) or not annotated:
        return list(pool)
    ref = dict(annotated[0]); ref["is_reference"] = True
    return list(pool) + [ref]

def strip_reference(cands):
    return [c for c in cands if not c.get("is_reference")]
```

- [ ] **Step 4: Run** — all PASS. If the beginner/mate thresholds fail, tune the profile numbers in `opponent_profiles.py` (they are knobs), not the assertions' intent.

---

### Task 4: `decide_ai_move` uses the pool and returns a decision record

**Files:**
- Modify: `app.py:567-580` (remove `DIFFICULTY_MAX`, `DIFFICULTY_WINDOW_SIZE`, `select_candidates_by_difficulty`), `app.py:661-849` (`decide_ai_move`)
- Modify: `stockfish_service.py:388-425` (`refine_candidates` keeps annotation keys on the refined entries)
- Modify: `gemini_move_service.py:173-256` (`_build_prompt` profile block), `gemini_move_service.py:296+` (accept `context["profile"]`)
- Test: `test_decide_integration.py`, `test_gemini_move.py`, new checks in `test_opponent_profiles.py`

**Interfaces:**
- `decide_ai_move(current_fen, moving_color, *, profile="club", last_move=_UNSET, use_learning=True, learning=None, guided=False, rng=None) -> (move, explanation, source, decision)`.
- `decision` keys: `profile_id, approx_elo, move, rank_in_pool, rank_overall, cpl, mate_in, n_candidates, n_eligible, chooser, selected_by, fallback_reason, rank_depth, search_depth, engine_ms, llm_ms, total_ms, model, source`.
- `refine_candidates(fen, candidates, depth=None)` returns entries that keep every extra key of the input entry with that `move` (merge `{**original, **refined}`), so `rank/cpl/is_reference/...` survive stage 2. Re-computes `cpl` for the refined set against the deepest `cp_value` among them (the reference makes that the true best).
- `gemini_move_service.choose_move_from_candidates(fen, candidates, context)` returns `(move, explanation, success)` unchanged; `context["profile"]` is an `OpponentProfile`; `context["own_loose_hint"]` bool switches the guided line-3 extra sentence. The model name used is stored on `self.last_model`.

- [ ] **Step 1: Update `test_decide_integration.py`** — every `move, expl, source = with_fake(...)` becomes `move, expl, source, decision = with_fake(...)`; add:

```python
check("decision carries profile", decision["profile_id"] == "club" and decision["approx_elo"] == 1500)
check("decision names the chooser", decision["selected_by"] == "gemini" and decision["fallback_reason"] is None)
check("decision cpl is a number", isinstance(decision["cpl"], int) and decision["cpl"] >= 0)
check("candidates count matches prompt", decision["n_candidates"] == len(seen_prompt["moves"]))
# fallback path
_, _, source3, d3 = with_fake(status=500)
check("fallback reason recorded", d3["selected_by"] == "fallback" and d3["fallback_reason"] == "gemini_error")
# invalid move
_, _, s5, d5 = with_fake(pick_index=None, reply_move="a1a1")   # extend FakeClient to allow a literal move
check("invalid Gemini move falls back", s5 == "stockfish_fallback" and d5["fallback_reason"] == "gemini_invalid_move")
# profile reaches the prompt
with_fake(pick_index=0, profile="beginner")
check("beginner block in prompt", "Beginner" in seen_prompt["text"] and "400" in seen_prompt["text"])
```

(Extend `with_fake` to pass `profile=` through to `app.decide_ai_move(FEN, "white", profile=profile)`.)

- [ ] **Step 2: Run `test_decide_integration.py`** — expect unpack error.

- [ ] **Step 3: Rewrite the engine stage in `decide_ai_move`**

```python
from opponent_profiles import get_profile
import candidate_selection

profile = get_profile(profile)
def _engine_stages():
    t_enter = time.monotonic()
    with stockfish_service.priority():
        t_acquired = time.monotonic()
        ranked = stockfish_service.get_ranked_moves(current_fen, None)
        t_stage1 = time.monotonic()
        ranked = deprioritize_reversal(ranked, last_move)
        pool, info = candidate_selection.select_pool(current_fen, ranked, profile, rng)
        to_refine = candidate_selection.with_reference(pool, candidate_selection.annotate(current_fen, ranked))
        refined = stockfish_service.refine_candidates(current_fen, to_refine)
        return refined, info, t_stage1, t_acquired - t_enter
```

After refinement: `candidates = candidate_selection.strip_reference(refined)`; `candidates.sort(key=lambda c: c["rank"])` is NOT applied — keep refined order (best-first by deep score) so `window_top_move` is the deep-best of the pool. Build `decision` at each return site via one helper `_decision(chosen, selected_by, fallback_reason, gemini_started)` that looks up the chosen candidate's `rank`, `cpl`, `mate_in`, and fills `rank_depth=stockfish_service.RANK_DEPTH`, `search_depth=stockfish_service.depth`, `model=getattr(chooser, "last_model", None)`, `chooser="gemini"|"langflow"|None`. Fallback reasons: `"no_llm"`, `"gemini_error"`, `"gemini_invalid_move"`, `"gemini_failed"`. Replace `difficulty=` in the timing log with `profile={profile.id}`. Pass `context = {"profile": profile, "own_loose_hint": profile.id in candidate_selection.LOW}` always (merge `guided`/`facts` into it when guided) — the non-guided prompt is no longer byte-identical to before, which is intended; update the `test_guided_play_api` "standard mode byte-identical" check to compare against the new non-guided prompt (assert it lacks "Watch out" and contains the profile block).

- [ ] **Step 4: `_build_prompt` profile block** — insert after the FEN/history lines:

```python
p = context.get("profile")
if p is not None:
    prompt.append(
        f"You are playing as a {p.label} opponent, roughly {p.approx_elo} strength. "
        f"{p.commentary_style} The shortlist below was already narrowed to moves such a "
        f"player might consider - choose the one that best fits that player. If a move "
        f"delivers checkmate, play it."
    )
```
Remove the old `context.get("difficulty")` block. In the guided branch, when `context.get("own_loose_hint")`, append to the line-3 rules: `"If the board facts say your own move left one of your pieces loose, you may say so - that is what such a player notices one move too late."` Keep every existing sentence of the "do NOT recommend" rule verbatim. Record `self.last_model = model` inside `attempt(model)` on success.

- [ ] **Step 5: `refine_candidates` merge** — after `refined = self._analyse_moves(...)`, do `by_move = {c["move"]: c for c in candidates}; refined = [{**by_move.get(r["move"], {}), **r} for r in refined]`; then recompute `cpl` = `max(0, best_cp - cp_value(r))` where `best_cp = max(cp_value(r) for r in refined)` (import `cp_value` from `candidate_selection`; no circular import — `candidate_selection` imports only `chess`).

- [ ] **Step 6: Run** `test_decide_integration.py`, `test_gemini_move.py` (fix any test that passes `context={"difficulty": n}` → `{"profile": get_profile("club")}`), `test_opponent_profiles.py`. All PASS.

---

### Task 5: Sessions, routes, PGN, chat context (backend Play path)

**Files:**
- Modify: `player_state.py:75,101,212,279` (`opponent_profile: str = "club"`; `to_dict` → `"opponent_profile"`, `"approx_elo"`)
- Modify: `app.py` every `ai_difficulty` / `difficulty=` site listed by `grep -n difficulty app.py`: lines ≈310, 924, 976, 1034, 1075, 1100, 1341-1342, 1414, 1494-1495, 1741, 1787, 1866, 1953, 2080-2107, 2141-2196
- Modify: `gemini_chat_service.py:205,318,498`
- Modify: `postmortem_api.py:81,391` (`BRANCH_PROFILE = "master"`, unpack 4-tuple)
- Modify: `sandbox_api.py:543` (unpack 4-tuple; rest in Task 7)
- Test: `test_player_state.py`, `test_guided_play_api.py`, `test_play_review_handoff.py`, `test_postmortem_api.py`, `test_latency_paths.py`, `test_beta_access.py`

**Interfaces:**
- `PlayerSession.opponent_profile: str`; `PlayerStore.for_identity(identity, profile="club")`.
- `GET /api/difficulty` → `{"profile": id, "label", "approx_elo", "profiles": all_summaries()}`; `POST /api/difficulty {"profile": id}` → same body, 400 on unknown id (`"message": "profile must be one of ...", "allowed": PROFILE_IDS`).
- Every JSON that carried `"difficulty": int` now carries `"opponent_profile": id, "approx_elo": int`. `/api/status` too.
- PGN headers: `AIStrength = display_label(profile)`, `OpponentProfile = id`.
- `decide_ai_move` callers unpack 4 values and pass `decision` on to Task 6's event helper.

- [ ] **Step 1: Update tests first** — in `test_player_state.py` replace `difficulty=` constructor args and `["difficulty"]` reads with `profile=` / `["opponent_profile"]`; add `check("default profile club", PlayerSession("s","i").opponent_profile == "club")`. In `test_guided_play_api.py`, `test_play_review_handoff.py`, `test_beta_access.py`, `test_latency_paths.py`: replace `POST /api/difficulty {"difficulty": n}` with `{"profile": "master"}` (where a strong bot was wanted) and assertions on `"difficulty"` with `"opponent_profile"`. Add to `test_guided_play_api.py`:

```python
r = client.post("/api/difficulty", json={"profile": "wizard"})
check("unknown profile rejected", r.status_code == 400 and "allowed" in r.json().get("details", r.json()))
r = client.post("/api/difficulty", json={"profile": "beginner"})
check("profile set", r.json()["data"]["profile"] == "beginner" and r.json()["data"]["approx_elo"] == 400)
r = client.get("/api/difficulty")
check("profiles listed", [p["id"] for p in r.json()["data"]["profiles"]] == list(PROFILE_IDS))
```
And in the PGN/handoff test: `check("PGN carries profile", 'OpponentProfile "beginner"' in pgn and 'AIStrength "Beginner (~400)"' in pgn)`.

- [ ] **Step 2: Run those suites** — expect failures on the old field.

- [ ] **Step 3: Implement** — mechanical replacement guided by the grep list. `DifficultyRequest` becomes `class ProfileRequest(BaseModel): profile: str`. `learning_service.record_move(..., difficulty=...)` call sites pass `opponent_profile=s.opponent_profile` (Task 6 changes the signature — do both in the same edit pass or the suites will not import). Chat context strings: `f"Opponent level: {display_label(profile)}"`.

- [ ] **Step 4: Run** the six suites + `test_opponent_profiles.py`. All PASS.

---

### Task 6: Storage and instrumentation

**Files:**
- Create: `migrations/010_opponent_profile.sql` — `alter table moves add column if not exists opponent_profile text;`
- Modify: `learning_service.py:185-240` (`difficulty: Optional[int]` → `opponent_profile: Optional[str]`; insert/upsert column list)
- Modify: `learning_events.py:125` (allowlist: remove `difficulty`; add `opponent_profile, approx_elo, rank_in_pool, rank_overall, cpl, n_candidates, n_eligible, selected_by, fallback_reason, rank_depth, search_depth`)
- Modify: `app.py` event emit sites (≈924-930, 1778-1791) to spread `decision` fields; helper `def _decision_props(decision) -> dict` in `app.py` picking exactly the allowlisted keys.
- Test: `test_accounts_postgres.py` (migration count / column presence), `test_guided_play_api.py` (events carry profile fields and no text), `test_learning_loop.py` (allowlist)

**Interfaces:**
- Produces: `_decision_props(decision: dict) -> dict` used by Play, AI-vs-AI, manual AI move, Sandbox and Post-Mortem branch events.

- [ ] **Step 1: Tests** — `test_guided_play_api.py`: after an AI move, find the `ai_move_explanation_generated` event and `check("event carries decision", set(["opponent_profile","approx_elo","rank_in_pool","cpl","n_candidates","selected_by"]) <= set(ev["props"]) and "explanation" not in ev["props"] and "fen" not in ev["props"])`. `test_learning_loop.py`: `check("difficulty no longer an allowed prop", "difficulty" not in learning_events.ALLOWED_PROPERTIES and "opponent_profile" in learning_events.ALLOWED_PROPERTIES)`. `test_accounts_postgres.py`: where migrations are counted, bump to 10 and add a check that `moves` has an `opponent_profile` column.

- [ ] **Step 2: Run** — expect FAIL.
- [ ] **Step 3: Implement** the four files.
- [ ] **Step 4: Run** `test_guided_play_api.py`, `test_learning_loop.py`, then the storage suites with a disposable schema (`test_accounts.py`, `test_accounts_postgres.py`, `test_improvement_profile.py`, `test_beta_access.py`) and `db.drop_schema()`.

---

### Task 7: Sandbox and scenarios on profiles

**Files:**
- Modify: `sandbox_state.py:328-344,386` (`profile: str = "club"`, `to_dict` → `opponent_profile`, `approx_elo`)
- Modify: `sandbox_api.py:242-288,330,384,401,469-488,525-546,856` (request models `profile: Optional[str]`; `/session`, `/reset`, `/scenario`, `/ai-move` use `get_profile`)
- Modify: `scenario_service.py:842,864,1065-1083` (schema asks for `"profile": one of beginner|casual|improving|club|advanced|expert|master`; hint: "casual/easy → casual or improving, hard/tough → expert, else club"; normalise with `get_profile(...).id`)
- Test: `test_sandbox_state.py`, `test_sandbox_api.py`, `test_scenario.py`, `test_sandbox_narration.py`

- [ ] **Step 1: Tests** — replace `difficulty` in the three suites with `profile` / `opponent_profile`; add `check("reset with bad profile -> club", ...)` and `check("scenario profile normalised", scenario["profile"] in PROFILE_IDS)`.
- [ ] **Step 2: Run** — FAIL. **Step 3: Implement.** **Step 4: Run** the four suites — PASS.

---

### Task 8: Behaviour sanity tool

**Files:**
- Create: `tools/profile_sanity.py`

**Interfaces:**
- CLI: `DISABLE_LANGFLOW=true /tmp/chessapp/bin/python tools/profile_sanity.py --games 3 --plies 40 --profiles beginner,casual,club,master`. Uses `app.decide_ai_move` with no Gemini key set in-process (`gemini_move_service.available` False → fallback picks top of pool) so the *pool* is what is measured. Plays the profile as White against `master` as Black from the start position. Prints per profile: `mean_cpl, median_cpl, blunder_rate (cpl>=200), top1_share (rank_overall==0), fallback_share, illegal=0`.
- Acceptance printed as PASS/FAIL lines: mean cpl strictly decreasing in table order; beginner top1_share < 0.40; master mean_cpl < 10; illegal == 0 for all.

- [ ] **Step 1: Write the tool** (uses `chess.Board`, loops `asyncio.run(app.decide_ai_move(fen, color, profile=pid, use_learning=False))`, pushes the move, verifies legality first with `chess.Move.from_uci(m) in board.legal_moves`).
- [ ] **Step 2: Run it** with `--games 2 --plies 30`. Record the numbers in the final report. If ordering fails, tune `opponent_profiles.py` knobs and re-run Task 3's tests.

---

### Task 9: Frontend — Play, Learn, Review, services, types

**Files:**
- Modify: `chess-frontend/src/components/ChessBoard.tsx:16,366,555-556,993-1040,1396-1397,1606-1632,1777,1831-1832,2144-2160`
- Modify: `chess-frontend/src/components/Sandbox.tsx:16,420,1068-1116,1612-1620`
- Modify: `chess-frontend/src/services/sandboxService.ts`, `chess-frontend/src/services/learningService.ts:65`, `chess-frontend/src/types/chess.ts:50`, `chess-frontend/src/types/sandbox.ts:82`, `chess-frontend/src/components/AccountMenu.tsx:242`, `chess-frontend/src/components/PostMortem.tsx` (header "vs Club (~1500)" when `opponent_profile` present on the review)
- Delete: `chess-frontend/src/difficulty.ts`
- CSS: `shell.css`, `ChessBoard.css`, `Sandbox.css` — keep class names (`game-difficulty`) so layout suites hold; only the label text changes.

**Interfaces:**
- State: `const [profile, setProfile] = useState<string>(DEFAULT_PROFILE_ID)`; `handleProfileChange` POSTs `{profile}` to `/api/difficulty`; status/new-game responses read `data.opponent_profile`.
- Select markup:
```tsx
<label className="game-difficulty">
  <span className="ws-label-full">Opponent level</span>
  <select aria-label="Opponent level" value={profile} onChange={handleProfileChange} title={profileById(profile).blurb}>
    {OPPONENT_PROFILES.map(p => <option key={p.id} value={p.id}>{profileLabel(p.id)}</option>)}
  </select>
</label>
```
- Review handoff payload: `opponent_profile` instead of `difficulty`; the "strength N" text at ≈1777 becomes `` ` · ${profileShort(profile)}` ``.

- [ ] **Step 1: Edit** all files above; `grep -rn difficulty chess-frontend/src` must return only CSS class names.
- [ ] **Step 2: Typecheck** — `cd chess-frontend && npx tsc -b --force` → no errors.
- [ ] **Step 3: Browser** — with the backend restarted on `:8081` and `BETA_ACCESS_REQUIRED=false`, on `http://localhost:3001`: the Play select shows seven "Label — about N" options, unclipped at 1366×768 and at 400px width; changing it and reloading keeps the value (session); Learn mode select shows the same seven. Screenshot both. Run `tools/verify/ui.mjs`, `guided.mjs`, `review-handoff.mjs`, `layout-stress.mjs`, `overlap.mjs`, `chat.mjs`, `loop.mjs` after updating any that select by the old option text (`grep -n difficulty tools/verify/*.mjs tools/*.mjs`).

---

### Task 10: Docs

**Files:**
- Modify: `CLAUDE.md` — intro paragraph ("slices a 3-move window by difficulty" → the profile pool), §0 handoff row, file map (add `opponent_profiles.py`, `candidate_selection.py`, `tools/profile_sanity.py`, `test_opponent_profiles.py`, migration 010), §5 knobs (none new — knobs live in the profile table), §6 test table (+`test_opponent_profiles.py`), API list, §11 UI state, and a new §37 "Opponent profiles" describing the pipeline, the decision record, the fallback reasons, and the sanity numbers from Task 8.

- [ ] **Step 1: Edit CLAUDE.md.** **Step 2:** `grep -n 'difficulty' CLAUDE.md` — every remaining hit is historical or the legacy column.

---

### Task 11: Full verification

- [ ] Run the complete §6 suite list (with disposable schema, drop at the end) plus `test_opponent_profiles.py`; all suites exit 0.
- [ ] Run `tools/profile_sanity.py --games 3 --plies 40` and paste the table into the final report.
- [ ] `git status` / `git diff --stat` for the user's review. **No commit.**
