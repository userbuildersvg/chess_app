"""
Do the opponent profiles actually play differently - and differently like
people, not like dice?

Plays N games per profile against `master` (the profile as White, master as
Black), through app.decide_ai_move with no Gemini key in-process, so the
fallback plays the highest-weight move of each profile's pool. What is measured
is therefore the POOL - the likeliest move in what Gemini is offered - which
is the deterministic half of the design and the half a tuning change moves.

Per profile it prints mean and median centipawn loss of the moves played,
the blunder rate (>= 200 cpl), how often the move was the engine's own top
choice, how often the fallback had to be used (should be 100% here, no key),
and the number of illegal moves (must be 0). Then the acceptance lines:

  - mean cpl strictly decreases from beginner to master
  - beginner plays the engine's top move under 40% of the time
  - master's mean cpl is under 10
  - no illegal move anywhere

A diagnostic, not a test: it spends real engine time (about a minute per
game at the default depth) and its numbers move with Stockfish's own noise.
Run it after changing a knob in opponent_profiles.py and paste the table
into CLAUDE.md.

    DISABLE_LANGFLOW=true /tmp/chessapp/bin/python tools/profile_sanity.py --games 2 --plies 30
"""
import argparse
import asyncio
import os
import statistics
import sys

os.environ.setdefault("DISABLE_LANGFLOW", "true")
os.environ["GEMINI_API_KEY"] = ""
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import chess  # noqa: E402

import app  # noqa: E402
from opponent_profiles import PROFILE_IDS  # noqa: E402


async def play(profile_id: str, plies: int, opponent: str = "master") -> dict:
    board = chess.Board()
    cpls, top, fallback, illegal = [], 0, 0, 0
    for ply in range(plies):
        if board.is_game_over():
            break
        mover = "white" if board.turn else "black"
        pid = profile_id if board.turn else opponent
        move, _expl, _source, d = await app.decide_ai_move(board.fen(), mover, profile=pid, use_learning=False)
        try:
            m = chess.Move.from_uci(move)
        except ValueError:
            m = None
        if m is None or m not in board.legal_moves:
            illegal += 1
            break
        if board.turn:  # the profile under test
            # Capped: a walk into mate is scored on the mate scale (~10000)
            # and one of them would swamp a mean of thirty moves.
            cpls.append(min(d["cpl"] if d["cpl"] is not None else 0, 1000))
            top += 1 if d["rank_overall"] == 0 else 0
            fallback += 1 if d["selected_by"] == "fallback" else 0
        board.push(m)
    return {"cpls": cpls, "top": top, "fallback": fallback, "illegal": illegal, "plies": len(cpls)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", type=int, default=2)
    ap.add_argument("--plies", type=int, default=30, help="half-moves per game")
    ap.add_argument("--profiles", default=",".join(PROFILE_IDS))
    args = ap.parse_args()
    app.gemini_move_service.api_key = ""

    rows = {}
    for pid in args.profiles.split(","):
        agg = {"cpls": [], "top": 0, "fallback": 0, "illegal": 0, "plies": 0}
        for g in range(args.games):
            r = asyncio.run(play(pid, args.plies))
            for k in agg:
                agg[k] = agg[k] + r[k] if isinstance(agg[k], list) else agg[k] + r[k]
            print(f"  {pid} game {g + 1}: {r['plies']} moves, mean cpl {statistics.mean(r['cpls']) if r['cpls'] else 0:.0f}",
                  file=sys.stderr)
        n = max(1, agg["plies"])
        rows[pid] = {
            "mean_cpl": statistics.mean(agg["cpls"]) if agg["cpls"] else 0.0,
            "median_cpl": statistics.median(agg["cpls"]) if agg["cpls"] else 0.0,
            "blunder_rate": sum(1 for c in agg["cpls"] if c >= 200) / n,
            "top1_share": agg["top"] / n,
            "fallback_share": agg["fallback"] / n,
            "illegal": agg["illegal"],
            "moves": agg["plies"],
        }

    print()
    print(f"{'profile':<10}{'moves':>6}{'mean_cpl':>10}{'median':>8}{'blunder%':>10}{'top1%':>8}{'fallback%':>11}{'illegal':>9}")
    for pid, r in rows.items():
        print(f"{pid:<10}{r['moves']:>6}{r['mean_cpl']:>10.1f}{r['median_cpl']:>8.0f}"
              f"{100 * r['blunder_rate']:>10.0f}{100 * r['top1_share']:>8.0f}{100 * r['fallback_share']:>11.0f}{r['illegal']:>9}")

    ok = True
    means = [rows[p]["mean_cpl"] for p in PROFILE_IDS if p in rows]
    ordered = all(a > b for a, b in zip(means, means[1:]))
    print(f"\n{'PASS' if ordered else 'FAIL'}  mean cpl strictly decreases in profile order: {[round(m) for m in means]}")
    ok &= ordered
    if "beginner" in rows:
        c = rows["beginner"]["top1_share"] < 0.40
        print(f"{'PASS' if c else 'FAIL'}  beginner plays the engine's top move under 40% of the time ({100 * rows['beginner']['top1_share']:.0f}%)")
        ok &= c
    if "master" in rows:
        c = rows["master"]["mean_cpl"] < 10
        print(f"{'PASS' if c else 'FAIL'}  master mean cpl under 10 ({rows['master']['mean_cpl']:.1f})")
        ok &= c
    c = all(r["illegal"] == 0 for r in rows.values())
    print(f"{'PASS' if c else 'FAIL'}  no illegal moves")
    ok &= c
    app.stockfish_service.close()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
