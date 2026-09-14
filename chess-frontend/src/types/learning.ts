// Types for the learning loop. These mirror `learning_loop_api.py` exactly -
// where a name here differs from the wire format it is a bug, not a
// convenience, for the same reason `types/sandbox.ts` says so.
//
// Note in particular what is NOT in `RetestPosition`: the answer. The server
// never sends `best_uci` or `best_san` with the question (see
// `retest_bank.public`), so there is no shape here that could hold it and no
// way for a curious devtools user to read the answer off the network tab.

/** One of the eight controlled themes. The server is the source of the list. */
export interface LearningTheme {
    id: string;
    label: string;
    description: string;
    /** The check to make next time. From the taxonomy, identical every time. */
    check: string;
    /** Whether a verified re-test position exists. Five of eight are false. */
    practice_available: boolean;
}

export interface IntentPreset {
    id: string;
    label: string;
}

export interface LearningReference {
    themes: LearningTheme[];
    intent_presets: IntentPreset[];
    statuses: string[];
}

/**
 * One piece of evidence behind a card: a copy of the engine's own packet for
 * the move, plus where it came from. This is what "why do you think this?"
 * renders, and every field in it was produced by Stockfish or typed by the
 * player - none of it by a model.
 */
export interface CorrectionEvidence {
    game_id: string;
    node_id: string;
    source_name: string | null;
    white: string | null;
    black: string | null;
    ply: number | null;
    san: string | null;
    uci: string | null;
    color: string | null;
    phase: string | null;
    fen_before: string | null;
    fen_after: string | null;
    eval_before: { score: number | null; mate_in: number | null } | null;
    eval_after: { score: number | null; mate_in: number | null } | null;
    best_move: string | null;
    best_san: string | null;
    pv_san: string[] | null;
    cpl: number | null;
    quality: { label: string; cpl?: number | null } | null;
    depth: number | null;
    intent: string;
    diagnosis_source: string;
    at: number;
}

export interface PracticeAttempt {
    id: string;
    fen: string;
    played_uci: string | null;
    expected: string[];
    passed: boolean;
    hints_used: number;
    response_ms: number | null;
    created_at: number;
}

export interface PracticeSummary {
    attempted: number;
    passed: number;
    /** Null when nothing has been attempted - not 0, which means something else. */
    rate: number | null;
    hints_used: number;
    last_passed: boolean | null;
}

export interface Correction {
    id: string;
    theme: string;
    theme_label: string;
    player_intent: string;
    missed_factor: string;
    diagnosis: string;
    correction_rule: string;
    confidence: number;
    uncertainty: string | null;
    status: string;
    created_at: number;
    last_seen_at: number;
    occurrence_count: number;
    /** True only after the backend has committed this card to the account DB. */
    saved_to_account: boolean;
    practice_available: boolean;
    practice_unavailable_reason: string | null;
    practice_summary: PracticeSummary;
    evidence?: CorrectionEvidence[];
    attempts?: PracticeAttempt[];
}

export interface DiagnoseResult {
    correction: Correction;
    /** True only because two diagnoses carried the same controlled token. */
    recurred: boolean;
    /** 'coach' when the model answered and was trusted; 'engine' when it was not. */
    diagnosis_source: 'coach' | 'engine';
    practice_available: boolean;
    practice_unavailable_reason: string | null;
    best_move: string | null;
    best_san: string | null;
    node_id: string;
}

/** The question, with no answer attached. */
export interface RetestPosition {
    fen: string;
    prompt: string;
    from_your_game?: boolean;
}

export type PracticeStart =
    | {
          available: true;
          theme: string;
          position: RetestPosition;
          attempt_index: number;
          check: string;
      }
    | { available: false; reason: string; reason_code?: string; theme: string };

export interface PracticeResult {
    passed: boolean;
    /** Revealed only in the response to an attempt. */
    best_san: string;
    best_uci: string;
    played_san: string;
    correction: Correction;
}
