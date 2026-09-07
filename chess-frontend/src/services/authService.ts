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

import { apiJson } from './http';

export interface AuthConfig {
    accounts_enabled: boolean;
    guest_mode: boolean;
    /** Whether a Google button can work here. False when the deployment has
     *  no Google client configured, in which case the button is not drawn at
     *  all rather than drawn and answering 503. */
    google: boolean;
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

const request = <T,>(path: string, init?: RequestInit) =>
    apiJson<T>(`/api/auth${path}`, init);

export const authService = {
    /** Whether accounts are on. Always answers, even when they are not. */
    config: () => request<AuthConfig>('/config'),

    /** Who the caller is. Answers for guests too, so the header has one call. */
    me: () => request<WhoAmI>('/me'),

    /** `claimed_games` says how many games played on this browser as a guest
     *  have just become this account's - the number the signup screen shows
     *  back, so the handover is visible rather than assumed. */
    signup: (username: string, password: string, email?: string) =>
        request<{ username: string; signed_in: boolean; claimed_games: number }>('/signup', {
            method: 'POST',
            body: JSON.stringify({ username, password, email: email || null }),
        }),

    /** `username` accepts a username OR an email address. */
    login: (username: string, password: string) =>
        request<{ username: string; signed_in: boolean; claimed_games: number }>('/login', {
            method: 'POST',
            body: JSON.stringify({ username, password }),
        }),

    logout: () => request<{ signed_in: boolean }>('/logout', { method: 'POST' }),

    /**
     * Ask for a reset link.
     *
     * Resolves with the SAME message whether or not the address has an
     * account - the server is deliberate about that, and the UI must not
     * unpick it by treating one case as an error. Only a transport failure
     * or a rate limit rejects.
     */
    forgotPassword: (email: string) =>
        request<{ message: string }>('/forgot-password', {
            method: 'POST',
            body: JSON.stringify({ email }),
        }),

    /** Redeem a reset link. Does not sign in - the token came by email. */
    resetPassword: (token: string, new_password: string) =>
        request<{ signed_in: boolean }>('/reset-password', {
            method: 'POST',
            body: JSON.stringify({ token, new_password }),
        }),
};
