"""
Opponent profiles and the candidate bracket: the profile table and its
frontend mirror, the python-chess annotation of every legal move, and the
profile-weighted sampling that builds the pool Gemini chooses from. Pure -
no Stockfish, no network, no key. The ranked lists are hand-built.

The product claim under test: a weak profile plays like a weak *player* -
drawn to checks and captures, blind to quiet defence, capable of a good
move - not like a random-move generator; and a strong profile stays near
the engine's best without being it every time.
"""
import random
import re
import sys

import chess

import candidate_selection as cs
import guided_play
import opponent_profiles as op
from opponent_profiles import get_profile

results = []


def check(name, ok, detail=""):
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if not ok and detail else ""))
    results.append(ok)


# --- 1. The table --------------------------------------------------------------
check("seven ids in order", op.PROFILE_IDS == ("beginner", "casual", "improving", "club", "advanced", "expert", "master"))
check("default is club", op.DEFAULT_PROFILE_ID == "club" and op.get_profile(None).id == "club")
check("unknown id -> club", op.get_profile("grandmaster").id == "club")
check("non-string -> club", op.get_profile(17).id == "club")
check("profile object passes through", op.get_profile(op.PROFILES["expert"]).id == "expert")
check("known id round-trips", op.get_profile("beginner").approx_elo == 400)
elos = [op.PROFILES[i].approx_elo for i in op.PROFILE_IDS]
check("elo strictly increases", elos == sorted(elos) and len(set(elos)) == 7)
cpls = [op.PROFILES[i].max_cpl for i in op.PROFILE_IDS]
check("max_cpl strictly decreases", cpls == sorted(cpls, reverse=True) and len(set(cpls)) == 7)
temps = [op.PROFILES[i].temperature for i in op.PROFILE_IDS]
check("temperature strictly decreases", temps == sorted(temps, reverse=True))
targets = [op.PROFILES[i].target_cpl for i in op.PROFILE_IDS]
check("target cpl strictly decreases to zero", targets == sorted(targets, reverse=True) and targets[-1] == 0
      and all(t < c for t, c in zip(targets, cpls)))
check("display label", op.display_label("club") == "Club (~1500)")
check("summary keys", set(op.profile_summary("club")) == {"id", "label", "approx_elo", "blurb"})
check("all_summaries in order", [s["id"] for s in op.all_summaries()] == list(op.PROFILE_IDS))
check("every profile has commentary style", all(op.PROFILES[i].commentary_style for i in op.PROFILE_IDS))

ts = open("chess-frontend/src/opponentProfiles.ts", encoding="utf-8").read()
ts_rows = re.findall(r"id:\s*'(\w+)',\s*label:\s*'([^']+)',\s*approxElo:\s*(\d+),\s*blurb:\s*'([^']+)'", ts)
check("ts mirror has seven rows", len(ts_rows) == 7, str(len(ts_rows)))
check("ts mirror matches python",
      [(i, op.PROFILES[i].label, op.PROFILES[i].approx_elo, op.PROFILES[i].blurb) for i in op.PROFILE_IDS]
      == [(i, l, int(e), b) for i, l, e, b in ts_rows])
check("ts default is club", "DEFAULT_PROFILE_ID = 'club'" in ts)


# --- 2. Annotation ------------------------------------------------------------
def ranked_from(fen, scored):
    """A stage-1 shaped list from [(uci, cp | ("mate", n))], in the order given."""
    out = []
    for uci, s in scored:
        if isinstance(s, tuple):
            out.append({"move": uci, "score": None, "mate_in": s[1], "pv": [uci]})
        else:
            out.append({"move": uci, "score": s, "mate_in": None, "pv": [uci]})
    return out


# Scholar's mate: white to move, Qxf7# on the board. Bxf7+ is a check that
# misses it; Qxe5+ and Qxh7 hang the queen (Nc6xe5, Nf6xh7).
MATE_FEN = "r1bqkb1r/pppp1ppp/2n2n2/4p2Q/2B1P3/8/PPPP1PPP/RNB1K1NR w KQkq - 4 4"
assert chess.Board(MATE_FEN).is_valid()
r = ranked_from(MATE_FEN, [("h5f7", ("mate", 1)), ("c4f7", 120), ("h5e5", -300), ("h5h7", -900)])
a = cs.annotate(MATE_FEN, r)
check("annotate keeps order", [c["move"] for c in a] == ["h5f7", "c4f7", "h5e5", "h5h7"])
check("mate has cpl 0 and mate_in 1", a[0]["cpl"] == 0 and a[0]["mate_in"] == 1)
check("missing the mate is a huge cpl", a[1]["cpl"] > 5000, str(a[1]["cpl"]))
check("check detected", a[1]["gives_check"] is True and a[0]["gives_check"] is True and a[3]["gives_check"] is False)
check("capture detected", a[1]["is_capture"] is True and a[2]["is_capture"] is True)
check("san filled", a[0]["san"] == "Qxf7#" and a[1]["san"] == "Bxf7+")
check("Qxh7 hangs the queen", a[3]["hangs_piece"] is True)
check("Qxe5+ hangs the queen", a[2]["hangs_piece"] is True)
check("Qxf7# does not hang", a[0]["hangs_piece"] is False)
check("rank numbered", [c["rank"] for c in a] == [0, 1, 2, 3])
check("not forced", all(c["forced"] is False for c in a))
check("no reference flag", all(c["is_reference"] is False for c in a))

# White knight on c3 is attacked by the b4 pawn and undefended. Ne2 answers
# that; a king shuffle leaves it to be taken.
THREAT_FEN = "4k3/8/8/8/1p6/2N5/8/4K3 w - - 0 1"
assert chess.Board(THREAT_FEN).is_valid()
r = ranked_from(THREAT_FEN, [("c3e2", 0), ("e1d1", -250)])
a = cs.annotate(THREAT_FEN, r)
check("moving the attacked knight answers the threat", a[0]["answers_threat"] is True)
check("king shuffle leaves it hanging", a[1]["answers_threat"] is False and a[1]["hangs_piece"] is True)
check("quiet move is not a capture or check", a[0]["is_capture"] is False and a[0]["gives_check"] is False)

# Black king on a8 in check from Ra1, own pawn on b7: ...Kb8 is the only move.
FORCED_FEN = "k7/1p6/8/8/8/8/8/R6K b - - 0 1"
b = chess.Board(FORCED_FEN)
assert b.is_valid() and b.legal_moves.count() == 1
only = list(b.legal_moves)[0].uci()
a = cs.annotate(FORCED_FEN, ranked_from(FORCED_FEN, [(only, -900)]))
check("single legal move is forced", a[0]["forced"] is True and a[0]["cpl"] == 0)

# Black to move; ...Kh8 walks into Qd8#, ...h6 does not.
WIM_FEN = "6k1/5ppp/8/8/8/8/5PPP/3Q2K1 b - - 0 1"
assert chess.Board(WIM_FEN).is_valid()
r = ranked_from(WIM_FEN, [("h7h6", 0), ("g8h8", ("mate", -1))])
a = cs.annotate(WIM_FEN, r)
check("walks_into_mate flagged", a[1]["walks_into_mate"] is True and a[0]["walks_into_mate"] is False)
check("walking into mate is a huge cpl", a[1]["cpl"] > 5000)

# When every move loses to mate, none of them "walks into" it.
r = ranked_from(WIM_FEN, [("g8f8", ("mate", -2)), ("g8h8", ("mate", -1))])
a = cs.annotate(WIM_FEN, r)
check("already lost: not flagged as walking into mate", not any(c["walks_into_mate"] for c in a))
check("a faster loss costs more than a slower one", a[0]["cpl"] == 0 and a[1]["cpl"] > 0)

check("annotate of empty list is empty", cs.annotate(WIM_FEN, []) == [])

facts = guided_play.candidate_facts(THREAT_FEN, "e1d1")
check("own_loose lists the knight", any("knight on c3" in s for s in facts["own_loose"]), str(facts))
facts = guided_play.candidate_facts(THREAT_FEN, "c3e2")
check("own_loose empty after answering", facts["own_loose"] == [])
text = guided_play.describe_candidates(THREAT_FEN, [{"move": "e1d1"}])
check("describe_candidates mentions own loose piece", "own knight on c3" in text, text)


# --- 3. Eligibility, weighting, sampling -----------------------------------
# A synthetic list on a real position: every legal move, cpl 0, 25, 50 ...
# in ranked order, so what each profile lets through is legible (beginner
# sees 15 moves within its ceiling, club 5, master 1).
SYN_FEN = "r1bqkbnr/pppp1ppp/2n5/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq - 2 3"
sb = chess.Board(SYN_FEN)
legal = [m.uci() for m in sb.legal_moves]
syn = ranked_from(SYN_FEN, [(u, -25 * i) for i, u in enumerate(legal)])


def pools(pid, n=200, seed=1):
    rng = random.Random(seed)
    return [cs.select_pool(SYN_FEN, syn, get_profile(pid), rng)[0] for _ in range(n)]


for pid in op.PROFILE_IDS:
    ps = pools(pid)
    check(f"{pid}: pool never empty, size <= 3", all(0 < len(p) <= 3 for p in ps))
    check(f"{pid}: pool only legal moves", all(c["move"] in legal for p in ps for c in p))
    check(f"{pid}: pool in ranked order", all([c["rank"] for c in p] == sorted(c["rank"] for c in p) for p in ps))
    check(f"{pid}: no reference in pool", all(not c["is_reference"] for p in ps for c in p))
    worst = max(c["cpl"] for p in ps for c in p)
    check(f"{pid}: pool stays within max_cpl", worst <= get_profile(pid).max_cpl, str(worst))

beg = pools("beginner")
beg_bad = sum(1 for p in beg if any(c["cpl"] >= 100 for c in p))
check("beginner pools usually contain a >=100cpl move", beg_bad > 120, str(beg_bad))
beg_top = sum(1 for p in beg if p[0]["rank"] == 0)
check("beginner rarely has the engine move in the pool", beg_top < 100, str(beg_top))
mas_top = sum(1 for p in pools("master") if p[0]["rank"] == 0)
check("master always has the engine move in the pool", mas_top == 200, str(mas_top))
mean_cpl = {pid: sum(c["cpl"] for p in pools(pid) for c in p) / sum(len(p) for p in pools(pid)) for pid in op.PROFILE_IDS}
order = [mean_cpl[pid] for pid in op.PROFILE_IDS]
check("mean pool cpl falls from beginner to master", order == sorted(order, reverse=True),
      " ".join(f"{k}={v:.0f}" for k, v in mean_cpl.items()))
check("pools vary between draws (not a fixed window)",
      len({tuple(c["move"] for c in p) for p in pools("club")}) > 3)
check("every pool entry carries its weight", all(c["weight"] > 0 for p in pools("club") for c in p))
heaviest = [max(p, key=lambda c: c["weight"])["cpl"] for p in pools("beginner")]
check("beginner's likeliest move is usually a real mistake (>=75cpl)",
      sum(1 for c in heaviest if c >= 75) > 120, str(sum(1 for c in heaviest if c >= 75)))
heaviest = [max(p, key=lambda c: c["weight"])["cpl"] for p in pools("master")]
check("master's likeliest move is the best", all(c == 0 for c in heaviest))

# Mate in one on the board. Master always takes it into the pool; a beginner
# finds it about tactical_awareness of the time - and the mate is not the
# only thing they see, so it is not always there.
r = ranked_from(MATE_FEN, [("h5f7", ("mate", 1)), ("c4f7", 120), ("h5e5", -300), ("h5h7", -900),
                           ("d2d3", -400), ("b1c3", -420)])
rng = random.Random(3)
m_has = sum(1 for _ in range(100)
            if any(c["move"] == "h5f7" for c in cs.select_pool(MATE_FEN, r, get_profile("master"), rng)[0]))
b_has = sum(1 for _ in range(300)
            if any(c["move"] == "h5f7" for c in cs.select_pool(MATE_FEN, r, get_profile("beginner"), rng)[0]))
check("master always has the mate", m_has == 100, str(m_has))
rng = random.Random(11)
missed = [cs.select_pool(MATE_FEN, r, get_profile("beginner"), rng) for _ in range(300)]
missed = [(p, i) for p, i in missed if not i["saw_mate"]]
check("a missed mate leaves the mate out of the pool", missed and all(not any(c["move"] == "h5f7" for c in p) for p, _ in missed))
check("a missed mate re-bases cpl on the rest", all(min(c["cpl"] for c in p) == 0 for p, _ in missed))
check("beginner finds the mate sometimes, not always", 30 < b_has < 200, str(b_has))
check("expert always has the mate", all(any(c["move"] == "h5f7" for c in
      cs.select_pool(MATE_FEN, r, get_profile("expert"), rng)[0]) for _ in range(50)))

# Forced move: the pool is that move, whatever the profile.
pool, info = cs.select_pool(FORCED_FEN, ranked_from(FORCED_FEN, [(only, -900)]), get_profile("beginner"))
check("forced move is the whole pool", [c["move"] for c in pool] == [only] and info["n_eligible"] == 1, str(info))

# Walking into mate is a beginner's mistake, not a club player's.
r = ranked_from(WIM_FEN, [("h7h6", 0), ("g7g6", -5), ("f7f6", -10), ("g8h8", ("mate", -1))])
rng = random.Random(5)
imp = sum(1 for _ in range(200)
          if any(c["walks_into_mate"] for c in cs.select_pool(WIM_FEN, r, get_profile("improving"), rng)[0]))
check("improving never walks into mate", imp == 0, str(imp))
clb = sum(1 for _ in range(200)
          if any(c["walks_into_mate"] for c in cs.select_pool(WIM_FEN, r, get_profile("club"), rng)[0]))
check("club never walks into mate", clb == 0, str(clb))

# Hanging a piece: a beginner sometimes does, a master never does when there
# is anything else to play. After 1.e4 e5 2.Nf3 Nc6, Nxe5 and Ng5 both put
# the knight where it is taken for nothing.
r = ranked_from(SYN_FEN, [("f1c4", 0), ("b1c3", -10), ("d2d4", -20), ("f3e5", -150), ("f3g5", -200)])
a = cs.annotate(SYN_FEN, r)
check("fixture: the two knight moves hang", [c["hangs_piece"] for c in a] == [False, False, False, True, True])
rng = random.Random(7)
b_hang = sum(1 for _ in range(200)
             if any(c["hangs_piece"] for c in cs.select_pool(SYN_FEN, r, get_profile("beginner"), rng)[0]))
m_hang = sum(1 for _ in range(200)
             if any(c["hangs_piece"] for c in cs.select_pool(SYN_FEN, r, get_profile("master"), rng)[0]))
c_hang = sum(1 for _ in range(200)
             if any(c["hangs_piece"] for c in cs.select_pool(SYN_FEN, r, get_profile("club"), rng)[0]))
check("beginner sometimes hangs the knight", 20 < b_hang < 200, str(b_hang))
check("master never hangs the knight", m_hang == 0, str(m_hang))
check("club rarely hangs the knight", c_hang < 40, str(c_hang))

# Fewer moves than the pool wants: the whole list comes back.
pool, _ = cs.select_pool(WIM_FEN, ranked_from(WIM_FEN, [("h7h6", 0), ("g7g6", -5)]), get_profile("master"))
check("short list returned whole", len(pool) == 2)
pool, _ = cs.select_pool(WIM_FEN, ranked_from(WIM_FEN, [("h7h6", 0), ("g7g6", -50), ("f7f6", -60)]), get_profile("master"))
check("one clearly best move -> pool of one", [c["move"] for c in pool] == ["h7h6"])
pool, info = cs.select_pool(WIM_FEN, [], get_profile("master"))
check("empty list -> empty pool", pool == [] and info["n_legal"] == 0)

# Every move loses badly: cpl is relative, so the least bad is still cpl 0.
r = ranked_from(WIM_FEN, [("h7h6", -600), ("g7g6", -700), ("f7f6", -800), ("g8f8", -900)])
pool, _ = cs.select_pool(WIM_FEN, r, get_profile("master"))
check("all-bad position: master plays the least bad", [c["move"] for c in pool] == ["h7h6"])
pool, _ = cs.select_pool(WIM_FEN, r, get_profile("beginner"))
check("all-bad position: beginner still gets a pool", 0 < len(pool) <= 3)

# Reference handling.
a = cs.annotate(SYN_FEN, syn)
sub = [a[4], a[5], a[6]]
wr = cs.with_reference(sub, a)
check("reference appended when best absent", len(wr) == 4 and wr[-1]["is_reference"] and wr[-1]["rank"] == 0)
check("strip_reference removes it", [c["move"] for c in cs.strip_reference(wr)] == [c["move"] for c in sub])
check("no reference when best present", len(cs.with_reference([a[0], a[1]], a)) == 2)
check("original entries untouched by with_reference", all(not c["is_reference"] for c in sub))

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
