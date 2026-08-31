#!/usr/bin/env python3
"""
Fixes the AI failing to play an available forced mate (e.g. not playing
Qh4# after a fool's-mate setup). Root cause: get_ranked_moves() evaluates
each candidate move by pushing it and calling engine.analyse() on the
RESULTING position. When a candidate move itself delivers checkmate, the
resulting position has zero legal replies - a degenerate case where
Stockfish's reported score doesn't sort correctly against sort_key()'s
"mate_in > 0 = winning" bucket, so a move that had JUST delivered mate was
sinking to the very BOTTOM of the ranked list instead of the top. That
kept it out of the difficulty window entirely, so Gemini never even saw it
as a candidate.

Fix: short-circuit checkmate-delivering (and other game-ending) candidate
moves before calling engine.analyse() at all, since there's nothing left
to search - checkmate is unconditionally the best possible move, and any
other game-ending move (stalemate, insufficient material, repetition,
50-move rule) is a draw worth 0.

Run this from the repo root:
    python3 patch_matefind.py
"""
import sys

def patch_file(path, replacements):
    with open(path, "r", encoding="utf-8") as f:
        src = f.read()

    for old, new, label in replacements:
        count = src.count(old)
        if count != 1:
            print(f"❌ {path}: expected exactly 1 match for '{label}', found {count}. Aborting - no changes written.")
            sys.exit(1)
        src = src.replace(old, new, 1)

    with open(path, "w", encoding="utf-8") as f:
        f.write(src)
    print(f"✅ {path}: applied {len(replacements)} patch(es)")


stockfish_replacements = [
    (
'''            legal_moves = list(board.legal_moves)

            for move in legal_moves:
                board.push(move)
                info = engine.analyse(board, chess.engine.Limit(depth=self.depth))
                score = info["score"].pov(not board.turn)

                mate_in = score.mate()
                cp = score.score() if mate_in is None else None

                ranked.append({
                    "move": move.uci(),
                    "score": cp,
                    "mate_in": mate_in
                })
                board.pop()''',
'''            legal_moves = list(board.legal_moves)

            for move in legal_moves:
                board.push(move)

                if board.is_checkmate():
                    # This move delivers checkmate immediately - the best
                    # possible outcome, full stop. Skip engine.analyse():
                    # a position with no legal replies is a degenerate case
                    # for the engine (Stockfish reports a "mate 0"-style
                    # score for the mated side that doesn't sort correctly
                    # against a normal mate_in > 0 winning score below - it
                    # was sinking forced-mate moves like Qh4# to the BOTTOM
                    # of the ranked list instead of the top), and there's
                    # nothing left to search anyway.
                    ranked.append({
                        "move": move.uci(),
                        "score": None,
                        "mate_in": 1
                    })
                elif board.is_game_over():
                    # Any other game-ending move (stalemate, insufficient
                    # material, repetition, 50-move rule) is a draw - same
                    # degenerate "no legal replies to analyse" case, but
                    # worth 0 instead of a win.
                    ranked.append({
                        "move": move.uci(),
                        "score": 0,
                        "mate_in": None
                    })
                else:
                    info = engine.analyse(board, chess.engine.Limit(depth=self.depth))
                    score = info["score"].pov(not board.turn)

                    mate_in = score.mate()
                    cp = score.score() if mate_in is None else None

                    ranked.append({
                        "move": move.uci(),
                        "score": cp,
                        "mate_in": mate_in
                    })
                board.pop()''',
        "short-circuit checkmate/game-over candidates in get_ranked_moves()",
    ),
]

patch_file("stockfish_service.py", stockfish_replacements)
print("🎉 All patches applied successfully.")
