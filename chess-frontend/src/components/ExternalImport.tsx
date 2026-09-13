import { useState } from 'react';
import { Link } from 'react-router-dom';
import {
    ExternalImportError, NeedsAccount, SOURCE_LABELS, profileService,
    type ExternalGame, type ExternalImportResult, type ImportSource,
} from '../services/profileService';
import './ExternalImport.css';

/**
 * Import recent public games from Chess.com or Lichess by username.
 *
 * ONE FORM, TWO PLACES
 * --------------------
 * The onboarding prompt after sign-up and the imported-games section in
 * Account settings both render this. One component, so the copy about what
 * is fetched - public games only, no password - cannot drift between them.
 *
 * TWO STEPS, ON PURPOSE
 * ---------------------
 * "Find recent games" shows what the site has before anything is stored, and
 * "Import" stores the ticked ones. The PGN never travels back up from the
 * browser: the import sends the site's game ids and the server fetches again
 * (from its one-minute cache), so what is stored under "Chess.com" really
 * came from Chess.com.
 *
 * ACCOUNT-GATED BY THE SERVER
 * ---------------------------
 * A guest reaching this gets a 401 and sees an invitation to create an
 * account. The gate is in profile_api.py, not here.
 */

export const PUBLIC_GAMES_NOTE =
    'We only fetch public games for the username you enter. No Chess.com or Lichess password is required.';

export const GUEST_IMPORT_NOTE =
    'Create a free account to import recent games from Chess.com or Lichess and build your improvement profile.';

const SOURCES: ImportSource[] = ['chesscom', 'lichess'];

const ERROR_LABELS: Record<string, string> = {
    username_not_found: 'Username not found.',
    no_games: 'No recent public games found.',
    provider_unavailable: 'That site is temporarily unavailable.',
    rate_limited: 'That site is asking us to slow down. Try again in a minute.',
    too_many: 'Too many games requested.',
    invalid_username: 'That does not look like a valid username.',
    invalid_source: 'Choose Chess.com or Lichess.',
    unsupported_variant: 'Unsupported variant.',
    library_full: 'Your library is full. Remove some games first.',
};

function resultFor(game: ExternalGame, username: string): string {
    const me = username.toLowerCase();
    const white = (game.white ?? '').toLowerCase() === me;
    const black = (game.black ?? '').toLowerCase() === me;
    if (game.result === '1/2-1/2') return 'draw';
    if (game.result === '1-0') return white ? 'won' : black ? 'lost' : '1-0';
    if (game.result === '0-1') return black ? 'won' : white ? 'lost' : '0-1';
    return game.result ?? '?';
}

interface Props {
    /** Called after games were stored. The prompt closes on it; settings reloads. */
    onImported?: (result: ExternalImportResult) => void;
    /** Header copy is the caller's; this is just the tool. */
    compact?: boolean;
}

export function ExternalImport({ onImported, compact = false }: Props) {
    const [source, setSource] = useState<ImportSource>('chesscom');
    const [username, setUsername] = useState('');
    const [busy, setBusy] = useState<'search' | 'import' | null>(null);
    const [games, setGames] = useState<ExternalGame[] | null>(null);
    const [searched, setSearched] = useState<{ source: ImportSource; username: string } | null>(null);
    const [selected, setSelected] = useState<Set<string>>(new Set());
    const [error, setError] = useState<string | null>(null);
    const [needsAccount, setNeedsAccount] = useState(false);
    const [notice, setNotice] = useState<string | null>(null);

    const fail = (e: unknown, fallback: string) => {
        if (e instanceof NeedsAccount) { setNeedsAccount(true); return; }
        if (e instanceof ExternalImportError) {
            setError(ERROR_LABELS[e.category] ?? e.message);
            return;
        }
        setError(e instanceof Error ? e.message : fallback);
    };

    const search = async (e: React.FormEvent) => {
        e.preventDefault();
        if (!username.trim()) return;
        setBusy('search');
        setError(null);
        setNotice(null);
        setGames(null);
        try {
            const result = await profileService.externalSearch(source, username.trim());
            setGames(result.games);
            setSearched({ source: result.source, username: result.username });
            setSelected(new Set(
                result.games.filter(g => g.supported && g.external_id).map(g => g.external_id as string),
            ));
        } catch (err) {
            fail(err, 'Could not fetch games.');
        } finally {
            setBusy(null);
        }
    };

    const importSelected = async () => {
        if (!searched || selected.size === 0) return;
        setBusy('import');
        setError(null);
        try {
            const result = await profileService.externalImport(searched.source, searched.username, Array.from(selected));
            setNotice(result.message);
            setGames(null);
            setSelected(new Set());
            onImported?.(result);
        } catch (err) {
            fail(err, 'Could not import those games.');
        } finally {
            setBusy(null);
        }
    };

    const toggle = (id: string) => {
        setSelected(prev => {
            const next = new Set(prev);
            if (next.has(id)) next.delete(id); else next.add(id);
            return next;
        });
    };

    if (needsAccount) {
        return (
            <div className="xi xi-guest" data-testid="external-import-guest">
                <p className="xi-guest-copy">{GUEST_IMPORT_NOTE}</p>
                <div className="xi-actions">
                    <Link className="acct-btn acct-btn-primary" to="/signup">Create a free account</Link>
                    <Link className="acct-btn acct-btn-quiet" to="/signin">Sign in</Link>
                </div>
            </div>
        );
    }

    return (
        <div className={`xi${compact ? ' xi-compact' : ''}`} data-testid="external-import">
            <form className="xi-form" onSubmit={search}>
                <div className="xi-sources" role="group" aria-label="Where your games are">
                    {SOURCES.map(s => (
                        <button
                            key={s}
                            type="button"
                            className="xi-source"
                            aria-pressed={source === s}
                            onClick={() => { setSource(s); setGames(null); setError(null); }}
                        >
                            {SOURCE_LABELS[s]}
                        </button>
                    ))}
                </div>

                <label className="acct-label" htmlFor="xi-username">{SOURCE_LABELS[source]} username</label>
                <div className="xi-row">
                    <input
                        id="xi-username"
                        className="acct-input xi-input"
                        type="text"
                        autoComplete="off"
                        spellCheck={false}
                        maxLength={50}
                        placeholder={source === 'chesscom' ? 'e.g. hikaru' : 'e.g. DrNykterstein'}
                        value={username}
                        onChange={e => setUsername(e.target.value)}
                    />
                    <button
                        className="acct-btn acct-btn-primary"
                        type="submit"
                        disabled={busy !== null || !username.trim()}
                    >
                        {busy === 'search' ? 'Looking…' : 'Find recent games'}
                    </button>
                </div>
                <p className="xi-note">{PUBLIC_GAMES_NOTE}</p>
            </form>

            {error && <p className="acct-error" role="alert">{error}</p>}
            {notice && <p className="xi-notice" role="status">{notice}</p>}

            {games && searched && (
                <div className="xi-results">
                    {games.length === 0 ? (
                        <p className="xi-empty">No recent public games found.</p>
                    ) : (
                        <>
                            <p className="xi-results-title">
                                {games.length} recent game{games.length === 1 ? '' : 's'} for{' '}
                                <strong>{searched.username}</strong> on {SOURCE_LABELS[searched.source]}
                            </p>
                            <ul className="xi-list">
                                {games.map((g, i) => {
                                    const id = g.external_id ?? `row-${i}`;
                                    const importable = g.supported && Boolean(g.external_id);
                                    return (
                                        <li key={id} className={`xi-game${importable ? '' : ' is-unsupported'}`}>
                                            <label className="xi-game-label">
                                                <input
                                                    type="checkbox"
                                                    disabled={!importable || busy !== null}
                                                    checked={importable && selected.has(id)}
                                                    onChange={() => toggle(id)}
                                                    aria-label={`Import ${g.white ?? '?'} vs ${g.black ?? '?'}`}
                                                />
                                                <span className="xi-game-main">
                                                    <span className="xi-game-players">
                                                        {g.white ?? '?'} vs {g.black ?? '?'}
                                                    </span>
                                                    <span className="xi-game-meta">
                                                        {resultFor(g, searched.username)}
                                                        {g.date ? ` · ${g.date}` : ''}
                                                        {g.time_control ? ` · ${g.time_control}` : ''}
                                                        {g.rated != null ? ` · ${g.rated ? 'rated' : 'casual'}` : ''}
                                                        {g.opening ? ` · ${g.opening}` : ''}
                                                        {` · ${g.move_count} plies`}
                                                        {!g.supported ? ` · unsupported variant (${g.variant})` : ''}
                                                    </span>
                                                </span>
                                            </label>
                                        </li>
                                    );
                                })}
                            </ul>
                            <div className="xi-actions">
                                <button
                                    type="button"
                                    className="acct-btn acct-btn-primary"
                                    disabled={busy !== null || selected.size === 0}
                                    onClick={() => void importSelected()}
                                >
                                    {busy === 'import'
                                        ? 'Importing…'
                                        : `Import ${selected.size} game${selected.size === 1 ? '' : 's'}`}
                                </button>
                            </div>
                        </>
                    )}
                </div>
            )}
        </div>
    );
}
