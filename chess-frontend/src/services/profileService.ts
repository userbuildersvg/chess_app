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

export type ImportSource = 'chesscom' | 'lichess' | 'manual';

export const SOURCE_LABELS: Record<ImportSource, string> = {
    chesscom: 'Chess.com',
    lichess: 'Lichess',
    manual: 'Manual PGN',
};

export interface ImportedGame {
    id: number;
    /** Which site it came from. Games from before source separation are 'manual'. */
    source: ImportSource;
    source_username: string | null;
    external_id: string | null;
    time_control: string | null;
    rated: boolean | null;
    variant: string | null;
    opening: string | null;
    /** When it was last opened in Review, or null. */
    reviewed_at: number | null;
    analysed_at: number | null;
    /** The worker's scan finished (state === 'done'). */
    analyzed: boolean;
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
    finding_id: number;
    game_id: number;
    ply: number;
    move_san: string;
    best_san: string | null;
    cpl: number | null;
    phase: string;
    severity: string;
    /** "Chess.com · you as White vs magnus · Blitz 5+0 · 1-0 · Sep 10, 2026 · game #69" */
    game_label: string | null;
    can_review_game: boolean;
}

export interface GameRef {
    game_id: number;
    game_label: string | null;
    source: ImportSource;
    can_review_game: boolean;
}

export interface PracticeStart {
    ok: boolean;
    available: boolean;
    reason?: string;
    practice_session_id?: string;
    theme?: string;
    instructions?: string;
    source_evidence?: { game_label: string | null; move_label: string; played_san: string };
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
    first_seen: GameRef | null;
    last_seen: GameRef | null;
    representative: Representative[];
    practice_available: boolean;
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

/** One public game as the site reported it - searched, not yet stored. */
export interface ExternalGame {
    external_id: string | null;
    source: ImportSource;
    white: string | null;
    black: string | null;
    result: string | null;
    date: string | null;
    played_at: number | null;
    time_control: string | null;
    rated: boolean | null;
    variant: string;
    /** Standard chess from the initial position. Anything else is shown but cannot be imported. */
    supported: boolean;
    opening: string | null;
    eco: string | null;
    white_elo: string | null;
    black_elo: string | null;
    move_count: number;
    pgn: string;
}

export interface ExternalSearchResult {
    ok: true;
    source: ImportSource;
    username: string;
    games: ExternalGame[];
    note: string;
}

export interface ExternalImportNote { external_id: string | null; name: string; reason?: string }

export interface ExternalImportResult {
    ok: true;
    source: ImportSource;
    username: string;
    imported: { id: number; external_id: string | null; name: string }[];
    duplicates: ExternalImportNote[];
    skipped: ExternalImportNote[];
    imported_count: number;
    duplicate_count: number;
    skipped_count: number;
    /** The server's sentence about what happened to every game. */
    message: string;
    progress: Progress;
}

export interface SourceEvidence {
    source: ImportSource;
    games: number;
    analysed: number;
    reviewed: number;
    findings: number;
    correction_evidence: number;
}

export interface EvidenceTheme {
    theme: string;
    label: string;
    count: number;
    games_count: number;
    by_source: Partial<Record<ImportSource, number>>;
    /** Seen in enough distinct games to call it a pattern. False from one game. */
    recurring: boolean;
}

export interface Evidence {
    sources: SourceEvidence[];
    themes: EvidenceTheme[];
    evidence_count: number;
    min_games_per_theme: number;
}

/** Thrown when the caller has no account. The UI turns this into a prompt to
 *  sign up rather than an error, because it is a precondition and not a fault. */
export class NeedsAccount extends Error {}

/** A refusal with a stable category from the server (`error`), so the UI can
 *  say "Username not found" without matching on sentence text. */
export class ExternalImportError extends Error {
    category: string;
    constructor(category: string, message: string) {
        super(message);
        this.category = category;
    }
}

async function json<T>(res: Response): Promise<T> {
    const body = await res.json().catch(() => ({}));
    const message = typeof body.message === 'string' ? body.message
        : typeof body.detail === 'string' ? body.detail : undefined;
    if (res.status === 401) {
        throw new NeedsAccount(message ?? 'Create an account to build a profile.');
    }
    if (!res.ok) {
        if (typeof body.error === 'string') {
            throw new ExternalImportError(body.error, message ?? 'Something went wrong.');
        }
        throw new Error(message ?? 'Something went wrong.');
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

    /** One game with its PGN - what "View PGN" reads. */
    game: (id: number) =>
        apiFetch(`/api/profile/games/${id}`).then(r => json<{ game: ImportedGame & { pgn: string } }>(r)),

    /** Open an imported game in Review. Answers the Post-Mortem review state;
     *  the caller remembers its id and switches to Review. */
    review: (id: number) =>
        apiFetch(`/api/profile/games/${id}/review`, { method: 'POST' })
            .then(r => json<{ game_id: string; player_color: 'white' | 'black' | null }>(r)),

    /** Recent public games for a username. Nothing is stored by this call. */
    externalSearch: (source: ImportSource, username: string, maxGames = 20) =>
        apiFetch('/api/profile/external/search', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ source, username, max_games: maxGames }),
        }).then(r => json<ExternalSearchResult>(r)),

    /** Store the chosen searched games (by the site's id) under the account. */
    externalImport: (source: ImportSource, username: string, externalIds: string[], maxGames = 20) =>
        apiFetch('/api/profile/external/import', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ source, username, external_ids: externalIds, max_games: maxGames }),
        }).then(r => json<ExternalImportResult>(r)),

    /** Open Learn on one of this theme's own positions. `available: false`
     *  is a fact about the evidence, not an error. */
    practice: (theme: string) =>
        apiFetch(`/api/profile/mistakes/${encodeURIComponent(theme)}/practice`, { method: 'POST' })
            .then(r => json<PracticeStart>(r)),

    /** Grade the first move of a profile practice session. */
    practiceAttempt: (session_id: string, uci: string) =>
        apiFetch('/api/profile/practice/attempt', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ session_id, uci }),
        }).then(r => json<{ passed: boolean; repeated_mistake: boolean; played_san: string; best_san: string | null; original_san: string | null }>(r)),

    /** Source-tagged evidence counts from the imported library. */
    evidence: () => apiFetch('/api/profile/evidence').then(r => json<Evidence>(r)),
};
