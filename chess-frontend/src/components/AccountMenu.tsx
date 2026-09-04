import { useCallback, useEffect, useRef, useState } from 'react';
import { authService, type AuthConfig, type WhoAmI } from '../services/authService';
import './AccountMenu.css';

/**
 * The account UI: real, finished, and currently unavailable.
 *
 * Built the way it will actually ship - a Guest indicator, a Sign in entry
 * point, a Create account entry point, and a real form behind them - so that
 * enabling accounts is deleting a guard rather than building an interface.
 *
 * WHAT "UNAVAILABLE" MEANS HERE
 * -----------------------------
 * The buttons are NOT hidden and NOT disabled. Both are worse than they look:
 * a hidden control leaves the user with no idea accounts are coming, and a
 * greyed-out one gives them nothing to click and no explanation. Instead the
 * entry points work, and what opens is a plain notice saying accounts are not
 * available yet and that they are playing as a guest.
 *
 * The wording comes from the SERVER (`unavailable_message`), not from this
 * file, so what a user is told cannot drift from what the backend actually
 * does. And the backend refuses the routes regardless of what this component
 * renders - hiding a button in React would leave /api/auth/signup open to
 * anyone who opened devtools.
 *
 * When ACCOUNTS_ENABLED flips on the backend, `config.accounts_enabled`
 * becomes true and the same entry points open the real form instead. Nothing
 * in this file needs editing for that to happen.
 */

type Panel = 'closed' | 'notice' | 'signin' | 'signup';

export function AccountMenu() {
    const [config, setConfig] = useState<AuthConfig | null>(null);
    const [who, setWho] = useState<WhoAmI | null>(null);
    const [panel, setPanel] = useState<Panel>('closed');
    const [username, setUsername] = useState('');
    const [password, setPassword] = useState('');
    const [error, setError] = useState<string | null>(null);
    const [busy, setBusy] = useState(false);
    const dialogRef = useRef<HTMLDivElement | null>(null);
    const openerRef = useRef<HTMLButtonElement | null>(null);

    const refresh = useCallback(async () => {
        try {
            const [cfg, me] = await Promise.all([authService.config(), authService.me()]);
            setConfig(cfg);
            setWho(me);
        } catch {
            // The header must never be the thing that breaks the page. If the
            // account endpoints cannot be reached at all, fall back to the
            // truthful assumption: this is a guest, and accounts are off.
            setConfig({ accounts_enabled: false, guest_mode: true, unavailable_message: null });
            setWho({ signed_in: false, guest: true, username: null, accounts_enabled: false });
        }
    }, []);

    useEffect(() => {
        void refresh();
    }, [refresh]);

    const close = useCallback(() => {
        setPanel('closed');
        setError(null);
        setPassword('');
        // Focus goes back to the control that opened the dialog. Without it,
        // closing drops the caret to the top of the document, which is
        // disorienting with a mouse and genuinely lost with a keyboard.
        openerRef.current?.focus();
    }, []);

    useEffect(() => {
        if (panel === 'closed') return;
        const onKey = (e: KeyboardEvent) => {
            if (e.key === 'Escape') close();
        };
        window.addEventListener('keydown', onKey);
        // Move focus into the dialog on open, for the same reason.
        const first = dialogRef.current?.querySelector<HTMLElement>(
            'input, button:not([disabled])',
        );
        first?.focus();
        return () => window.removeEventListener('keydown', onKey);
    }, [panel, close]);

    const accountsOn = config?.accounts_enabled === true;

    /** Sign in and Create account both land here while accounts are off. */
    const open = (intent: 'signin' | 'signup') => {
        setError(null);
        setPanel(accountsOn ? intent : 'notice');
    };

    const submit = async (e: React.FormEvent) => {
        e.preventDefault();
        setError(null);
        setBusy(true);
        try {
            if (panel === 'signup') {
                await authService.signup(username, password);
            } else {
                await authService.login(username, password);
            }
            setPassword('');
            await refresh();
            setPanel('closed');
        } catch (err) {
            // Includes the 503 the server sends if accounts were switched off
            // between this component loading and the form being submitted -
            // the server is the authority, and it says so in its own words.
            setError(err instanceof Error ? err.message : 'Something went wrong.');
        } finally {
            setBusy(false);
        }
    };

    const signOut = async () => {
        setBusy(true);
        try {
            await authService.logout();
            await refresh();
        } finally {
            setBusy(false);
        }
    };

    const signedIn = who?.signed_in === true;

    return (
        <div className="acct">
            {signedIn ? (
                <>
                    <span className="acct-who" title="Signed in">
                        {who?.username}
                    </span>
                    <button
                        type="button"
                        className="acct-btn"
                        onClick={() => void signOut()}
                        disabled={busy}
                    >
                        Sign out
                    </button>
                </>
            ) : (
                <>
                    {/* Says what you ARE, not what is missing. A guest is a
                        real, working state in this build - the whole app
                        works - so the chip is informational rather than a
                        prompt to fix something. The title explains the one
                        consequence that matters. */}
                    <span
                        className="acct-who acct-who-guest"
                        title="Playing as a guest - nothing you do here is saved"
                    >
                        Guest
                    </span>
                    <button
                        type="button"
                        className="acct-btn"
                        ref={openerRef}
                        onClick={() => open('signin')}
                    >
                        Sign in
                    </button>
                    <button
                        type="button"
                        className="acct-btn acct-btn-quiet"
                        onClick={() => open('signup')}
                    >
                        Create account
                    </button>
                </>
            )}

            {panel !== 'closed' && (
                <div className="acct-overlay" role="presentation" onClick={close}>
                    <div
                        className="acct-dialog"
                        role="dialog"
                        aria-modal="true"
                        aria-labelledby="acct-title"
                        ref={dialogRef}
                        onClick={e => e.stopPropagation()}
                    >
                        {panel === 'notice' ? (
                            <>
                                <h2 className="acct-title" id="acct-title">
                                    Accounts aren't available yet
                                </h2>
                                <p className="acct-body">
                                    {config?.unavailable_message ??
                                        "Accounts aren't available yet - you're playing as a guest."}
                                </p>
                                <p className="acct-body acct-body-dim">
                                    Everything works as a guest: play the coach, use Learner
                                    Mode, change the difficulty, ask about the position. The
                                    only difference is that nothing is saved - your game lives
                                    in this browser session and is gone when the server
                                    restarts.
                                </p>
                                <div className="acct-actions">
                                    <button type="button" className="acct-btn acct-btn-primary" onClick={close}>
                                        Keep playing as a guest
                                    </button>
                                </div>
                            </>
                        ) : (
                            <form onSubmit={submit}>
                                <h2 className="acct-title" id="acct-title">
                                    {panel === 'signup' ? 'Create an account' : 'Sign in'}
                                </h2>
                                <label className="acct-label" htmlFor="acct-username">
                                    Username
                                </label>
                                <input
                                    id="acct-username"
                                    className="acct-input"
                                    value={username}
                                    autoComplete="username"
                                    onChange={e => setUsername(e.target.value)}
                                />
                                <label className="acct-label" htmlFor="acct-password">
                                    Password
                                </label>
                                <input
                                    id="acct-password"
                                    className="acct-input"
                                    type="password"
                                    value={password}
                                    autoComplete={
                                        panel === 'signup' ? 'new-password' : 'current-password'
                                    }
                                    onChange={e => setPassword(e.target.value)}
                                />
                                {error && <p className="acct-error">{error}</p>}
                                <div className="acct-actions">
                                    <button type="button" className="acct-btn" onClick={close}>
                                        Cancel
                                    </button>
                                    <button
                                        type="submit"
                                        className="acct-btn acct-btn-primary"
                                        disabled={busy}
                                    >
                                        {busy
                                            ? 'Working...'
                                            : panel === 'signup'
                                              ? 'Create account'
                                              : 'Sign in'}
                                    </button>
                                </div>
                            </form>
                        )}
                    </div>
                </div>
            )}
        </div>
    );
}
