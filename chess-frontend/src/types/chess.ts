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
    player_move?: any;
    status?: any;
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