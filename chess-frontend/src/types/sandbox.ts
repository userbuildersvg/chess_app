// Types for Sandbox Learner Mode. These mirror the payloads in
// sandbox_api.py exactly - where a name here differs from the wire format,
// it is a bug, not a convenience. Two shapes in particular have already
// cost time by being guessed rather than read:
//
//   - the session id field is `session_id`, not `id`
//   - the narration *poll* endpoint returns `status`, while a node inside
//     the tree carries `narration_status`
//
// Both spellings are correct in their own place; keep them distinct.

/** How a node's move got onto the board. */
export type SandboxSource = 'setup' | 'ai' | 'human';

/** Narration lifecycle, straight from sandbox_state. */
export type NarrationStatus = 'none' | 'pending' | 'ready' | 'failed';

/**
 * One position in the move tree. Every node carries its own FEN, which is
 * what makes rewinding an O(1) board build rather than a replay - and what
 * lets a narration job describe itself without holding the whole tree.
 */
export interface SandboxNode {
    id: string;
    parent_id: string | null;
    children: string[];
    move: string | null;
    san: string | null;
    fen: string;
    turn: 'white' | 'black';
    mover: 'white' | 'black' | null;
    source: SandboxSource;
    /** The AI player's own justification, in the player's voice. */
    explanation: string | null;
    /** The coach's teaching narration - a different voice. Don't merge them. */
    narration: string | null;
    narration_status: NarrationStatus;
    evaluation: number | null;
}

export interface SandboxTree {
    root_id: string;
    current_id: string;
    nodes: Record<string, SandboxNode>;
    /** Node ids from root to current, in order. */
    line: string[];
}

/** Set when a session was opened from a natural-language scenario prompt. */
export interface SandboxScenario {
    title: string;
    description: string;
    /** Honest notes - including "couldn't make this winnable" admissions. */
    notes: string;
    side_to_move: string;
    prompt: string;
}

/** The standard response body for nearly every sandbox endpoint. */
export interface SandboxState {
    /**
     * What the position was built for, when it was built by /scenario.
     * Carried on every read so a resumed session still knows.
     */
    scenario_description?: string | null;
    session_id: string;
    title: string;
    difficulty: number;
    narration_enabled: boolean;
    fen: string;
    turn: 'white' | 'black';
    current_id: string;
    legal_moves: string[];
    line_san: string[];
    created_at: number;
    last_active: number;
    tree: SandboxTree;
    node: SandboxNode;
    /** Only on /ai-move. */
    selection_source?: string;
    played?: SandboxNode;
    /** Only on /scenario. */
    scenario?: SandboxScenario;
}

/** Stockfish's ranking of the current position. Moves are UCI, not SAN. */
export interface RankedMove {
    move: string;
    score: number;
    mate_in: number | null;
}

/** A move already played from this node - the tree's own memory. */
export interface ExploredMove {
    move: string;
    san: string;
    node_id: string;
    source: SandboxSource;
    explanation: string | null;
}

export interface SandboxAlternatives {
    session_id: string;
    node_id: string;
    fen: string;
    turn: 'white' | 'black';
    ranked: RankedMove[];
    explored: ExploredMove[];
}

/** Response of the single-node narration poll. Note: `status`, not `narration_status`. */
export interface NarrationPoll {
    session_id: string;
    node_id: string;
    status: NarrationStatus;
    narration: string | null;
}

/**
 * One turn of the Learner Mode coach chat.
 *
 * `role` mirrors the Gemini wire format the backend stores - 'model', not
 * 'ai', so the transcript can be replayed to Gemini without translation.
 */
/**
 * One turn as the SERVER stores it. Mirrors `session.chat_history`, which is
 * also what gets replayed to Gemini, so this stays exactly two roles.
 */
export interface SandboxChatTurn {
    role: 'user' | 'model';
    text: string;
}

/**
 * One entry in the transcript as the UI shows it.
 *
 * A superset of SandboxChatTurn, because Learner Mode's composer does two
 * jobs and the conversation has to be able to say things the server has no
 * concept of. `confirm` is the coach asking whether to replace the line
 * before it does so - it carries the prompt it would build, and is answered
 * in place. `divider` is the rule marking where a new position began, which
 * is what lets the transcript survive a rebuild instead of being cleared.
 *
 * Neither is ever sent anywhere: the server's transcript is rebuilt from its
 * own history on every reply, so these live only in this component's state.
 */
export type SandboxTranscriptEntry =
    | { kind: 'turn'; role: 'user' | 'model'; text: string }
    | { kind: 'confirm'; text: string; prompt: string; resolved: 'built' | 'declined' | null }
    | { kind: 'divider'; text: string };

export interface SandboxChatHistory {
    session_id: string;
    history: SandboxChatTurn[];
}

export interface SandboxChatReply {
    session_id: string;
    /** The node the question was asked about. Absent on a plain transcript read. */
    node_id?: string;
    reply?: string;
    history: SandboxChatTurn[];
}
