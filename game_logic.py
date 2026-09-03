import chess
import chess.svg
from config import BOARD_SIZE

class ChessGame:
    def __init__(self):
        self.board = chess.Board()
        self.game_history = []
    
    def make_player_move(self, move_str):
        """Make player move"""
        try:
            move = chess.Move.from_uci(move_str)
            if move in self.board.legal_moves:
                san = self.board.san(move)
                self.board.push(move)
                self.game_history.append({
                    'player': 'human',
                    'move': move_str,
                    'san': san
                })
                return {'success': True, 'message': f'Move {move_str} played'}
            else:
                return {'success': False, 'message': f'Invalid move: {move_str}'}
        except Exception as e:
            return {'success': False, 'message': f'Error parsing move: {move_str}'}
    
    def make_langflow_move(self, move_str, san=None, explanation=None):
        """Make AI move from Langflow"""
        try:
            move = chess.Move.from_uci(move_str)
            
            if move in self.board.legal_moves:
                if not san:
                    san = self.board.san(move)
                self.board.push(move)
                self.game_history.append({
                    'player': 'langflow',
                    'move': move_str,
                    'san': san,
                    'explanation': explanation
                })
                
                return {
                    'success': True,
                    'message': f'Langflow played: {san} ({move_str})',
                    'move': move_str,
                    'san': san,
                    'explanation': explanation
                }
            else:
                return {
                    'success': False,
                    'message': f'Langflow suggested invalid move: {move_str}',
                    'suggested_move': move_str,
                    'legal_moves': [m.uci() for m in self.board.legal_moves]
                }
                
        except Exception as e:
            return {
                'success': False,
                'message': f'Error processing move: {move_str}',
                'error': str(e)
            }
    
    def get_game_status(self):
        """Get current game status"""
        return {
            'fen': self.board.fen(),
            'turn': 'white' if self.board.turn else 'black',
            'legal_moves': [move.uci() for move in self.board.legal_moves],
            'is_check': self.board.is_check(),
            'is_checkmate': self.board.is_checkmate(),
            'is_stalemate': self.board.is_stalemate(),
            'is_game_over': self.board.is_game_over(),
            'move_count': len(self.game_history)
        }
    
    def get_game_state(self):
        """Get complete game state for Langflow integration"""
        return {
            'fen': self.board.fen(),
            'turn': 'white' if self.board.turn else 'black',
            'legal_moves': [move.uci() for move in self.board.legal_moves],
            'move_history': [entry['move'] for entry in self.game_history],
            'san_history': [entry['san'] for entry in self.game_history if 'san' in entry],
            'is_check': self.board.is_check(),
            'is_checkmate': self.board.is_checkmate(),
            'is_stalemate': self.board.is_stalemate(),
            'is_game_over': self.board.is_game_over(),
            'move_count': len(self.game_history),
            'piece_count': len(self.board.piece_map()),
            'castling_rights': {
                'white_kingside': self.board.has_kingside_castling_rights(chess.WHITE),
                'white_queenside': self.board.has_queenside_castling_rights(chess.WHITE),
                'black_kingside': self.board.has_kingside_castling_rights(chess.BLACK),
                'black_queenside': self.board.has_queenside_castling_rights(chess.BLACK)
            }
        }
    
    def reset_game(self):
        """Reset game"""
        self.board = chess.Board()
        self.game_history = []
    
    # Helper methods for API compatibility
    def get_fen(self):
        """Get current FEN position"""
        return self.board.fen()
    
    def get_current_turn(self):
        """Get current player turn"""
        return 'white' if self.board.turn else 'black'
    
    def get_legal_moves_uci(self):
        """Get legal moves in UCI format"""
        return [move.uci() for move in self.board.legal_moves]
    
    def is_game_over(self):
        """Check if game is over"""
        return self.board.is_game_over()
    
    def is_in_check(self):
        """Check if current player is in check"""
        return self.board.is_check()
    
    def is_checkmate(self):
        """Check if current position is checkmate"""
        return self.board.is_checkmate()
    
    def is_stalemate(self):
        """Check if current position is stalemate"""
        return self.board.is_stalemate()
    
    def make_move(self, move_str):
        """Make a move (generic method for AI)"""
        return self.make_langflow_move(move_str)