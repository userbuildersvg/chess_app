// Client for /api/sandbox/*. Thin on purpose: the interesting logic (move
// tree, validation, narration scheduling) all lives server-side in
// sandbox_state.py and sandbox_api.py, and duplicating any of it here
// would give us two versions of the truth to keep in sync.

import type {
    SandboxState,
    SandboxAlternatives,
    NarrationPoll,
    SandboxChatReply,
    SandboxChatHistory,
} from '../types/sandbox';
import { apiFetch } from './http';
import { coachBehaviorPayload } from '../coachBehavior';

const BASE = '/api/sandbox';

/**
 * The starting position of a normal game.
 *
 * Needed because `/reset` with no `start_fen` deliberately means "this
 * session's own root" - so a session opened on a generated rook endgame can
 * restart as that endgame. There is no value for "the standard opening"
 * short of naming it, and naming it here keeps the one literal FEN in the
 * frontend next to the call that uses it.
 */
export const STANDARD_FEN =
    'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1';

/**
 * The backend answers a bad request with `{"detail": "..."}` and a 4xx, and
 * those details are written to be read by a person - "I don't know the
 * Zugzwang Gambit" rather than a stack trace. Surface them verbatim instead
 * of replacing them with a generic failure message.
 */
async function request<T>(path: string, init?: RequestInit): Promise<T> {
    const response = await apiFetch(`${BASE}${path}`, {
        headers: { 'Content-Type': 'application/json' },
        ...init,
    });
    if (!response.ok) {
        let detail = `${response.status} ${response.statusText}`;
        try {
            const body = await response.json();
            if (body?.detail) {
                detail = body.detail;
            }
        } catch {
            // Non-JSON error body - keep the status line we already have.
        }
        throw new Error(detail);
    }
    return response.json() as Promise<T>;
}

export const sandboxService = {
    /** Natural language in, a validated position and an open session out. */
    createScenario(prompt: string, profile?: string): Promise<SandboxState> {
        return request<SandboxState>('/scenario', {
            method: 'POST',
            body: JSON.stringify({ prompt, profile, narration_enabled: true }),
        });
    },

    /** A session on the standard position, or an explicit FEN. */
    createSession(profile = 'club', startFen?: string): Promise<SandboxState> {
        return request<SandboxState>('/session', {
            method: 'POST',
            body: JSON.stringify({
                profile,
                start_fen: startFen,
                narration_enabled: true,
                title: 'Sandbox',
            }),
        });
    },

    getSession(id: string): Promise<SandboxState> {
        return request<SandboxState>(`/session/${id}`);
    },

    /**
     * The transcript so far.
     *
     * Needed on resume: the session survives a page reload but the component's
     * copy of the conversation does not, and a board that comes back without
     * the discussion about it is only half the thing you left.
     */
    chatHistory(id: string): Promise<SandboxChatHistory> {
        return request<SandboxChatHistory>(`/session/${id}/chat`);
    },

    /**
     * Ask the coach about the position on the board.
     *
     * Deliberately NOT the real game's `/api/chat`. That endpoint reads the
     * module-level game and appends to a module-level transcript, so routing
     * Learner Mode questions through it would mix the two conversations and
     * hand the coach the wrong position. This transcript lives on the sandbox
     * session and dies with it.
     */
    chat(id: string, message: string): Promise<SandboxChatReply> {
        return request<SandboxChatReply>(`/session/${id}/chat`, {
            method: 'POST',
            body: JSON.stringify({ message, ...coachBehaviorPayload() }),
        });
    },


    /**
     * Empty the coach's transcript for this session, leaving the board and
     * the tree alone. Server-side, because the server's copy is the one the
     * coach is replayed - a clear that only emptied the screen would leave
     * the coach remembering what the reader had just watched disappear.
     */
    clearChat(id: string): Promise<SandboxChatHistory> {
        return request<SandboxChatHistory>(`/session/${id}/chat`, { method: 'DELETE' });
    },

    deleteSession(id: string): Promise<unknown> {
        return request(`/session/${id}`, { method: 'DELETE' });
    },

    /** One AI half-move. Returns as soon as the move exists - narration follows. */
    aiMove(id: string): Promise<SandboxState> {
        return request<SandboxState>(`/session/${id}/ai-move`, { method: 'POST' });
    },

    /**
     * The student plays a move. `narrate` is off by default server-side so
     * that exploring doesn't double the load on the shared API key; we pass
     * it explicitly for "tell me what you think of my move".
     */
    playMove(id: string, move: string, narrate = false): Promise<SandboxState> {
        return request<SandboxState>(`/session/${id}/move`, {
            method: 'POST',
            body: JSON.stringify({ move, narrate }),
        });
    },

    goto(id: string, nodeId: string): Promise<SandboxState> {
        return request<SandboxState>(`/session/${id}/goto`, {
            method: 'POST',
            body: JSON.stringify({ node_id: nodeId }),
        });
    },

    back(id: string): Promise<SandboxState> {
        return request<SandboxState>(`/session/${id}/back`, { method: 'POST' });
    },

    forward(id: string): Promise<SandboxState> {
        return request<SandboxState>(`/session/${id}/forward`, { method: 'POST' });
    },

    /**
     * Restart the line, optionally at a new strength.
     *
     * Every field the backend's ResetRequest leaves out is kept as-is, so
     * omitting `profile` really does mean "same profile" and omitting
     * `start_fen` means "this session's own starting position" - not the
     * standard opening. Don't helpfully fill either one in here.
     */
    reset(id: string, profile?: string): Promise<SandboxState> {
        return request<SandboxState>(`/session/${id}/reset`, {
            method: 'POST',
            body: JSON.stringify(profile === undefined ? {} : { profile }),
        });
    },

    /**
     * Put every piece back on its starting square.
     *
     * Distinct from `reset`, which returns to whatever position THIS session
     * began at - for a generated endgame that is the endgame, which is the
     * right behaviour for "restart the line" and the wrong one for "give me a
     * normal board". This one says the position explicitly, which is the only
     * way to ask for the standard opening through an endpoint whose absent
     * `start_fen` means something else.
     *
     * The scenario is abandoned by definition: the session no longer starts
     * where the scenario put it.
     */
    resetToStandard(id: string, profile?: string): Promise<SandboxState> {
        return request<SandboxState>(`/session/${id}/reset`, {
            method: 'POST',
            body: JSON.stringify({
                start_fen: STANDARD_FEN,
                // Renamed as well as repositioned. A session opened by
                // /scenario carries that scenario's title, and leaving it in
                // place captioned a plain starting position as "Hard Rook
                // Endgame" - the heading describing a board that had just
                // been replaced.
                title: 'Sandbox',
                ...(profile === undefined ? {} : { profile }),
            }),
        });
    },

    /**
     * Was that message a question about the board, or a request for a new one?
     *
     * Learner Mode has one composer doing both jobs, and only a model can
     * reliably tell "give me something easier" from "why was that easier for
     * white?". Never throws for a classification failure - the endpoint
     * answers 200 with `ask` when Gemini is unreachable, because guessing
     * "ask" costs an odd reply and guessing "build" offers to destroy the
     * line being studied.
     */
    classify(message: string): Promise<{ intent: 'ask' | 'build'; classified: boolean }> {
        return request<{ intent: 'ask' | 'build'; classified: boolean }>('/classify', {
            method: 'POST',
            body: JSON.stringify({ message }),
        });
    },

    /**
     * The position's evaluation, from White's absolute point of view.
     *
     * A full-depth search sharing one engine lock with move selection, which
     * is why it is its own call rather than a field on every state response -
     * it is fetched only while the eval bar is actually showing.
     */
    evaluate(id: string): Promise<{ session_id: string; node_id: string; score: number | null; mate_in: number | null }> {
        return request(`/session/${id}/eval`);
    },

    /** Stockfish's ranking plus what's already been tried from here. */
    alternatives(id: string, topN = 5): Promise<SandboxAlternatives> {
        return request<SandboxAlternatives>(`/session/${id}/alternatives?top_n=${topN}`);
    },

    /**
     * Poll one node's narration. Addressed by node id, never by ply index -
     * ids are minted once and never reused, so a late job either finds its
     * node or finds nothing. That is what makes parallel narration safe.
     */
    narration(id: string, nodeId: string): Promise<NarrationPoll> {
        return request<NarrationPoll>(`/session/${id}/narration/${nodeId}`);
    },
};
