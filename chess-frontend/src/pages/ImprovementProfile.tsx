import { useCallback, useEffect, useRef, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import {
    NeedsAccount, SOURCE_LABELS, profileService,
    type Finding, type ImportedGame, type Profile, type Progress,
} from '../services/profileService';
import { GUEST_IMPORT_NOTE } from '../components/ExternalImport';
import { learningService } from '../services/learningService';
import type { Correction } from '../types/learning';
import { SiteFooter } from '../components/SiteFooter';
import { ConfirmDialog, RemoveGameBody } from '../components/ConfirmDialog';
import { ProCard } from '../components/ProCard';
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
    const why = trend === 'improving'
        ? 'Rarer in your later games than your earlier ones'
        : trend === 'worsening'
            ? 'More common in your later games than your earlier ones'
            : 'About as common in your later games as your earlier ones';
    return (
        <span className={`pf-chip pf-trend-${trend}`} title={why}>
            {mark} {trend}
        </span>
    );
}

function FindingCard({ finding, onReview, onPractice, practicing, note, practised }: {
    finding: Finding;
    onReview: (gameId: number) => void;
    onPractice: (theme: string) => void;
    practicing: string | null;
    note: string | null;
    /** Local, this browser only: the outcome of the practice session just
     *  finished for this theme. Not persisted anywhere. */
    practised: 'passed' | 'missed' | null;
}) {
    const [showEvidence, setShowEvidence] = useState(false);
    const seenIn = finding.last_seen?.game_label ?? null;
    return (
        <article className="pf-finding" data-theme-id={finding.theme}>
            <header className="pf-finding-head">
                <h3 className="pf-claim">{finding.claim}</h3>
                <div className="pf-chips">
                    <Confidence level={finding.confidence} />
                    <Trend trend={finding.trend} />
                    {practised && (
                        <span className={`pf-chip pf-practised-${practised}`} data-testid="pf-practised">
                            {practised === 'passed' ? 'Practised just now' : 'Needs another attempt'}
                        </span>
                    )}
                </div>
            </header>

            <p className="pf-meta" data-testid="pf-meta">
                {finding.trend === 'stable' ? 'Stable pattern' : finding.trend === 'improving' ? 'Fading pattern' : 'Growing pattern'}
                {' · '}{finding.games_count} game{finding.games_count === 1 ? '' : 's'}
                {' · '}{finding.evidence_count} decision{finding.evidence_count === 1 ? '' : 's'}
            </p>
            {seenIn && <p className="pf-seen" data-testid="pf-seen">Seen in: {seenIn}</p>}

            {finding.description && <p className="pf-desc">{finding.description}</p>}

            {finding.check && (
                <div className="pf-rule" data-testid="pf-rule">
                    <span className="pf-rule-label">Next-time rule</span>
                    <p>{finding.check}</p>
                </div>
            )}

            <div className="pf-actions">
                {finding.practice_available ? (
                    <button
                        type="button"
                        className="acct-btn acct-btn-primary"
                        data-testid="pf-practice"
                        disabled={practicing !== null}
                        onClick={() => onPractice(finding.theme)}
                    >
                        {practicing === finding.theme ? 'Opening Learn…' : 'Practice this'}
                    </button>
                ) : (
                    <button type="button" className="acct-btn" disabled data-testid="pf-practice-unavailable"
                        title="This evidence is missing the position snapshot practice needs">
                        Practice unavailable
                    </button>
                )}
                <button type="button" className="acct-btn acct-btn-quiet" aria-expanded={showEvidence}
                    data-testid="pf-show-evidence" onClick={() => setShowEvidence(v => !v)}>
                    {showEvidence ? 'Hide evidence' : 'Show evidence'}
                </button>
                {note && <p className="acct-error pf-practice-note" role="alert" data-testid="pf-practice-note">{note}</p>}
                {!finding.practice_available && (
                    <p className="settings-row-hint pf-practice-note" data-testid="pf-practice-why">
                        Practice is not available for this theme yet because this evidence is missing the position snapshot.
                        You can still open any of the games below in Review.
                    </p>
                )}
            </div>

            {showEvidence && (
                <div className="pf-examples" data-testid="pf-evidence">
                    <dl className="pf-evidence">
                        <div className="pf-evidence-wide"><dt>First seen</dt><dd>{finding.first_seen?.game_label ?? `game #${finding.first_seen_game}`}</dd></div>
                        <div className="pf-evidence-wide"><dt>Most recent</dt><dd>{finding.last_seen?.game_label ?? `game #${finding.last_seen_game}`}</dd></div>
                    </dl>
                    <p className="pf-examples-title">Where it happened</p>
                    <ul className="pf-example-list">
                        {finding.representative.map((r, i) => (
                            <li key={`${r.finding_id}-${i}`} className="pf-example" data-testid="pf-example">
                                <div className="pf-example-line">
                                    <span className="pf-example-move">
                                        {Math.ceil(r.ply / 2)}
                                        {r.ply % 2 === 1 ? '.' : '...'} {r.move_san}
                                    </span>
                                    {r.best_san && (
                                        <span className="pf-example-best">→ engine preferred {r.best_san}</span>
                                    )}
                                    <span className="pf-example-meta">
                                        {r.phase}
                                        {r.cpl != null && ` · lost ${(r.cpl / 100).toFixed(1)} pawns`}
                                    </span>
                                </div>
                                <div className="pf-example-line">
                                    <span className="pf-example-game">{r.game_label ?? `game #${r.game_id}`}</span>
                                    {r.can_review_game && (
                                        <button type="button" className="pf-link" onClick={() => onReview(r.game_id)}>
                                            Review game
                                        </button>
                                    )}
                                </div>
                            </li>
                        ))}
                    </ul>
                </div>
            )}
        </article>
    );
}

export function ImprovementProfile() {
    const [needsAccount, setNeedsAccount] = useState<string | null>(null);
    const [games, setGames] = useState<ImportedGame[]>([]);
    const [progress, setProgress] = useState<Progress | null>(null);
    const [profile, setProfile] = useState<Profile | null>(null);
    // The newest Correction Card on the account, shown before the ten-game
    // threshold so the page is not empty for someone who has already saved
    // a lesson. A card, never a pattern: one game proves nothing recurring.
    const [latestLesson, setLatestLesson] = useState<Correction | null>(null);
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
            // Separate and best-effort: a failed cards read must not take the
            // library down with it.
            learningService.corrections()
                .then(r => setLatestLesson([...r.corrections].sort((a, b) => b.last_seen_at - a.last_seen_at)[0] ?? null))
                .catch(() => setLatestLesson(null));
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

    const navigate = useNavigate();
    const [pendingRemove, setPendingRemove] = useState<number | null>(null);
    const [removing, setRemoving] = useState(false);
    const [practicing, setPracticing] = useState<string | null>(null);
    // Said next to the button that was pressed, not at the top of the page.
    const [practiceNote, setPracticeNote] = useState<{ theme: string; text: string } | null>(null);
    // What Learn recorded for the practice session that just ended, if any.
    // sessionStorage, read once: visible continuity, not persistence.
    const [practised] = useState<{ theme: string; outcome: 'passed' | 'missed' } | null>(() => {
        try {
            const raw = sessionStorage.getItem('zz-practised');
            return raw ? JSON.parse(raw) : null;
        } catch { return null; }
    });

    // Clear failed imports: every library row the worker could not read or
    // analyse, removed one by one through the same owner-scoped DELETE a
    // single row uses. Done games, corrections and findings are not touched -
    // a failed row has no findings to lose.
    const [confirmClear, setConfirmClear] = useState(false);
    const [clearing, setClearing] = useState(false);
    const failedGames = games.filter(g => g.state === 'failed');
    const clearFailed = async () => {
        setClearing(true);
        setError(null);
        try {
            for (const g of failedGames) {
                await profileService.remove(g.id);
            }
            await load();
        } catch (e) {
            setError(e instanceof Error ? e.message : 'The failed imports could not be cleared.');
        }
        setClearing(false);
        setConfirmClear(false);
    };

    const remove = async () => {
        if (pendingRemove === null) return;
        setRemoving(true);
        try {
            await profileService.remove(pendingRemove);
            await load();
        } catch (e) {
            setError(e instanceof Error ? e.message : 'That game could not be removed.');
        }
        setRemoving(false);
        setPendingRemove(null);
    };

    /** Into Review - the same handoff Account settings uses. */
    const review = async (id: number) => {
        setError(null);
        try {
            const state = await profileService.review(id);
            try {
                localStorage.setItem('postmortem-game', state.game_id);
                localStorage.setItem('chess-mode', 'postmortem');
            } catch { /* Review opens on its empty canvas */ }
            navigate('/');
        } catch (e) {
            setError(e instanceof Error ? e.message : 'That game could not be opened.');
        }
    };

    /**
     * Into Learn, on a position from one of this person's own games. The
     * server opens the sandbox session; the browser remembers its id under
     * the key Learn resumes from and switches mode - the same path a reload
     * takes, so there is no second way into Learn.
     */
    const practice = async (theme: string) => {
        setPracticing(theme);
        setPracticeNote(null);
        try {
            const r = await profileService.practice(theme);
            if (!r.available || !r.practice_session_id) {
                setPracticeNote({ theme, text: r.reason ?? 'Practice is not available for this mistake yet.' });
                setPracticing(null);
                return;
            }
            try {
                localStorage.setItem('sandbox-session', r.practice_session_id);
                localStorage.setItem('chess-mode', 'sandbox');
                localStorage.setItem('sandbox-panel', 'chat');
            } catch { /* the URL below carries the session anyway */ }
            // The session id rides in the URL as well as localStorage, so a
            // blocked or stale localStorage cannot drop it on the way over.
            navigate(`/?practice=${encodeURIComponent(r.practice_session_id)}`);
        } catch (e) {
            setPracticeNote({ theme, text: e instanceof Error ? e.message : 'Practice could not be opened. Try again in a moment.' });
            setPracticing(null);
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

                <ProCard onUpgraded={() => void load()} />

                {/* --- where you stand ----------------------------------------- */}
                {profile && !profile.ready && (
                    <section className="settings-card pf-progress" data-testid="pf-progress">
                        <p className="pf-progress-count">
                            <strong>{profile.analysed_games}</strong>/{profile.minimum_games} analysed games toward your first recurring pattern
                        </p>
                        <div
                            className="pf-bar"
                            role="progressbar"
                            aria-valuenow={Math.min(100, Math.round((profile.analysed_games / Math.max(1, profile.minimum_games)) * 100))}
                            aria-valuemin={0}
                            aria-valuemax={100}
                            aria-label="Analysed games toward the first recurring pattern"
                            data-testid="pf-threshold-bar"
                        >
                            <div className="pf-bar-fill" style={{ width: `${Math.min(100, (profile.analysed_games / Math.max(1, profile.minimum_games)) * 100)}%` }} />
                        </div>
                        {progress && progress.total > progress.analysed && (
                            <p className="settings-row-hint" data-testid="pf-progress-pending">
                                {progress.total} imported · {progress.analysed} analysed · {progress.remaining} still being analysed on the server - nothing to do but wait, and you can leave.
                                {progress.failed ? ` ${progress.failed} could not be analysed; see the list below.` : ''}
                            </p>
                        )}
                        <p className="settings-card-sub">
                            Zugzwang waits for enough evidence before calling something a recurring
                            weakness. A single bad game can be noise; repeated patterns are what matter.
                        </p>
                        <div className="pf-actions">
                            <Link className="acct-btn acct-btn-primary" to="/settings#imported-games">Import recent games</Link>
                            <button type="button" className="acct-btn" onClick={() => fileInput.current?.click()}>Upload PGN</button>
                        </div>
                        <details className="pf-unlock">
                            <summary className="settings-row-hint">What unlocks at {profile.minimum_games} games?</summary>
                            <p className="settings-row-hint">
                                At {profile.minimum_games} analysed games, Zugzwang starts looking for recurring themes across
                                your decisions and shows only patterns with enough evidence to practise responsibly -
                                each one named, counted, and tied to the exact games it came from.
                            </p>
                        </details>
                    </section>
                )}

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
                    {error && <p className="acct-error" role="alert">{error}</p>}
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
                            <div className="pf-notice pf-failed" data-testid="pf-failed">
                                <p>
                                    {progress.failed} game{progress.failed === 1 ? '' : 's'} could not be
                                    analysed. They are listed below with the reason.
                                </p>
                                <button
                                    type="button"
                                    className="acct-btn pf-clear-failed"
                                    onClick={() => setConfirmClear(true)}
                                    disabled={failedGames.length === 0}
                                    data-testid="pf-clear-failed"
                                >
                                    Clear failed imports
                                </button>
                            </div>
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
                            <p className="pf-needed" data-testid="pf-next">
                                {profile.games_needed > 0
                                    ? `Bring ${profile.games_needed} more game${profile.games_needed === 1 ? '' : 's'} to discover repeated mistakes.`
                                    : 'Analysing what you have added.'}
                            </p>
                            {latestLesson && (
                                <div className="pf-lesson" data-testid="pf-latest-lesson">
                                    <span className="pf-lesson-eyebrow">Latest saved lesson · {latestLesson.theme_label}</span>
                                    <p className="pf-lesson-text">{latestLesson.missed_factor || latestLesson.diagnosis}</p>
                                    <p className="settings-row-hint">{latestLesson.correction_rule}</p>
                                </div>
                            )}
                        </>
                    ) : profile && profile.findings.length === 0 ? (
                        <p className="settings-card-sub">
                            {profile.analysed_games} games analysed, and no pattern appears in enough
                            of them to call it recurring. That is a real answer, not an empty screen.
                        </p>
                    ) : (
                        <>
                            <p className="settings-card-sub">
                                Your recurring weaknesses, from {profile?.analysed_games} analysed games. Every
                                claim is counted from your own moves; open the evidence to see the exact games.
                                Practice opens a position from one of them in Learn.
                            </p>
                            <div className="pf-findings">
                                {profile?.findings.map(f => (
                                    <FindingCard key={f.theme} finding={f} onReview={id => void review(id)} onPractice={t => void practice(t)} practicing={practicing}
                                        note={practiceNote?.theme === f.theme ? practiceNote.text : null}
                                        practised={practised?.theme === f.theme ? practised.outcome : null} />
                                ))}
                                {/* Themes the server holds back on the Free plan. Real
                                    rows, real counts - only ever rendered when the server
                                    sent them, never invented to make Pro look bigger. */}
                                {profile?.locked_findings.map(l => (
                                    <article key={l.theme} className="pf-finding pf-finding-locked" data-testid="pf-locked">
                                        <div className="pf-finding-head">
                                            <h3 className="pf-claim">{l.label}</h3>
                                            <span className="pf-chip">Pro</span>
                                        </div>
                                        <p className="pf-desc">
                                            Seen in {l.games_count} game{l.games_count === 1 ? '' : 's'} · {l.evidence_count} decision{l.evidence_count === 1 ? '' : 's'}
                                        </p>
                                        <p className="settings-row-hint">Upgrade to Pro for full recurring-pattern history.</p>
                                    </article>
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
                                        className="acct-btn acct-btn-primary"
                                        onClick={() => void review(g.id)}
                                    >
                                        Review this game
                                    </button>
                                    <button
                                        type="button"
                                        className="acct-btn acct-btn-quiet pf-remove"
                                        onClick={() => setPendingRemove(g.id)}
                                        aria-label={`Remove ${g.white ?? '?'} vs ${g.black ?? '?'}`}
                                    >
                                        Remove
                                    </button>
                                </li>
                            ))}
                        </ul>
                    </section>
                )}

                <ConfirmDialog
                    open={confirmClear}
                    title="Clear failed imports?"
                    body={<p>This removes games that Zugzwang could not read or analyze. Successfully imported games and saved lessons will stay.</p>}
                    confirmLabel="Clear failed imports"
                    busy={clearing}
                    onConfirm={() => void clearFailed()}
                    onCancel={() => setConfirmClear(false)}
                />
                <ConfirmDialog
                    open={pendingRemove !== null}
                    title="Remove this imported game?"
                    body={<RemoveGameBody game={games.find(g => g.id === pendingRemove) ?? null} />}
                    confirmLabel="Delete game"
                    busy={removing}
                    onConfirm={() => void remove()}
                    onCancel={() => setPendingRemove(null)}
                />
                <SiteFooter />
            </div>
        </div>
    );
}
