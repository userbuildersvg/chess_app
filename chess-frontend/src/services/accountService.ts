import { apiJson } from './http';

/**
 * The account you are signed in as, as opposed to signing in.
 *
 * Separate from `authService` because the two answer different questions and
 * live behind different prefixes: `/api/auth` is how you get a session,
 * `/api/account` is what that session lets you read and change. Neither call
 * here takes a user id - the server reads it from the session cookie, and a
 * client-supplied id would be a client-supplied answer to "whose account is
 * this".
 */

export interface AccountProfile {
    username: string;
    email: string | null;
    created_at: number | null;
    /** e.g. ["password"], ["google"], or both. */
    auth_methods: string[];
}

/** Preferences that follow an account between devices. */
export interface Prefs {
    pieceTheme: string | null;
    showCoordinates: boolean;
    showEngineNumbers: boolean;
    showMoveQuality: boolean;
    activeSection: string;
    guidedPlay: boolean;
    coachBluntness: number;
    coachCreativity: number;
    coachStylePreset: string;
}

const request = <T,>(path: string, init?: RequestInit) =>
    apiJson<T>(`/api/account${path}`, init);

export const accountService = {
    profile: () => request<AccountProfile>(''),

    prefs: async () => (await request<{ prefs: Prefs }>('/settings')).prefs,

    /** Sends only what changed; the server merges. Sending the whole object
     *  would race two tabs against each other. */
    savePrefs: async (prefs: Partial<Prefs>) =>
        (await request<{ prefs: Prefs }>('/settings', {
            method: 'PUT',
            body: JSON.stringify({ prefs }),
        })).prefs,

    changePassword: (current_password: string, new_password: string) =>
        request<{ signed_in: boolean }>('/password', {
            method: 'POST',
            body: JSON.stringify({ current_password, new_password }),
        }),

    deleteAccount: (confirm_username: string) =>
        request<{ signed_in: boolean }>('', {
            method: 'DELETE',
            body: JSON.stringify({ confirm_username }),
        }),
};

/**
 * Where a Google sign-in starts.
 *
 * A full-page navigation, not fetch: the browser has to actually travel to
 * Google and come back, and an XHR cannot do that. The server sets the state
 * cookie on the way out and checks it on the way back.
 */
export const GOOGLE_START_URL = '/api/auth/google/start';
