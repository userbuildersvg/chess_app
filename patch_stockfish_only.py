#!/usr/bin/env python3
import sys

with open("stockfish_service.py", "r", encoding="utf-8") as f:
    src = f.read()

old = '''            return (1, entry["score"])

        ranked.sort(key=sort_key, reverse=True)

        logger.info(f"♟️ Ranked {len(ranked)} moves, top: {ranked[0] if ranked else 'none'}")
        return ranked[:top_n]


# Global instance
stockfish_service = StockfishService()'''

new = '''            return (1, entry["score"])

        ranked.sort(key=sort_key, reverse=True)

        logger.info(f"♟️ Ranked {len(ranked)} moves, top: {ranked[0] if ranked else 'none'}")
        return ranked[:top_n]

    def get_position_evaluation(self, fen: str) -> dict:
        """
        Evaluate the given position directly (no move applied) from White's
        absolute perspective - the standard convention for an eval bar
        (positive = White is better, negative = Black is better), regardless
        of whose turn it actually is.

        Returns {"score": cp, "mate_in": mate_in}. "score" is centipawns
        (None if a forced mate was found instead). "mate_in" is the number
        of moves to mate (positive = White mates, negative = Black mates),
        None if no forced mate was found.
        """
        board = chess.Board(fen)
        with chess.engine.SimpleEngine.popen_uci(self.path) as engine:
            info = engine.analyse(board, chess.engine.Limit(depth=self.depth))
            score = info["score"].pov(chess.WHITE)
            mate_in = score.mate()
            cp = score.score() if mate_in is None else None
        return {"score": cp, "mate_in": mate_in}


# Global instance
stockfish_service = StockfishService()'''

count = src.count(old)
if count != 1:
    print(f"❌ expected exactly 1 match, found {count}. Aborting - no changes written.")
    sys.exit(1)

with open("stockfish_service.py", "w", encoding="utf-8") as f:
    f.write(src.replace(old, new, 1))

print("✅ stockfish_service.py patched successfully")
