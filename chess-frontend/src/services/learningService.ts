// Client for /api/learning-loop/*. Thin, exactly like postmortemService, and
// for the same reason: the taxonomy, the diagnosis validation, the re-test
// answer and the grading all live server-side, and a second opinion about any
// of them here would give us two versions of the truth to keep in sync.
//
// What this file does NOT do: it never decides whether a move is legal, never
// holds the answer to a re-test, and never classifies a theme. It posts and it
// reads.

import type {
    Correction,
    DiagnoseResult,
    LearningReference,
    PracticeResult,
    PracticeStart,
} from '../types/learning';
import { apiFetch } from './http';

const BASE = '/api/learning-loop';

/**
 * Backend `detail` strings for this router are written to be read by a player
 * ("Tell the coach what you were trying to do first"), so they are surfaced
 * verbatim - replacing them with a generic message would throw away the only
 * part of the error that helps.
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
                detail = typeof body.detail === 'string' ? body.detail : detail;
            }
        } catch {
            // A non-JSON error body is not worth a second failure.
        }
        throw new Error(detail);
    }
    return response.json() as Promise<T>;
}

const post = <T>(path: string, body: unknown) =>
    request<T>(path, { method: 'POST', body: JSON.stringify(body) });

export interface LearningEventProperties {
    theme?: string;
    game_id?: string;
    correction_id?: string;
    node_id?: string;
    ply_index?: number;
    source_mode?: 'Post-Mortem' | 'Review' | 'Learn' | 'Play';
    duration_ms?: number;
    operation?: string;
    outcome?: string;
    error_code?: string;
    error_category?: string;
    completed?: boolean;
    hint_used?: boolean;
}

export const learningService = {
    reference: () => request<LearningReference>('/themes'),

    corrections: () => request<{ corrections: Correction[]; count: number }>('/corrections'),

    diagnose: (gameId: string, nodeId: string, intent: string, preset: string | null) =>
        post<DiagnoseResult>('/diagnose', {
            game_id: gameId,
            node_id: nodeId,
            intent,
            intent_preset: preset,
        }),

    setStatus: (correctionId: string, status: string) =>
        post<{ correction: Correction }>(`/correction/${correctionId}/status`, { status }),

    branchTried: (correctionId: string, uci: string) =>
        post<{ matched_best: boolean }>('/branch-tried', { correction_id: correctionId, uci }),

    startPractice: (correctionId: string) =>
        post<PracticeStart>('/practice/start', { correction_id: correctionId }),

    attempt: (correctionId: string, uci: string, hintsUsed: number, responseMs: number | null) =>
        post<PracticeResult>('/practice/attempt', {
            correction_id: correctionId,
            uci,
            hints_used: hintsUsed,
            response_ms: responseMs,
        }),

    hint: (correctionId: string) =>
        post<{ hint: string }>('/practice/hint', { correction_id: correctionId }),

    /**
     * The UI's half of the funnel - the steps the server cannot see.
     *
     * Deliberately fire-and-forget and deliberately silent on failure:
     * instrumentation that can interrupt the thing it is instrumenting is
     * worse than no instrumentation, and this is called from the middle of a
     * flow the player is walking through.
     */
    event: (name: string, properties: LearningEventProperties = {}) => {
        void request('/events', {
            method: 'POST',
            body: JSON.stringify({ name, source_mode: 'Post-Mortem', ...properties }),
            keepalive: true,
        }).catch(() => {});
    },
};
