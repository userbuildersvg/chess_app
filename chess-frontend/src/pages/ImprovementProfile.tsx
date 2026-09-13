import { useCallback, useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import {
    NeedsAccount, SOURCE_LABELS, profileService,
    type Finding, type ImportedGame, type Profile, type Progress,
} from '../services/profileService';
import { GUEST_IMPORT_NOTE } from '../components/ExternalImport';
import { SiteFooter } from '../components/SiteFooter';
import '../components/AccountMenu.css';
import './account.css';
import './profile.css';

/**
 * Build Improvement Profile - the multi-game workflow.
 *
 * WHY THIS IS A PAGE AND NOT A FOURTH MODE
 * ----------------------------------------
 * Play, Learn and Review are peers, all mounted at once and hidden rather than
 * unmounted because each holds state a person would be furious to lose. This is
 * not that: it is a workflow you go into, do something in, and come out of, and
 * it holds nothing that a navigation would destroy - the library is on the
 * account and the analysis runs on the server whether or not this page is open.
 * Making it a fourth tab would also change the one sentence the header has to
 * say, which is that there are three modes.
 *
 * It is reached from a button at the bottom of Review, which is where somebody
 * who has just analysed one game is standing when the idea of analysing twenty
 * becomes interesting.
 *
 * WHY IT ASKS FOR AN ACCOUNT
 * --------------------------
 * Everything else in this app works as a guest, deliberately. A profile is a
 * claim built from ten or more games over days, and a guest identity is a
 * cookie - someone who imported a library as a guest and then cleared their
 * cookies would lose all of it with no warning that this was possible. The 401
 * is rendered as an invitation rather than an error.
 */

const POLL_MS = 4000;

function Confidence({ level }: { level: Finding['confidence'] }) {
    return (
        <span className={`pf-chip pf-conf-${level}`} title="How much evidence stands behind this">
            {level} confidence
        </span>
    );
}

function Trend({ trend }: { trend: Finding['trend'] }) {
    // The arrow points at what is happening to the MISTAKE, so "improving"
    // is fewer of them. Labelled in words as well, because an arrow alone
    // reverses meaning depending on which of those two you assume.
    const mark = trend === 'improving' ? '↓' : trend === 'worsening' ? '↑' : '→';
    return (
        <span className={`pf-chip pf-trend-${trend}`}>
            {mark} {trend}
        </span>
    );
}

function FindingCard({ finding }: { finding: Finding }) {
    return (
        <article className="pf-finding">
            <header className="pf-finding-head">
                <h3 className="pf-claim">{finding.claim}</h3>
                <div className="pf-chips">
                    <Confidence level={finding.confidence} />
                    <Trend trend={finding.trend} />
                </div>
            </header>

            {finding.description && <p className="pf-desc">{finding.description}</p>}

            <dl className="pf-evidence">
                <div><dt>Evidence</dt><dd>{finding.evidence_count} moves</dd></div>
                <div><dt>Across</dt><dd>{finding.games_count} games</dd></div>
                <div><dt>First seen</dt><dd>game #{finding.first_seen_game}</dd></div>
                <div><dt>Most recent</dt><dd>game #{finding.last_seen_game}</dd></div>
            </dl>

            {finding.representative.length > 0 && (
                <div className="pf-examples">
                    <p className="pf-examples-title">Where it happened</p>
                    <ul className="pf-example-list">
                        {finding.representative.map((r, i) => (
                            <li key={`${r.game_id}-${r.ply}-${i}`} className="pf-example">
                                <span className="pf-example-move">
                                    {Math.ceil(r.ply / 2)}
                                    {r.ply % 2 === 1 ? '.' : '...'} {r.move_san}
                                </span>
                                {r.best_san && (
                                    <span className="pf-example-best">engine: {r.best_san}</span>
                                )}
                                <span className="pf-example-meta">
                                    game #{r.game_id} · {r.phase}
                                    {r.cpl != null && ` · −${(r.cpl / 100).toFixed(1)}`}
                                </span>
                            </li>
                        ))}
                    </ul>
                </div>
            )}

            {finding.check && (
                <p className="pf-check"><strong>Try this:</strong> {finding.check}</p>
            )}
        </article>
    );
}

export function ImprovementProfile() {
    const [needsAccount, setNeedsAccount] = useState<string | null>(null);
    const [games, setGames] = useState<ImportedGame[]>([]);
    const [progress, setProgress] = useState<Progress | null>(null);
    const [profile, setProfile] = useState<Profile | null>(null);
    const [paste, setPaste] = useState('');
    const [busy, setBusy] = useState(false);
    const [notice, setNotice] = useState<string | null>(null);
    // Kept apart from `notice`: this one is a thing the person still has to do.
    const [leftOut, setLeftOut] = useState<string | null>(null);
    const [error, setError] = useState<string | null>(null);
    const fileInput = useRef<HTMLInputElement>(null);

    const load = useCallback(async () => {
        try {
            const [lib, prof] = await Promise.all([
                profileService.games(),
                profileService.profile(),
            ]);
            setGames(lib.games);
            setProgress(lib.progress);
            setProfile(prof);
            setNeedsAccount(null);
        } catch (e) {
            if (e instanceof NeedsAccount) setNeedsAccount(e.message);
            else setError(e instanceof Error ? e.message : 'Could not load your library.');
        }
    }, []);

    useEffect(() => { void load(); }, [load]);

    // Poll only while there is something to watch. A page that polls forever
    // keeps a free instance awake doing nothing, and the analysis runs on the
    // server whether or not anybody is looking at this screen.
    useEffect(() => {
        if (!progress || progress.remaining === 0) return;
        const id = window.setInterval(() => { void load(); }, POLL_MS);
        return () => window.clearInterval(id);
    }, [progress, load]);

    const importText = async (text: string, name: string) => {
        if (!text.trim()) return;
        setBusy(true);
        setError(null);
        setNotice(null);
        setLeftOut(null);
        try {
            const result = await profileService.importPgn(text, name);
            // The server's sentence, not one assembled here. It is the only
            // place that knows what happened to every game in the request, and
            // it accounts for all four outcomes - added, duplicate, unreadable,
            // and left out for being past the per-request limit.
            setNotice(result.message ?? `Added ${result.added} game${result.added === 1 ? '' : 's'}.`);
            // Anything past the limit needs an action from the person, so it
            // gets its own line rather than sitting at the end of a sentence
            // about successes.
            setLeftOut(result.ignored > 0
                ? `${result.ignored} game${result.ignored === 1 ? ' was' : 's were'} not imported - `
                  + `only ${result.limit} can be sent at once. Add the rest in another batch; `
                  + `nothing was lost, they simply were not read.`
                : null);
            setPaste('');
            await load();
        } catch (e) {
            if (e instanceof NeedsAccount) setNeedsAccount(e.message);
            else setError(e instanceof Error ? e.message : 'Those games could not be added.');
        } finally {
            setBusy(false);
        }
    };

    const takeFiles = async (files: FileList | null) => {
        if (!files || files.length === 0) return;
        // Read every file and send them as one body. A person exporting a
        // season gets one file per month, and importing them one request at a
        // time would spend the rate limit on the shape of their export.
        const parts: string[] = [];
        for (const file of Array.from(files)) {
            parts.push(await file.text());
        }
        await importText(parts.join('\n\n'), files.length === 1 ? files[0].name : `${files.length} files`);
    };

    const remove = async (id: number) => {
        try {
            await profileService.remove(id);
            await load();
        } catch (e) {
            setError(e instanceof Error ? e.message : 'That game could not be removed.');
        }
    };

    if (needsAccount) {
        return (
            <div className="settings-page">
                <div className="settings-shell">
                    <header className="settings-head">
                        <p className="auth-brand">Zugzwang</p>
                        <h1 className="settings-h1">Improvement profile</h1>
                        <Link className="auth-minor" to="/">Back to the board</Link>
                    </header>
                    <section className="settings-card">
                        <h2 className="settings-card-title">This one needs an account</h2>
                        <p className="settings-card-sub">{needsAccount}</p>
                        <p className="settings-card-sub" data-testid="guest-import-note">{GUEST_IMPORT_NOTE}</p>
                        <div className="pf-actions">
                            <Link className="acct-btn acct-btn-primary" to="/signup">Create an account</Link>
                            <Link className="acct-btn acct-btn-quiet" to="/signin">Sign in</Link>
                        </div>
                    </section>
                    <SiteFooter />
                </div>
            </div>
        );
    }

    const analysed = progress?.done ?? 0;
    const remaining = progress?.remaining ?? 0;
    const total = progress?.total ?? 0;
    const pct = total > 0 ? Math.round((analysed / total) * 100) : 0;

    return (
        <div className="settings-page">
            <div className="settings-shell">
                <header className="settings-head">
                    <p className="auth-brand">Zugzwang</p>
                    <h1 className="settings-h1">Improvement profile</h1>
                    <Link className="auth-minor" to="/">Back to the board</Link>
                </header>

                {/* --- import ------------------------------------------------ */}
                <section className="settings-card">
                    <h2 className="settings-card-title">Import your games</h2>
                    <p className="settings-card-sub">
                        Add PGN files or paste them in. A file with a whole season in it is fine -
                        every game inside is imported separately. Analysis runs in the background,
                        so you can close this page.
                    </p>

                    <div className="pf-actions">
                        <button
                            type="button"
                            className="acct-btn acct-btn-primary"
                            disabled={busy}
                            onClick={() => fileInput.current?.click()}
                        >
                            {busy ? 'Adding…' : 'Choose PGN files'}
                        </button>
                        <input
                            ref={fileInput}
                            className="pf-file-input"
                            type="file"
                            multiple
                            accept=".pgn,application/x-chess-pgn,text/plain"
                            onChange={e => { void takeFiles(e.target.files); e.target.value = ''; }}
                        />
                    </div>

                    <label className="acct-label" htmlFor="pf-paste">Or paste PGN</label>
                    <textarea
                        id="pf-paste"
                        className="acct-input pf-textarea"
                        rows={5}
                        value={paste}
                        placeholder={'[Event "..."]\n1. e4 e5 ...'}
                        onChange={e => setPaste(e.target.value)}
                    />
                    <button
                        type="button"
                        className="acct-btn"
                        disabled={busy || !paste.trim()}
                        onClick={() => void importText(paste, 'pasted.pgn')}
                    >
                        Add pasted games
                    </button>

                    {notice && <p className="pf-notice">{notice}</p>}
                    {leftOut && <p className="pf-leftout" role="alert">{leftOut}</p>}
                    {error && <p className="acct-error">{error}</p>}
                </section>

                {/* --- progress ---------------------------------------------- */}
                {total > 0 && (
                    <section className="settings-card">
                        <h2 className="settings-card-title">Analysis</h2>
                        <p className="settings-card-sub">
                            {remaining > 0
                                ? `${analysed} of ${total} games analysed. ${remaining} to go - this runs on the server, so you can leave.`
                                : `All ${total} games analysed.`}
                        </p>
                        <div
                            className="pf-bar"
                            role="progressbar"
                            aria-valuenow={pct}
                            aria-valuemin={0}
                            aria-valuemax={100}
                            aria-label="Games analysed"
                        >
                            <div className="pf-bar-fill" style={{ width: `${pct}%` }} />
                        </div>
                        {progress && progress.failed > 0 && (
                            <p className="pf-notice">
                                {progress.failed} game{progress.failed === 1 ? '' : 's'} could not be
                                analysed. They are listed below with the reason.
                            </p>
                        )}
                    </section>
                )}

                {/* --- the profile ------------------------------------------- */}
                <section className="settings-card">
                    <h2 className="settings-card-title">What your games show</h2>
                    {profile && !profile.ready ? (
                        <>
                            <p className="settings-card-sub">
                                Nothing yet, and that is deliberate. A pattern needs{' '}
                                <strong>{profile.minimum_games} analysed games</strong> behind it
                                before it is worth telling you about - anything less describes a bad
                                afternoon rather than a habit.
                            </p>
                            <p className="pf-needed">
                                {profile.games_needed > 0
                                    ? `${profile.games_needed} more game${profile.games_needed === 1 ? '' : 's'} to go.`
                                    : 'Analysing what you have added.'}
                            </p>
                        </>
                    ) : profile && profile.findings.length === 0 ? (
                        <p className="settings-card-sub">
                            {profile.analysed_games} games analysed, and no pattern appears in enough
                            of them to call it recurring. That is a real answer, not an empty screen.
                        </p>
                    ) : (
                        <>
                            <p className="settings-card-sub">
                                From {profile?.analysed_games} analysed games. Every claim below is
                                counted from your own moves, and you can check each one against the
                                games it came from.
                            </p>
                            <div className="pf-findings">
                                {profile?.findings.map(f => (
                                    <FindingCard key={f.theme} finding={f} />
                                ))}
                            </div>
                        </>
                    )}
                </section>

                {/* --- the library ------------------------------------------- */}
                {games.length > 0 && (
                    <section className="settings-card">
                        <h2 className="settings-card-title">Your games ({games.length})</h2>
                        <ul className="pf-games">
                            {games.map(g => (
                                <li key={g.id} className="pf-game">
                                    <span className="pf-game-main">
                                        <span className="pf-game-players">
                                            {g.white ?? '?'} vs {g.black ?? '?'}
                                        </span>
                                        <span className="pf-game-meta">
                                            {SOURCE_LABELS[g.source] ?? g.source} · you played {g.player_color} · {g.ply_count} plies
                                            {g.result ? ` · ${g.result}` : ''}
                                            {g.played_on ? ` · ${g.played_on}` : ''}
                                        </span>
                                        {g.state === 'failed' && g.error && (
                                            <span className="pf-game-error">{g.error}</span>
                                        )}
                                    </span>
                                    <span className={`pf-state pf-state-${g.state}`}>
                                        {g.state === 'done'
                                            ? `${g.findings} found`
                                            : g.state === 'analysing'
                                              ? 'analysing…'
                                              : g.state}
                                    </span>
                                    <button
                                        type="button"
                                        className="acct-btn acct-btn-quiet pf-remove"
                                        onClick={() => void remove(g.id)}
                                        aria-label={`Remove ${g.white ?? '?'} vs ${g.black ?? '?'}`}
                                    >
                                        Remove
                                    </button>
                                </li>
                            ))}
                        </ul>
                    </section>
                )}

                <SiteFooter />
            </div>
        </div>
    );
}
