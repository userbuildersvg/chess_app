// Client for /api/postmortem/*. Thin on purpose, exactly like sandboxService:
// the PGN replay, the branch validation, the grading and the coach's evidence
// packet all live server-side, and a second opinion about any of them here
// would give us two versions of the truth to keep in sync.
//
// Note what this file does NOT do: it never parses a PGN, never decides
// whether a move is legal, and never computes an evaluation. It reads a file
// as text and posts it. That is the whole of the frontend's involvement in
// chess truth, and it is deliberate - see the spec's backend boundaries.

import type {
    AnalysisReport,
    MoveEvidence,
    PostMortemChatReply,
    PostMortemChatTurn,
    PostMortemState,
    ScanProgress,
} from '../types/postmortem';
import { apiFetch } from './http';

const BASE = '/api/postmortem';

/**
 * The backend answers a bad request with `{"detail": "..."}`, and for this
 * mode those details are written to be read by a chess player - "that file is
 * empty - there is no game in it to review" rather than a parser diagnostic.
 * Surface them verbatim; replacing them with a generic message would throw
 * away the only part of the error that helps.
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
        const error = new Error(detail) as Error & { status?: number };
        error.status = response.status;
        throw error;
    }
    return response.json() as Promise<T>;
}

export const postmortemService = {
    /**
     * Import a PGN. The text is sent as-is: the server parses it, replays it
     * move by move, and refuses anything it cannot reproduce exactly.
     */
    importPgn(pgn: string, sourceName: string): Promise<PostMortemState> {
        return request<PostMortemState>('/import', {
            method: 'POST',
            body: JSON.stringify({ pgn, source_name: sourceName }),
        });
    },

    getGame(id: string): Promise<PostMortemState> {
        return request<PostMortemState>(`/game/${id}`);
    },

    deleteGame(id: string): Promise<unknown> {
        return request(`/game/${id}`, { method: 'DELETE' });
    },

    goto(id: string, nodeId: string): Promise<PostMortemState> {
        return request<PostMortemState>(`/game/${id}/goto`, {
            method: 'POST',
            body: JSON.stringify({ node_id: nodeId }),
        });
    },

    back(id: string): Promise<PostMortemState> {
        return request<PostMortemState>(`/game/${id}/back`, { method: 'POST' });
    },

    /** One ply forward *through the game* - never into a branch. */
    forward(id: string): Promise<PostMortemState> {
        return request<PostMortemState>(`/game/${id}/forward`, { method: 'POST' });
    },

    /** Back to the real game, at the position the branch left from. */
    returnToGame(id: string): Promise<PostMortemState> {
        return request<PostMortemState>(`/game/${id}/return`, { method: 'POST' });
    },

    /**
     * Play a different move from the position on the board.
     *
     * Playing the move that was actually played is not an error and does not
     * branch - the server walks the game forward instead, so a user stepping
     * through by hand cannot create a phantom variation.
     */
    branch(id: string, move: string): Promise<PostMortemState> {
        return request<PostMortemState>(`/game/${id}/branch`, {
            method: 'POST',
            body: JSON.stringify({ move }),
        });
    },

    /**
     * The engine's reply inside a what-if, at full strength.
     *
     * Refused with 409 on the real game, where what happened next is recorded
     * rather than decided.
     */
    aiMove(id: string): Promise<PostMortemState> {
        return request<PostMortemState>(`/game/${id}/ai-move`, { method: 'POST' });
    },

    /** Start the whole-game scan. Idempotent - safe to call again. */
    startScan(id: string): Promise<{ game_id: string; scan: ScanProgress }> {
        return request(`/game/${id}/analyse`, { method: 'POST' });
    },

    /** Progress and results together, so a poll never has to reconcile two calls. */
    analysis(id: string): Promise<AnalysisReport> {
        return request<AnalysisReport>(`/game/${id}/analysis`);
    },

    /** One move's evidence, computed at full depth if it is not already known. */
    moveAnalysis(id: string, nodeId: string): Promise<{ analysis: MoveEvidence }> {
        return request(`/game/${id}/analysis/${nodeId}`);
    },

    chat(id: string, message: string): Promise<PostMortemChatReply> {
        return request<PostMortemChatReply>(`/game/${id}/chat`, {
            method: 'POST',
            body: JSON.stringify({ message }),
        });
    },

    /**
     * The transcript so far.
     *
     * Needed on resume: the review survives a page reload but the component's
     * copy of the conversation does not, and a game that comes back without
     * the discussion about it is only half the thing you left.
     */
    chatHistory(id: string): Promise<{ history: PostMortemChatTurn[] }> {
        return request(`/game/${id}/chat`);
    },

    /** Empty the review's conversation without closing the review. */
    clearChat(id: string): Promise<{ history: PostMortemChatTurn[] }> {
        return request(`/game/${id}/chat`, { method: 'DELETE' });
    },
};
