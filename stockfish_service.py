"""
Stockfish integration for chess move generation
"""
import chess
import chess.engine
import logging

logger = logging.getLogger(__name__)

STOCKFISH_PATH = "/usr/games/stockfish"
SEARCH_DEPTH = 15  # how many moves deep Stockfish calculates


class StockfishService:
    def __init__(self, path: str = STOCKFISH_PATH, depth: int = SEARCH_DEPTH):
        self.path = path
        self.depth = depth

    def get_best_move(self, fen: str) -> str:
        """
        Given a FEN position, return the single best move in UCI format.
        """
        board = chess.Board(fen)

        with chess.engine.SimpleEngine.popen_uci(self.path) as engine:
            result = engine.play(board, chess.engine.Limit(depth=self.depth))
            move = result.move

            if move is None:
                raise ValueError("Stockfish returned no move (game may be over)")

            logger.info(f"♟️ Stockfish selected move: {move.uci()}")
            return move.uci()

    def get_ranked_moves(self, fen: str, top_n: int = 5) -> list[dict]:
        """
        Given a FEN position, return the top N legal moves ranked by evaluation,
        best first. Each entry: {"move": "e2e4", "score": 34, "mate_in": None}

        Score is in centipawns from the perspective of the side to move
        (positive = good for the side to move). If a forced mate is found,
        "mate_in" will be set instead (positive = winning, negative = losing).
        """
        board = chess.Board(fen)

        ranked = []
        with chess.engine.SimpleEngine.popen_uci(self.path) as engine:
            legal_moves = list(board.legal_moves)

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
                board.pop()

        def sort_key(entry):
            if entry["mate_in"] is not None:
                if entry["mate_in"] > 0:
                    return (2, -entry["mate_in"])
                else:
                    return (0, entry["mate_in"])
            return (1, entry["score"])

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
stockfish_service = StockfishService()
