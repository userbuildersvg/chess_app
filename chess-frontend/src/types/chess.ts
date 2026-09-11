import type { MoveQuality } from '../moveQuality';

export interface GameState {
    fen: string;
    turn: 'white' | 'black';
    legal_moves: string[];
    move_history: string[];
    san_history: string[];
    is_check: boolean;
    is_checkmate: boolean;
    is_stalemate: boolean;
    is_game_over: boolean;
    move_count: number;
    piece_count: number;
    castling_rights: {
        white_kingside: boolean;
        white_queenside: boolean;
        black_kingside: boolean;
        black_queenside: boolean;
    };
}

export interface PositionEval {
    score: number | null;
    mate_in: number | null;
}

export interface HistoryEntry {
    player: string;
    move: string;
    san: string;
    explanation?: string | null;
    quality?: MoveQuality | null;
}

export interface PlayerMovePayload {
    move?: string;
    san?: string;
    [key: string]: unknown;
}

/** Common envelope returned by the game-control endpoints. */
export interface GameActionResult {
    success: boolean;
    message?: string;
    error?: string;
    status?: GameState;
    eval?: PositionEval;
    history?: HistoryEntry[];
    difficulty?: number;
    ai_scheduled?: boolean;
    game_mode?: 'human_vs_ai' | 'ai_vs_ai';
    ai_vs_ai_running?: boolean;
}

export interface MoveResult {
    success: boolean;
    move?: string;
    san?: string;
    fen?: string;
    error?: string;
    is_game_over?: boolean;
    winner?: 'white' | 'black' | 'draw';
    model_move?: {
        success: boolean;
        message?: string;
        ai_scheduled?: boolean;
        skip_ai?: boolean;
        manual_ai_required?: boolean;
        move?: string;
        san?: string;
        explanation?: string;
    };
    player_move?: string | PlayerMovePayload;
    status?: GameState;
}


export interface LangflowConfig {
    flowId: string;
    isConnected: boolean;
    status: 'idle' | 'connecting' | 'connected' | 'error' | 'thinking';
}


export interface ChessMove {
    from: string;
    to: string;
    promotion?: string;
}
