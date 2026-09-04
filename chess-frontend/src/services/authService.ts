/**
 * Client for /api/auth/*.
 *
 * All five routes exist and are called for real. Four of them currently
 * answer 503, because accounts are built and switched off (`auth_api.py`) -
 * and this file deliberately does not know that. It asks the server and
 * reports what the server said.
 *
 * That is the difference between a feature that is disabled and a feature
 * that is faked. When ACCOUNTS_ENABLED flips to true on the backend, nothing
 * here changes: `config()` starts reporting `accounts_enabled: true`, the
 * forms stop being replaced by the unavailable notice, and signup/login start
 * succeeding. There is no client-side flag to remember to flip.
 *
 * The session token is never handled here. It lives in an HttpOnly cookie the
 * server sets and clears, which is the whole reason it cannot be stolen by
 * script - so there is nothing for this file to store, and `apiFetch` carries
 * it automatically.
 */

import { apiFetch } from './http';

export interface AuthConfig {
    accounts_enabled: boolean;
    guest_mode: boolean;
    /** What to tell the user when accounts are off. Written by the server so
     *  the message on screen cannot drift from what the server actually does. */
    unavailable_message: string | null;
}

export interface WhoAmI {
    signed_in: boolean;
    guest: boolean;
    username: string | null;
    accounts_enabled: boolean;
}

/**
 * The backend answers a refusal with `{"detail": "..."}`, written to be read
 * by a person. Surface it verbatim rather than replacing it with a generic
 * message - "Accounts aren't available yet" is exactly what should appear.
 */
async function request<T>(path: string, init?: RequestInit): Promise<T> {
    const response = await apiFetch(`/api/auth${path}`, {
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

export const authService = {
    /** Whether accounts are on. Always answers, even when they are not. */
    config: () => request<AuthConfig>('/config'),

    /** Who the caller is. Answers for guests too, so the header has one call. */
    me: () => request<WhoAmI>('/me'),

    signup: (username: string, password: string) =>
        request<{ username: string; signed_in: boolean }>('/signup', {
            method: 'POST',
            body: JSON.stringify({ username, password }),
        }),

    login: (username: string, password: string) =>
        request<{ username: string; signed_in: boolean }>('/login', {
            method: 'POST',
            body: JSON.stringify({ username, password }),
        }),

    logout: () => request<{ signed_in: boolean }>('/logout', { method: 'POST' }),
};
