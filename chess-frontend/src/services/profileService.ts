import { apiFetch } from './http';

/**
 * The improvement profile: a library of imported games, and what they show.
 *
 * Separate from `postmortemService` on purpose. Review analyses ONE game
 * immediately and holds it in memory on the server; this stores many games on
 * the account and analyses them in the background. They share a PGN parser and
 * nothing else, and merging the two clients would mean one module with two
 * lifetimes in it.
 */

export interface ImportedGame {
    id: number;
    white: string | null;
    black: string | null;
    result: string | null;
    played_on: string | null;
    event: string | null;
    player_color: 'white' | 'black';
    ply_count: number;
    source_name: string | null;
    created_at: number;
    state: 'pending' | 'analysing' | 'done' | 'failed';
    error: string | null;
    findings: number;
}

export interface Progress {
    total: number;
    pending: number;
    analysing: number;
    done: number;
    failed: number;
    analysed: number;
    remaining: number;
}

export interface Representative {
    game_id: number;
    ply: number;
    move_san: string;
    best_san: string | null;
    cpl: number | null;
    phase: string;
    severity: string;
    fen_before: string;
}

export interface Finding {
    theme: string;
    label: string;
    /** The sentence said about the person - "You frequently...". */
    claim: string;
    description: string | null;
    check: string | null;
    evidence_count: number;
    games_count: number;
    confidence: 'low' | 'medium' | 'high';
    trend: 'improving' | 'stable' | 'worsening';
    first_seen_game: number;
    last_seen_game: number;
    representative: Representative[];
}

export interface Profile {
    ready: boolean;
    analysed_games: number;
    games_needed: number;
    minimum_games: number;
    findings: Finding[];
    progress: Progress;
}

export interface ImportNote { name: string; reason: string }

export interface ImportResult {
    added: number;
    game_ids: number[];
    /** Already in the library, by fingerprint. Not an error - re-uploading a
     *  season is a normal thing to do, and these simply do not count twice. */
    duplicates: ImportNote[];
    /** Could not be read, or had no moves in them. */
    skipped: ImportNote[];
    /** How many games in the request were past the per-request limit and were
     *  NOT looked at. Sending them again works. Never silently dropped. */
    ignored: number;
    limit: number;
    /** The server's own sentence about what happened to every game in the
     *  request. Rendered as-is: assembling it here from four numbers would
     *  eventually describe a case nobody thought about. */
    message?: string;
    progress: Progress;
}

/** Thrown when the caller has no account. The UI turns this into a prompt to
 *  sign up rather than an error, because it is a precondition and not a fault. */
export class NeedsAccount extends Error {}

async function json<T>(res: Response): Promise<T> {
    if (res.status === 401) {
        const body = await res.json().catch(() => ({}));
        throw new NeedsAccount(body.detail ?? 'Create an account to build a profile.');
    }
    const body = await res.json().catch(() => ({}));
    if (!res.ok) {
        throw new Error(body.detail ?? 'Something went wrong.');
    }
    return body as T;
}

export const profileService = {
    /** Add PGN text, which may hold many games. */
    importPgn: (pgn: string, sourceName = 'import.pgn', playerColor: 'auto' | 'white' | 'black' = 'auto') =>
        apiFetch('/api/profile/games', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ pgn, source_name: sourceName, player_color: playerColor }),
        }).then(r => json<ImportResult>(r)),

    games: () =>
        apiFetch('/api/profile/games').then(r => json<{ games: ImportedGame[]; progress: Progress }>(r)),

    remove: (id: number) =>
        apiFetch(`/api/profile/games/${id}`, { method: 'DELETE' })
            .then(r => json<{ removed: number; progress: Progress }>(r)),

    /** Cheap enough to poll while a scan runs. */
    progress: () => apiFetch('/api/profile/progress').then(r => json<Progress>(r)),

    profile: () => apiFetch('/api/profile').then(r => json<Profile>(r)),
};
