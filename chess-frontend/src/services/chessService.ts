import { Chess } from 'chess.js';
import type { GameState, MoveResult, ChessMove } from '../types/chess';
import { apiFetch } from './http';
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
    /**
     * Play a move on the LOCAL board only, and say whether it was legal.
     *
     * This exists for perceived responsiveness, not for correctness. Before
     * it, a player's own move did not appear until the server had answered:
     * the click cost a `/api/status` round trip to re-sync, then a `/api/move`
     * round trip, and only then did the piece leave the square it was clicked
     * on. On a warm local backend that is a few hundred milliseconds of board
     * that looks frozen; on a sleeping Render instance it is seconds.
     *
     * So the move is shown here first and confirmed by the server after. The
     * authority has not moved an inch - python-chess still validates every
     * move, and the response overwrites whatever this produced (see
     * ChessBoard.makePlayerMove, which reloads the server's own position on
     * any failure). What this can do is show a move the server is going to
     * accept a moment sooner, and it can only do it for a move chess.js
     * already agrees is legal in the position the server just gave us.
     */
    applyLocalMove(move: ChessMove): boolean {
        try {
            const result = this.game.move({
                from: move.from,
                to: move.to,
                promotion: move.promotion,
            });
            return !!result;
        } catch {
            // chess.js throws on an illegal move rather than returning null.
            // False here means the optimistic step is simply skipped and the
            // board waits for the server, which is the old behaviour - the one
            // thing that must never happen is a board showing a move that is
            // not going to be played.
            return false;
        }
    }
    async makePlayerMove(move: ChessMove | string): Promise<MoveResult> {
        const moveStr = typeof move === 'string' ? move : `${move.from}${move.to}${move.promotion || ''}`;
        try {
            const response = await apiFetch('/api/move', {
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
            // POST, not GET: resetting throws away a game in progress, and
            // a state-changing GET is reachable from any other site that can
            // make this browser follow a URL.
            const response = await apiFetch('/api/reset', {
                method: 'POST'
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
    // Starts a fresh game with the human playing `color`. If the human
    // picks black, the backend immediately schedules White's (the AI's)
    // opening move in the background - callers should check
    // `ai_scheduled` on the result and start polling if it's true, the
    // same way makePlayerMove's ai_scheduled flag is handled.
    async setPlayerColor(color: 'white' | 'black'): Promise<any> {
        try {
            const response = await apiFetch('/api/set-color', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({ color })
            });
            const data = await response.json();
            if (data.success && data.status?.fen) {
                this.game.load(data.status.fen);
            }
            return data;
        } catch (error) {
            console.error('Network error in setPlayerColor:', error);
            return {
                success: false,
                error: error instanceof Error ? error.message : 'Network error'
            };
        }
    }
    async startAiVsAi(): Promise<any> {
        try {
            const response = await apiFetch('/api/ai-vs-ai/start', { method: 'POST' });
            const data = await response.json();
            if (data.success && data.status?.fen) {
                this.game.load(data.status.fen);
            }
            return data;
        } catch (error) {
            console.error('Network error in startAiVsAi:', error);
            return {
                success: false,
                error: error instanceof Error ? error.message : 'Network error'
            };
        }
    }
    async pauseAiVsAi(): Promise<any> {
        try {
            const response = await apiFetch('/api/ai-vs-ai/pause', { method: 'POST' });
            return await response.json();
        } catch (error) {
            console.error('Network error in pauseAiVsAi:', error);
            return {
                success: false,
                error: error instanceof Error ? error.message : 'Network error'
            };
        }
    }
    async resumeAiVsAi(): Promise<any> {
        try {
            const response = await apiFetch('/api/ai-vs-ai/resume', { method: 'POST' });
            return await response.json();
        } catch (error) {
            console.error('Network error in resumeAiVsAi:', error);
            return {
                success: false,
                error: error instanceof Error ? error.message : 'Network error'
            };
        }
    }
    async stepAiVsAi(): Promise<any> {
        try {
            const response = await apiFetch('/api/ai-vs-ai/step', { method: 'POST' });
            const data = await response.json();
            if (data.success && data.status?.fen) {
                this.game.load(data.status.fen);
            }
            return data;
        } catch (error) {
            console.error('Network error in stepAiVsAi:', error);
            return {
                success: false,
                error: error instanceof Error ? error.message : 'Network error'
            };
        }
    }
    async exitAiVsAi(): Promise<any> {
        try {
            const response = await apiFetch('/api/ai-vs-ai/exit', { method: 'POST' });
            return await response.json();
        } catch (error) {
            console.error('Network error in exitAiVsAi:', error);
            return {
                success: false,
                error: error instanceof Error ? error.message : 'Network error'
            };
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
    async fetchGameStateFromServer(): Promise<GameState | null> {
        try {
            const response = await apiFetch('/api/status');
            const data = await response.json();
            if (data.success && data.status?.fen) {
                this.game.load(data.status.fen);
                return this.getGameState();
            }
            return null;
        } catch (error) {
            console.error('Failed to fetch game state:', error);
            return null;
        }
    }
}
export const chessService = new ChessService();
