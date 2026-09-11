// The shapes /api/postmortem/* returns. Mirrors postmortem_state.to_dict(),
// postmortem_api._state() and postmortem_analysis.build_evidence().
//
// Written as types rather than validated at runtime for the same reason the
// sandbox's are: the backend owns every one of these values, the frontend
// never constructs one, and a mismatch is a build error rather than a runtime
// surprise. Every optional field here is optional in the backend too - an
// unanalysed move really does have no grade yet.

import type { MoveQuality } from '../moveQuality';

/** An evaluation from White's absolute point of view. Positive favours White. */
export interface Evaluation {
    score: number | null;    // centipawns, null when a forced mate was found
    mate_in: number | null;  // moves to mate; positive = White mates, 0 = mate on the board
}

/**
 * Everything the engine established about one half-move.
 *
 * This is the evidence packet - the same object the coach is given. Nothing in
 * the UI may state a number that is not in here: if a field is null, the
 * engine did not produce it and the interface says so rather than filling it.
 */
export interface MoveEvidence {
    ply: number;
    san: string;
    uci: string;
    color: 'white' | 'black';
    phase: 'opening' | 'middlegame' | 'endgame';
    fen_before: string;
    fen_after: string;
    eval_before: Evaluation;
    eval_after: Evaluation;
    best_move: string | null;
    best_san: string | null;
    pv_san: string[];
    cpl: number | null;
    quality: MoveQuality | null;
    depth: number | null;
}

/** One row of the move list: a half-move of the game as it was played. */
export interface MoveRow {
    ply: number;
    move_number: number;
    color: 'white' | 'black';
    san: string;
    node_id: string;
    fen: string;
    quality: MoveQuality | null;
    /** A what-if has been explored instead of this move. */
    has_branch: boolean;
}

export interface BoardStatus {
    state: 'playing' | 'check' | 'checkmate' | 'stalemate' | 'draw';
    winner: 'white' | 'black' | null;
    reason?: string;
}

export interface Termination {
    kind: 'checkmate' | 'stalemate' | 'draw' | 'decisive' | 'unfinished';
    result: string;
    detail: string | null;
}

export type ScanStatus = 'idle' | 'running' | 'done' | 'failed';

export interface ScanProgress {
    status: ScanStatus;
    analysed: number;
    total: number;
    depth: number | null;
    /** Written for a person: shown as-is when a scan stops part-way. */
    error: string | null;
}

export interface SideAccuracy {
    accuracy: number | null;
    counts: Record<string, number>;
    /** Decisions contributing to accuracy; book/forced moves are excluded. */
    graded: number;
    scored: number;
    analysed: number;
    total: number;
    skipped: number;
    excluded_from_score: Record<string, number>;
}

export interface AnalysisCoverage {
    scope: 'full_game' | 'partial_game';
    analysed_moves: number;
    total_moves: number;
    scored_decisions: number;
    skipped_moves: number;
    skipped_reasons: Record<string, number>;
    score_exclusions: Record<string, number>;
}

export interface TurningPoint {
    ply: number;
    san: string;
    color: 'white' | 'black';
    cpl: number | null;
    label: string;
    phase: string;
}

export interface GameSummary {
    white: SideAccuracy;
    black: SideAccuracy;
    coverage: AnalysisCoverage;
    /** Biggest losses first by centipawn, presented in game order. */
    turning_points: TurningPoint[];
    depth: number | null;
    /** Grades this pass cannot detect, declared rather than silently missing. */
    grades_unavailable: string[];
}

export interface CurvePoint {
    ply: number;
    node_id: string;
    san: string | null;
    score: number | null;
    mate_in: number | null;
}

/** A node of the tree - a position, and the move that produced it. */
export interface PostMortemNode {
    id: string;
    parent_id: string | null;
    children: string[];
    move: string | null;
    san: string | null;
    fen: string;
    turn: 'white' | 'black';
    mover: 'white' | 'black' | null;
    source: string;
    explanation: string | null;
}

/** The whole review, as every mutating endpoint returns it. */
export interface PostMortemState {
    game_id: string;
    source_name: string;
    /** How many games the imported file held; the first is the one loaded. */
    game_count: number;
    headers: Record<string, string>;
    result: string;
    termination: Termination;
    start_fen: string;
    from_setup: boolean;
    total_plies: number;

    fen: string;
    turn: 'white' | 'black';
    current_id: string;
    ply: number;
    legal_moves: string[];
    line_san: string[];
    status: BoardStatus;

    /** False while exploring a what-if. */
    on_mainline: boolean;
    branch_point: string | null;
    branch_ply: number | null;
    branch_line_san: string[];

    scan: ScanProgress;
    summary: GameSummary | null;
    created_at: number;
    last_active: number;

    moves: MoveRow[];
    node: PostMortemNode;
    /** The evidence for the move on the board now, when we have it. */
    analysis: MoveEvidence | null;

    // Present on the responses that played something.
    played?: PostMortemNode;
    branched_from?: string;
    selection_source?: 'gemini' | 'stockfish_fallback';
}

export interface AnalysisReport {
    game_id: string;
    scan: ScanProgress;
    summary: GameSummary | null;
    moves: MoveRow[];
    curve: CurvePoint[];
}

export interface PostMortemChatTurn {
    role: 'user' | 'model';
    text: string;
}

export interface PostMortemChatReply {
    game_id: string;
    node_id: string;
    reply: string;
    history: PostMortemChatTurn[];
}
