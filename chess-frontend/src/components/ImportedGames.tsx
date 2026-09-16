import { useCallback, useEffect, useState } from 'react';
import { ConfirmDialog, RemoveGameBody } from './ConfirmDialog';
import { useNavigate } from 'react-router-dom';
import {
    NeedsAccount, SOURCE_LABELS, profileService,
    type Evidence, type ImportSource, type ImportedGame,
} from '../services/profileService';
import { ExternalImport } from './ExternalImport';
import './ExternalImport.css';

/**
 * Account settings: the imported games, by source, and what they have shown.
 *
 * Chess.com games, Lichess games and pasted PGNs are three lists, not one
 * pile - the whole point of source separation is that a person can see
 * which site each game came from and read the PGN back. "Review" opens the
 * game in the existing Post-Mortem (profileService.review); there is no
 * second review system.
 */

const ORDER: ImportSource[] = ['chesscom', 'lichess', 'manual'];

export const STORED_PGN_NOTE =
    'Imported PGNs are stored in your Zugzwang account so your review history and improvement profile can use them.';

export const REMOVE_NOTE =
    'Removing an imported game stops it from appearing in your imported games list and deletes the analysis evidence found in it. A correction card you already made while reviewing it keeps its own copy of that position.';

function dateOf(g: ImportedGame): string | null {
    return g.played_on ?? null;
}

function opponentOf(g: ImportedGame): string {
    const opp = g.player_color === 'white' ? g.black : g.white;
    return opp ?? '?';
}

function outcomeOf(g: ImportedGame): string {
    if (g.result === '1/2-1/2') return 'draw';
    if (g.result === '1-0') return g.player_color === 'white' ? 'won' : 'lost';
    if (g.result === '0-1') return g.player_color === 'black' ? 'won' : 'lost';
    return g.result ?? '?';
}

/** "600+5" -> "Rapid 10+5", the same classes the profile's game labels use. */
function tcLabel(tc: string): string {
    const [base, inc] = tc.split('+');
    const secs = Number(base), bonus = Number(inc ?? 0);
    if (!Number.isFinite(secs) || !Number.isFinite(bonus)) return tc;
    const est = secs + 40 * bonus;
    const kind = est < 180 ? 'Bullet' : est < 480 ? 'Blitz' : est < 1500 ? 'Rapid' : 'Classical';
    const minutes = secs % 60 === 0 ? secs / 60 : Math.round(secs / 6) / 10;
    return `${kind} ${minutes}+${bonus}`;
}

function GameRow({ game, onRemove, onReview }: {
    game: ImportedGame;
    onRemove: (id: number) => void;
    onReview: (id: number) => Promise<void>;
}) {
    const [pgn, setPgn] = useState<string | null>(null);
    const [showPgn, setShowPgn] = useState(false);
    const [copied, setCopied] = useState(false);
    const [busy, setBusy] = useState<'review' | 'pgn' | null>(null);
    const [error, setError] = useState<string | null>(null);

    const loadPgn = async (): Promise<string | null> => {
        if (pgn) return pgn;
        setBusy('pgn');
        try {
            const { game: full } = await profileService.game(game.id);
            setPgn(full.pgn);
            return full.pgn;
        } catch (e) {
            setError(e instanceof Error ? e.message : 'Could not load the PGN.');
            return null;
        } finally {
            setBusy(null);
        }
    };

    const view = async () => {
        if (showPgn) { setShowPgn(false); return; }
        const text = await loadPgn();
        if (text) setShowPgn(true);
    };

    const copy = async () => {
        const text = await loadPgn();
        if (!text) return;
        try {
            await navigator.clipboard.writeText(text);
            setCopied(true);
            window.setTimeout(() => setCopied(false), 1500);
        } catch {
            // Clipboard unavailable (insecure context, permissions): show it instead.
            setShowPgn(true);
        }
    };

    const review = async () => {
        setBusy('review');
        setError(null);
        try {
            await onReview(game.id);
        } catch (e) {
            setError(e instanceof Error ? e.message : 'The review could not be opened.');
            setBusy(null);
        }
    };

    const label = `${game.white ?? '?'} vs ${game.black ?? '?'}`;

    return (
        <li className="ig-game" data-testid="imported-game" data-source={game.source}>
            <span className="ig-game-main">
                <span className="ig-game-players">{label}</span>
                <span className="ig-game-meta">
                    {dateOf(game) ?? 'date unknown'}
                    {` · vs ${opponentOf(game)}`}
                    {` · you played ${game.player_color}`}
                    {` · ${outcomeOf(game)}`}
                    {game.time_control ? ` · ${tcLabel(game.time_control)}` : ''}
                    {game.rated != null ? ` · ${game.rated ? 'rated' : 'casual'}` : ''}
                    {game.opening ? ` · ${game.opening}` : ''}
                    {` · ${game.ply_count} plies`}
                    {game.reviewed_at ? ' · reviewed' : ''}
                </span>
                {game.state === 'failed' && game.error && (
                    <span className="ig-game-meta">{game.error}</span>
                )}
                {error && <span className="acct-error">{error}</span>}
            </span>
            <span className={`ig-state${game.analyzed ? ' is-analyzed' : ''}`}>
                {game.analyzed ? `analyzed · ${game.findings} found` : game.state === 'analysing' ? 'analysing…' : 'not analyzed'}
            </span>
            <span className="ig-actions">
                <button type="button" className="acct-btn acct-btn-primary" disabled={busy !== null} onClick={() => void review()}>
                    {busy === 'review' ? 'Opening…' : 'Review this game'}
                </button>
                {/* The rest under More: PGN is shown only when asked for, and
                    Remove never sits beside the primary action. */}
                <details className="ig-more">
                    <summary className="acct-btn acct-btn-quiet" data-testid="imported-more">More</summary>
                    <span className="ig-more-actions">
                        <button type="button" className="acct-btn" disabled={busy === 'pgn'} onClick={() => void view()}>
                            {showPgn ? 'Hide PGN' : 'View PGN'}
                        </button>
                        <button type="button" className="acct-btn" disabled={busy === 'pgn'} onClick={() => void copy()} aria-live="polite">
                            {copied ? 'Copied ✓' : 'Copy PGN'}
                        </button>
                        <button type="button" className="acct-btn acct-btn-quiet ig-remove" onClick={() => onRemove(game.id)} aria-label={`Remove ${label}`}>
                            Remove
                        </button>
                    </span>
                </details>
            </span>
            {showPgn && pgn && (
                <div className="ig-pgn-wrap" data-testid="imported-pgn">
                    <pre className="ig-pgn">{pgn}</pre>
                    <button type="button" className="acct-btn acct-btn-quiet" onClick={() => setShowPgn(false)}>Close PGN</button>
                </div>
            )}
        </li>
    );
}

export function ImportedGames() {
    const navigate = useNavigate();
    const [games, setGames] = useState<ImportedGame[]>([]);
    const [evidence, setEvidence] = useState<Evidence | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [loaded, setLoaded] = useState(false);

    const load = useCallback(async () => {
        try {
            const [lib, ev] = await Promise.all([profileService.games(), profileService.evidence()]);
            setGames(lib.games);
            setEvidence(ev);
            setError(null);
        } catch (e) {
            if (!(e instanceof NeedsAccount)) {
                setError(e instanceof Error ? e.message : 'Could not load your imported games.');
            }
        } finally {
            setLoaded(true);
        }
    }, []);

    useEffect(() => { void load(); }, [load]);

    // Poll only while a scan is outstanding, as the profile page does.
    useEffect(() => {
        if (!games.some(g => g.state === 'pending' || g.state === 'analysing')) return;
        const id = window.setInterval(() => { void load(); }, 5000);
        return () => window.clearInterval(id);
    }, [games, load]);

    const [pendingRemove, setPendingRemove] = useState<number | null>(null);
    const [removing, setRemoving] = useState(false);

    const remove = async () => {
        const id = pendingRemove;
        if (id === null) return;
        setRemoving(true);
        try {
            await profileService.remove(id);
            await load();
        } catch (e) {
            setError(e instanceof Error ? e.message : 'That game could not be removed.');
        }
        setRemoving(false);
        setPendingRemove(null);
    };

    /**
     * Into Review. The server makes the review from the stored PGN and starts
     * its scan; the browser remembers the id under the key Review resumes
     * from, sets the mode, and goes to the board. The same resume path a
     * refresh takes, so nothing here is a second way into Review.
     */
    const review = async (id: number) => {
        const state = await profileService.review(id);
        try {
            localStorage.setItem('postmortem-game', state.game_id);
            localStorage.setItem('chess-mode', 'postmortem');
        } catch {
            // localStorage unavailable: Review opens on its empty canvas.
        }
        navigate('/');
    };

    const bySource = new Map<ImportSource, ImportedGame[]>();
    for (const g of games) {
        const key = (ORDER.includes(g.source) ? g.source : 'manual') as ImportSource;
        bySource.set(key, [...(bySource.get(key) ?? []), g]);
    }

    return (
        <>
            <section className="settings-card" id="import-games" data-section="import">
                <h2 className="settings-card-title">Import games</h2>
                <p className="settings-card-sub" data-testid="import-trust">
                    Zugzwang only imports public games from Chess.com or Lichess. No chess-site
                    password is needed.
                </p>
                <p className="settings-card-sub" data-testid="import-effect">
                    Imported games can be reviewed and, once enough are analysed, contribute to your{' '}
                    <a className="auth-link" href="/profile">Improvement Profile</a>. PGN files can be
                    added there as well.
                </p>
                <ExternalImport compact onImported={() => void load()} />
            </section>

            <section className="settings-card" id="imported-games">
                <h2 className="settings-card-title">Imported games</h2>
                <p className="settings-card-sub">{STORED_PGN_NOTE}</p>
                {error && <p className="acct-error">{error}</p>}
                {loaded && games.length === 0 && (
                    <p className="ig-empty">Nothing imported yet. Use the form above, or paste a PGN on the profile page.</p>
                )}
                {ORDER.filter(s => bySource.has(s)).map(s => {
                    const list = bySource.get(s) ?? [];
                    return (
                        <div className="ig-source" key={s} data-testid={`imported-source-${s}`}>
                            <h3 className="ig-source-title">
                                {SOURCE_LABELS[s]}
                                <span className="ig-source-count">
                                    {list.length} game{list.length === 1 ? '' : 's'}
                                    {list[0]?.source_username ? ` · ${list[0].source_username}` : ''}
                                </span>
                            </h3>
                            <ul className="ig-list">
                                {list.map(g => <GameRow key={g.id} game={g} onRemove={id => setPendingRemove(id)} onReview={review} />)}
                            </ul>
                        </div>
                    );
                })}
                {games.length > 0 && <p className="settings-card-sub" style={{ marginTop: 12 }}>{REMOVE_NOTE}</p>}
            </section>

            {evidence && evidence.sources.length > 0 && (
                <section className="settings-card" id="imported-evidence">
                    <h2 className="settings-card-title">Imported-game evidence</h2>
                    <p className="settings-card-sub">
                        Counted from analysed games and from correction cards made in Review. A theme is
                        only called recurring once it appears in {evidence.min_games_per_theme} or more
                        of your games; until then this is evidence collected, not a pattern.
                    </p>
                    <div className="ig-evidence">
                        {evidence.sources.map(s => (
                            <div className="ig-evidence-source" key={s.source}>
                                <strong>{SOURCE_LABELS[s.source] ?? s.source}</strong>
                                <br />{s.analysed} analysed game{s.analysed === 1 ? '' : 's'} of {s.games}
                                <br />{s.reviewed} reviewed · {s.findings} finding{s.findings === 1 ? '' : 's'}
                            </div>
                        ))}
                    </div>
                    {evidence.themes.length === 0 ? (
                        <p className="ig-empty">No profile evidence collected yet.</p>
                    ) : (
                        <>
                            <p className="settings-card-sub" style={{ marginBottom: 6 }}>Most common themes</p>
                            <ul className="ig-themes">
                                {evidence.themes.slice(0, 8).map(t => (
                                    <li className={`ig-theme${t.recurring ? ' is-recurring' : ''}`} key={t.theme}>
                                        <span className="ig-theme-label">{t.label}</span>
                                        <span className="ig-theme-count">
                                            {t.count} · {t.games_count} game{t.games_count === 1 ? '' : 's'}
                                            {t.recurring ? ' · recurring' : ''}
                                        </span>
                                    </li>
                                ))}
                            </ul>
                        </>
                    )}
                </section>
            )}
            <ConfirmDialog
                open={pendingRemove !== null}
                title="Remove this imported game?"
                body={<RemoveGameBody game={games.find(g => g.id === pendingRemove) ?? null} />}
                confirmLabel="Delete game"
                busy={removing}
                onConfirm={() => void remove()}
                onCancel={() => setPendingRemove(null)}
            />
        </>
    );
}
