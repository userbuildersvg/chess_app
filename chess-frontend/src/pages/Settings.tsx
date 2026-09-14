import { useCallback, useEffect, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { accountService, type AccountProfile, type Prefs } from '../services/accountService';
import { authService } from '../services/authService';
import { setSignedIn } from '../services/preferences';
import { PIECE_THEME_LIST } from '../pieceThemes';
import { SiteFooter } from '../components/SiteFooter';
import { DataRetention } from '../components/DataRetention';
import { ImportedGames } from '../components/ImportedGames';
import '../components/AccountMenu.css';
import './account.css';

/**
 * The account area: who you are, how you sign in, what the board looks like,
 * and the way out.
 *
 * Deliberately short. Every row here is either a fact about the account or a
 * preference that already existed in the product and now has an owner - there
 * are no settings invented to fill the page, because a settings screen full
 * of switches nobody asked for is how a product starts feeling like a
 * template.
 *
 * Every preference on this page is stored in Postgres against the account and
 * applies on every device it signs in from. None of them is frontend-only
 * state dressed up as a setting.
 */

const PREF_ROWS: { key: keyof Prefs; label: string; hint: string }[] = [
    {
        key: 'showCoordinates',
        label: 'Board coordinates',
        hint: 'File and rank labels around the edge of the board.',
    },
    {
        key: 'showEngineNumbers',
        label: 'Evaluation bar and engine numbers',
        hint: 'The bar beside the board and the centipawn figures with it.',
    },
    {
        key: 'showMoveQuality',
        label: 'Move grading',
        hint: 'Chess.com-style badges on each move, and the accuracy figures in Review.',
    },
    {
        key: 'guidedPlay',
        label: 'Guided Play',
        hint: 'After the AI moves, show what to watch for before your reply.',
    },
];

export function Settings() {
    const navigate = useNavigate();
    const [profile, setProfile] = useState<AccountProfile | null>(null);
    const [prefs, setPrefs] = useState<Prefs | null>(null);
    const [loading, setLoading] = useState(true);
    const [loadError, setLoadError] = useState<string | null>(null);
    const [savedAt, setSavedAt] = useState<number | null>(null);

    const [currentPw, setCurrentPw] = useState('');
    const [newPw, setNewPw] = useState('');
    const [pwBusy, setPwBusy] = useState(false);
    const [pwError, setPwError] = useState<string | null>(null);
    const [pwOk, setPwOk] = useState(false);

    const [inviteCode, setInviteCode] = useState('');
    const [inviteBusy, setInviteBusy] = useState(false);
    const [inviteError, setInviteError] = useState<string | null>(null);
    const [inviteOk, setInviteOk] = useState<string | null>(null);

    const [confirmName, setConfirmName] = useState('');
    const [confirmPw, setConfirmPw] = useState('');
    const [delBusy, setDelBusy] = useState(false);
    const [delError, setDelError] = useState<string | null>(null);

    useEffect(() => {
        (async () => {
            try {
                const [p, pr] = await Promise.all([accountService.profile(), accountService.prefs()]);
                setProfile(p);
                setPrefs(pr);
            } catch (err) {
                const status = (err as Error & { status?: number }).status;
                if (status === 401 || status === 403) {
                    // Not signed in, or the session expired underneath us.
                    // The sign-in page is the only useful destination.
                    navigate('/signin', { replace: true });
                    return;
                }
                // Anything else (the database away for a moment, a 503) is
                // said in words - not "Loading…" forever, not a sign-in page
                // for someone who is signed in.
                setLoadError(err instanceof Error ? err.message : 'Your account could not be loaded. Try again in a moment.');
                return;
            }
            setLoading(false);
        })();
    }, [navigate]);

    /**
     * Save one preference.
     *
     * Optimistic: the control moves immediately and the request follows. A
     * checkbox that waits for a round trip before it visibly changes feels
     * broken, and the cost of being wrong here is one setting out of step
     * until the next change - not lost work.
     */
    const savePref = useCallback(async (patch: Partial<Prefs>) => {
        setPrefs((current) => (current ? { ...current, ...patch } : current));
        try {
            const saved = await accountService.savePrefs(patch);
            setPrefs(saved);
            setSavedAt(Date.now());
            // Keep this device in step with what the account now says, so a
            // reload does not show the old value.
            for (const [k, v] of Object.entries(patch)) {
                const localKey = {
                    pieceTheme: 'chess-piece-theme',
                    showCoordinates: 'chess-coordinates',
                    showEngineNumbers: 'chess-engine-numbers',
                    showMoveQuality: 'chess-move-quality',
                    activeSection: 'chess-active-section',
                    guidedPlay: 'chess-guided-play',
                }[k];
                if (localKey) {
                    try { localStorage.setItem(localKey, String(v)); } catch { /* unavailable */ }
                }
            }
        } catch {
            setSavedAt(null);
        }
    }, []);

    const changePassword = async (e: React.FormEvent) => {
        e.preventDefault();
        setPwBusy(true);
        setPwError(null);
        setPwOk(false);
        try {
            await accountService.changePassword(currentPw, newPw);
            setPwOk(true);
            setCurrentPw('');
            setNewPw('');
        } catch (err) {
            setPwError(err instanceof Error ? err.message : 'Could not change the password.');
        }
        setPwBusy(false);
    };

    const redeemInvite = async (e: React.FormEvent) => {
        e.preventDefault();
        setInviteBusy(true);
        setInviteError(null);
        setInviteOk(null);
        try {
            const r = await accountService.becomeAdmin(inviteCode);
            // Re-read the profile before announcing, so the Admin panel link
            // is there by the time the message is: admin status is read fresh
            // on every request server-side, no re-login needed.
            setProfile(await accountService.profile());
            setInviteOk(r.message);
        } catch (err) {
            setInviteError(err instanceof Error ? err.message : 'That code could not be used.');
        }
        setInviteCode('');
        setInviteBusy(false);
    };

    const removeAccount = async (e: React.FormEvent) => {
        e.preventDefault();
        setDelBusy(true);
        setDelError(null);
        try {
            await accountService.deleteAccount(confirmName, hasPassword ? confirmPw : undefined);
            setSignedIn(false);
            navigate('/');
            window.location.reload();
        } catch (err) {
            setDelError(err instanceof Error ? err.message : 'Could not delete the account.');
            setDelBusy(false);
        }
    };

    const signOut = async () => {
        try { await authService.logout(); } catch { /* signing out must always work */ }
        setSignedIn(false);
        navigate('/');
        window.location.reload();
    };

    if (loading || !profile || !prefs) {
        return (
            <div className="settings-page">
                <div className="settings-shell">
                    {loadError
                        ? <p className="acct-error" role="alert">{loadError}</p>
                        : <p className="settings-saved">Loading your account…</p>}
                </div>
            </div>
        );
    }

    const hasPassword = profile.auth_methods.includes('password');
    const methodLabel = profile.auth_methods
        .map((m) => (m === 'password' ? 'Email and password' : m === 'google' ? 'Google' : m))
        .join(' + ') || 'None';

    return (
        <div className="settings-page">
            <div className="settings-shell">
                <div className="settings-head">
                    <h1 className="settings-h1">Account</h1>
                    <Link className="acct-btn acct-btn-quiet" to="/">Back to the board</Link>
                </div>
                {profile.admin && (
                    <p className="settings-card-sub">
                        <Link className="auth-link" to="/admin" data-testid="admin-link">Admin panel</Link>
                    </p>
                )}

                <section className="settings-card">
                    <h2 className="settings-card-title">Who you are</h2>
                    <p className="settings-card-sub">
                        Your games and coaching history belong to this account, on every device you
                        sign in from.
                    </p>
                    <div className="settings-row">
                        <span className="settings-row-label">Username</span>
                        <span className="settings-row-value">{profile.username}</span>
                    </div>
                    <div className="settings-row">
                        <span className="settings-row-label">Email</span>
                        <span className={profile.email ? 'settings-row-value' : 'settings-row-value settings-row-value-empty'}>
                            {profile.email ?? 'Not set'}
                        </span>
                    </div>
                    <div className="settings-row">
                        <span className="settings-row-label">
                            Sign-in method
                            <span className="settings-row-hint">
                                Email addresses are not verified yet, so this is what you told us.
                            </span>
                        </span>
                        <span className="settings-row-value">{methodLabel}</span>
                    </div>
                    {!profile.admin && (
                        <details className="settings-invite" data-testid="become-admin">
                            <summary className="settings-row-hint">Have an admin invite code?</summary>
                            <form className="settings-invite-form" onSubmit={redeemInvite}>
                                <input
                                    id="admin-invite" className="acct-input" type="text" autoComplete="off"
                                    spellCheck={false} maxLength={64} required placeholder="zz-admin-…"
                                    aria-label="Admin invite code"
                                    value={inviteCode} onChange={(e) => setInviteCode(e.target.value)}
                                />
                                <button className="acct-btn acct-btn-quiet" type="submit" disabled={inviteBusy}>
                                    {inviteBusy ? 'Checking…' : 'Activate'}
                                </button>
                            </form>
                            {inviteError && <p className="acct-error">{inviteError}</p>}
                        </details>
                    )}
                    {inviteOk && <p className="auth-ok" data-testid="become-admin-ok">{inviteOk}</p>}
                </section>

                <section className="settings-card">
                    <h2 className="settings-card-title">Board and coaching</h2>
                    <p className="settings-card-sub">
                        Saved to your account, so the board looks the same wherever you sign in.
                        {savedAt && <span className="settings-saved"> Saved.</span>}
                    </p>

                    <div className="settings-row">
                        <span className="settings-row-label">
                            Piece set
                            <span className="settings-row-hint">Also sets the board colours.</span>
                        </span>
                        <select
                            className="settings-select"
                            value={prefs.pieceTheme ?? PIECE_THEME_LIST[0]?.id ?? ''}
                            onChange={(e) => savePref({ pieceTheme: e.target.value })}
                        >
                            {PIECE_THEME_LIST.map((theme) => (
                                <option key={theme.id} value={theme.id}>{theme.label}</option>
                            ))}
                        </select>
                    </div>

                    {PREF_ROWS.map((row) => (
                        <div className="settings-row" key={row.key}>
                            <span className="settings-row-label">
                                {row.label}
                                <span className="settings-row-hint">{row.hint}</span>
                            </span>
                            <input
                                className="settings-check"
                                type="checkbox"
                                aria-label={row.label}
                                checked={Boolean(prefs[row.key])}
                                onChange={(e) => savePref({ [row.key]: e.target.checked } as Partial<Prefs>)}
                            />
                        </div>
                    ))}
                </section>

                <section className="settings-card">
                    <h2 className="settings-card-title">Password</h2>
                    {hasPassword ? (
                        <>
                            <p className="settings-card-sub">
                                Changing it signs you out everywhere else. This device stays signed in.
                            </p>
                            <form className="auth-form" onSubmit={changePassword}>
                                <label className="acct-label" htmlFor="cur-pw">Current password</label>
                                <input
                                    id="cur-pw" className="acct-input" type="password"
                                    autoComplete="current-password" required
                                    value={currentPw} onChange={(e) => setCurrentPw(e.target.value)}
                                />
                                <label className="acct-label" htmlFor="new-pw">New password</label>
                                <input
                                    id="new-pw" className="acct-input" type="password"
                                    autoComplete="new-password" required minLength={8}
                                    value={newPw} onChange={(e) => setNewPw(e.target.value)}
                                />
                                <button className="acct-btn acct-btn-primary auth-submit" type="submit" disabled={pwBusy}>
                                    {pwBusy ? 'Changing…' : 'Change password'}
                                </button>
                            </form>
                            {pwError && <p className="acct-error">{pwError}</p>}
                            {pwOk && <p className="auth-ok">Password changed. Other devices have been signed out.</p>}
                        </>
                    ) : (
                        <p className="settings-card-sub">
                            This account signs in with Google, so it has no password to change.
                        </p>
                    )}
                </section>

                {/* Chess.com / Lichess import and the imported library, by
                    source. Account-only, which is why it is here and not on
                    the board. */}
                <ImportedGames />

                <DataRetention />

                <section className="settings-card">
                    <h2 className="settings-card-title">Sign out</h2>
                    <p className="settings-card-sub">
                        Ends this session. Your history stays on the account and is here when you
                        sign back in.
                    </p>
                    <button className="acct-btn acct-btn-quiet" type="button" onClick={signOut}>
                        Sign out
                    </button>
                </section>

                <section className="settings-card settings-danger">
                    <h2 className="settings-card-title">Delete account</h2>
                    <p className="settings-card-sub">
                        This happens immediately and cannot be undone. There is no backup you can
                        ask us for afterwards. Deleting removes:
                    </p>
                    <ul className="settings-list">
                        <li>your account, username and email address</li>
                        <li>every game you have played and every move in it</li>
                        <li>every game you imported, its analysis, and the patterns found in it</li>
                        <li>your saved board and coaching preferences</li>
                        <li>every session you are signed in on, on every device</li>
                    </ul>
                    <p className="settings-card-sub">
                        Zugzwang also destroys the encryption key for your account data. If encrypted
                        copies remain for a while in database backups, they cannot be decrypted without
                        that key. Aggregate counts that name no game, position or text may remain.
                    </p>
                    <p className="settings-card-sub">
                        You can carry on playing afterwards as a guest. Type{' '}
                        <strong>{profile.username}</strong>{hasPassword ? ' and your password' : ''} to confirm.
                    </p>
                    <form className="auth-form" onSubmit={removeAccount}>
                        <label className="acct-label" htmlFor="confirm-name">Your username</label>
                        <input
                            id="confirm-name" className="acct-input" type="text" autoComplete="off"
                            value={confirmName} onChange={(e) => setConfirmName(e.target.value)}
                        />
                        {hasPassword && (
                            <>
                                <label className="acct-label" htmlFor="confirm-pw">Your password</label>
                                <input
                                    id="confirm-pw" className="acct-input" type="password" autoComplete="current-password"
                                    value={confirmPw} onChange={(e) => setConfirmPw(e.target.value)}
                                />
                            </>
                        )}
                        <button
                            className="acct-btn auth-submit" type="submit"
                            disabled={delBusy || confirmName.trim().toLowerCase() !== profile.username.toLowerCase() || (hasPassword && !confirmPw)}
                        >
                            {delBusy ? 'Deleting…' : 'Delete my account'}
                        </button>
                    </form>
                    {delError && <p className="acct-error">{delError}</p>}
                </section>

                <SiteFooter />
            </div>
        </div>
    );
}
