"""
Re-certify every re-test position against the real engine.

Run:
    /tmp/chessapp/bin/python test_retest_bank.py

Needs Stockfish. Slower than the other pure suites (one two-line search per
position at depth 20), and worth it: this is the file that stops the product
telling a player they got a concept wrong when they did not.

WHAT IS BEING CLAIMED
---------------------
For every entry in `retest_bank.BANK`, at `CONFIRM_DEPTH`:

  1. the position is legal and reachable, and the game is not already over;
  2. the recorded `best_uci` is legal, and its SAN is exactly `best_san`;
  3. the engine's own first choice IS that move; and
  4. the second-best move is at least `MIN_GAP` centipawns worse.

(3) and (4) together are the whole basis for grading an attempt. If either
stops holding - a new Stockfish version, a corrected entry, a position pasted
in by hand - this suite fails and the entry must be removed or re-harvested.
**Do not relax `MIN_GAP` to make this pass.** The number is what "there is one
right answer here" means; lowering it does not make more positions valid, it
makes the pass/fail verdict less true.

The engine is opened directly rather than through `stockfish_service` because
this asks for two lines (`multipv=2`) and the shared service's helpers are
single-line. It closes its own engine in a `finally`.
"""

import chess
import chess.engine

import retest_bank

ENGINE_PATH = "/usr/games/stockfish"

PASSED = 0
FAILED = 0


def check(label, condition, detail=""):
    global PASSED, FAILED
    if condition:
        PASSED += 1
        print(f"PASS  {label}")
    else:
        FAILED += 1
        print(f"FAIL  {label}" + (f" - {detail}" if detail else ""))


def _cp(info, pov) -> int:
    """One comparable number from `pov`'s point of view, mate included."""
    score = info["score"].pov(pov)
    if score.is_mate():
        mate = score.mate()
        return 30000 - mate * 10 if mate > 0 else -30000 - mate * 10
    return score.score()


entries = retest_bank.all_entries()
check("the bank is not empty", len(entries) > 0, f"{len(entries)} entries")

engine = chess.engine.SimpleEngine.popen_uci(ENGINE_PATH)
try:
    for theme, entry in entries:
        fen = entry["fen"]
        tag = f"{theme} {entry['best_san']}"

        try:
            board = chess.Board(fen)
        except ValueError as exc:
            check(f"{tag}: the FEN parses", False, str(exc))
            continue

        if not board.is_valid():
            check(f"{tag}: the position is reachable", False, fen)
            continue
        if board.is_game_over():
            check(f"{tag}: the game is not already over", False, fen)
            continue

        try:
            best = chess.Move.from_uci(entry["best_uci"])
        except ValueError as exc:
            check(f"{tag}: best_uci parses", False, str(exc))
            continue
        if best not in board.legal_moves:
            check(f"{tag}: the recorded answer is legal", False, entry["best_uci"])
            continue

        # The SAN the UI shows after an attempt must be the SAN of the move it
        # graded against. A mismatch here would tell the player the right move
        # was something other than the one the check accepted.
        check(f"{tag}: best_san matches best_uci", board.san(best) == entry["best_san"],
              f"{board.san(best)} != {entry['best_san']}")

        info = engine.analyse(board, chess.engine.Limit(depth=retest_bank.CONFIRM_DEPTH), multipv=2)
        if len(info) < 2:
            check(f"{tag}: the engine offers a second line to compare against", False)
            continue

        pv_first = info[0].get("pv") or []
        pv_second = info[1].get("pv") or []
        if not pv_first or not pv_second:
            check(f"{tag}: both lines have a move", False)
            continue

        check(f"{tag}: the engine's first choice is the recorded answer",
              pv_first[0] == best, f"engine prefers {board.san(pv_first[0])}")

        gap = _cp(info[0], board.turn) - _cp(info[1], board.turn)
        check(f"{tag}: the second-best move is at least {retest_bank.MIN_GAP}cp worse",
              gap >= retest_bank.MIN_GAP, f"gap is {gap}cp")

    # Cheap structural claims, checked here too so this file alone is enough
    # to trust the table.
    check("no two entries share a position",
          len({e["fen"] for _, e in entries}) == len(entries))
    check("every entry records the gap it was certified at",
          all(isinstance(e.get("gap"), int) for _, e in entries))
    check("every entry has a prompt",
          all(isinstance(e.get("prompt"), str) and e["prompt"].strip() for _, e in entries))
finally:
    engine.quit()

print(f"\n{PASSED}/{PASSED + FAILED} passed")
raise SystemExit(1 if FAILED else 0)
