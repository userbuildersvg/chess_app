import { Chess } from 'chess.js';
import type { GameState, MoveResult, ChessMove } from '../types/chess';

class ChessService {
    private game: Chess;

    constructor() {
        this.game = new Chess();
    }

    getGameState(): GameState {
        return {
            fen: this.game.fen(),
            turn: this.game.turn() === 'w' ? 'white' : 'black',
            legal_moves: this.game.moves({ verbose: true }).map(move => move.from + move.to + (move.promotion || '')),
            move_history: this.game.history(),
            san_history: this.game.history({ verbose: false }),
            is_check: this.game.isCheck(),
            is_checkmate: this.game.isCheckmate(),
            is_stalemate: this.game.isStalemate(),
            is_game_over: this.game.isGameOver(),
            move_count: this.game.history().length,
            piece_count: Object.keys(this.game.board().flat().filter(Boolean)).length,
            castling_rights: {
                white_kingside: this.game.getCastlingRights('w').k,
                white_queenside: this.game.getCastlingRights('w').q,
                black_kingside: this.game.getCastlingRights('b').k,
                black_queenside: this.game.getCastlingRights('b').q,
            }
        };
    }

    loadPosition(fen: string): boolean {
        try {
            this.game.load(fen);
            return true;
        } catch (error) {
            console.error('Failed to load position:', error);
            return false;
        }
    }

    async makePlayerMove(move: ChessMove | string): Promise<MoveResult> {
        const moveStr = typeof move === 'string' ? move : `${move.from}${move.to}${move.promotion || ''}`;
        
        try {
            const response = await fetch('/api/move', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({ move: moveStr })
            });

            const data = await response.json();
            
            if (data.success) {
                const newFen = data.status?.fen;
                if (newFen) {
                    this.game.load(newFen);
                }
                
                return {
                    success: true,
                    move: data.player_move?.move || data.player_move,
                    fen: newFen,
                    is_game_over: data.status?.is_game_over || false,
                    winner: this.getWinner(),
                    model_move: data.model_move,
                    player_move: data.player_move,
                    status: data.status
                };
            } else {
                return {
                    success: false,
                    error: data.message || 'Move failed'
                };
            }
        } catch (error) {
            console.error('Network error in makePlayerMove:', error);
            return {
                success: false,
                error: error instanceof Error ? error.message : 'Network error'
            };
        }
    }

    async resetGameOnServer(): Promise<boolean> {
        try {
            const response = await fetch('/api/reset', {
                method: 'GET'
            });

            const data = await response.json();
            
            if (data.success) {
                this.game.reset();
                return true;
            }
            return false;
        } catch {
            return false;
        }
    }

    private getWinner(): 'white' | 'black' | 'draw' | undefined {
        if (!this.game.isGameOver()) return undefined;
        
        if (this.game.isCheckmate()) {
            return this.game.turn() === 'w' ? 'black' : 'white';
        }
        
        return 'draw';
    }

    getPiece(square: string) {
        return this.game.get(square as any);
    }

    getLegalMoves(square: string): string[] {
        const moves = this.game.moves({
            square: square as any,
            verbose: true
        });
        return moves.map(move => move.to);
    }
}

export const chessService = new ChessService();