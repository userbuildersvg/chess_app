import { useEffect, useState, type ReactNode } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { authService, type AuthConfig } from '../services/authService';
import { GOOGLE_START_URL } from '../services/accountService';
import { hydrateFromAccount, seedAccountFromLocal, setSignedIn } from '../services/preferences';
import '../components/AccountMenu.css';
import './account.css';

/**
 * The dedicated sign-in and create-account surfaces.
 *
 * They are pages rather than a dropdown panel because signing in is not an
 * incidental action taken while looking at a board - it is the moment a
 * person's history stops being tied to one browser. A surface with its own
 * URL can also be linked to, returned to after a Google round trip, and
 * bookmarked, none of which a dropdown can do.
 *
 * Both end in the same place: a session cookie, `/` and a reload.
 */

function GoogleMark() {
    // Google's own mark, inlined. Their brand guidelines require the real
    // four-colour G rather than a monochrome approximation, and this is the
    // only place in the app where a foreign brand colour is allowed to sit.
    return (
        <svg viewBox="0 0 48 48" aria-hidden="true" focusable="false">
            <path fill="#EA4335" d="M24 9.5c3.5 0 6.6 1.2 9 3.6l6.7-6.7C35.6 2.6 30.2 0 24 0 14.6 0 6.5 5.4 2.6 13.2l7.8 6.1C12.3 13.2 17.7 9.5 24 9.5z" />
            <path fill="#4285F4" d="M46.1 24.6c0-1.6-.1-3.2-.4-4.6H24v9.1h12.4c-.5 2.9-2.2 5.3-4.6 7l7.6 5.9c4.4-4.1 6.7-10.1 6.7-17.4z" />
            <path fill="#FBBC05" d="M10.4 28.7a14.6 14.6 0 010-9.4l-7.8-6.1a24 24 0 000 21.6l7.8-6.1z" />
            <path fill="#34A853" d="M24 48c6.5 0 11.9-2.1 15.9-5.8l-7.6-5.9c-2.1 1.4-4.8 2.3-8.3 2.3-6.3 0-11.7-3.7-13.6-9.8l-7.8 6.1C6.5 42.6 14.6 48 24 48z" />
        </svg>
    );
}

function AuthShell({ title, sub, children }: { title: string; sub: string; children: ReactNode }) {
    return (
        <div className="auth-page">
            <div className="auth-card">
                <p className="auth-brand">Zugzwang</p>
                <h1 className="auth-title">{title}</h1>
                <p className="auth-sub">{sub}</p>
                {children}
            </div>
        </div>
    );
}

/** Shared by both pages: read config once, and know whether to draw Google. */
function useAuthConfig() {
    const [config, setConfig] = useState<AuthConfig | null>(null);
    useEffect(() => {
        authService.config()
            .then(setConfig)
            .catch(() => setConfig({
                accounts_enabled: false, guest_mode: true, google: false,
                unavailable_message: null,
            }));
    }, []);
    return config;
}

function GoogleButton({ label }: { label: string }) {
    return (
        <>
            <div className="auth-divider">or</div>
            {/* A real navigation, not a fetch: the browser has to travel to
                Google and back, and the server sets its CSRF state cookie on
                the way out. */}
            <a className="acct-btn acct-btn-quiet auth-google" href={GOOGLE_START_URL}>
                <GoogleMark />
                {label}
            </a>
        </>
    );
}

function Unavailable({ message }: { message: string | null }) {
    return (
        <AuthShell
            title="Accounts aren't available yet"
            sub={message ?? "You're playing as a guest - everything works, and your history stays with this browser."}
        >
            <Link className="acct-btn acct-btn-primary auth-submit" to="/">Keep playing as a guest</Link>
        </AuthShell>
    );
}

export function SignIn() {
    const config = useAuthConfig();
    const navigate = useNavigate();
    const [identifier, setIdentifier] = useState('');
    const [password, setPassword] = useState('');
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState<string | null>(null);

    if (config && !config.accounts_enabled) {
        return <Unavailable message={config.unavailable_message} />;
    }

    const submit = async (e: React.FormEvent) => {
        e.preventDefault();
        setBusy(true);
        setError(null);
        try {
            await authService.login(identifier, password);
            setSignedIn(true);
            // Pull this account's preferences down before showing the app, so
            // the board arrives looking the way this person left it on
            // whatever device they last used.
            await hydrateFromAccount();
            navigate('/');
            // A full reload, deliberately: the preferences above are read
            // during render from localStorage, and React state already
            // mounted would not see them change.
            window.location.reload();
        } catch (err) {
            setError(err instanceof Error ? err.message : 'Sign-in failed. Try again.');
            setBusy(false);
        }
    };

    return (
        <AuthShell title="Sign in" sub="Your games, profile and settings, on any device.">
            <form className="auth-form" onSubmit={submit}>
                <label className="acct-label" htmlFor="signin-id">Email or username</label>
                <input
                    id="signin-id" className="acct-input" type="text" autoComplete="username"
                    value={identifier} required autoFocus
                    onChange={(e) => setIdentifier(e.target.value)}
                />
                <label className="acct-label" htmlFor="signin-pw">Password</label>
                <input
                    id="signin-pw" className="acct-input" type="password" autoComplete="current-password"
                    value={password} required
                    onChange={(e) => setPassword(e.target.value)}
                />
                <button className="acct-btn acct-btn-primary auth-submit" type="submit" disabled={busy}>
                    {busy ? 'Signing in…' : 'Sign in'}
                </button>
            </form>

            {config?.google && <GoogleButton label="Sign in with Google" />}

            {error && <p className="acct-error">{error}</p>}

            <p className="auth-alt">
                New here? <Link className="auth-link" to="/signup">Create an account</Link>
                <br />
                {/* The entry point exists so the flow has a home when reset is
                    built; it says plainly that it does not work yet rather
                    than linking somewhere that 404s. */}
                <span className="auth-minor" title="Not built yet">Forgotten your password? Not available yet</span>
                <br />
                <Link className="auth-minor" to="/">Keep playing as a guest</Link>
            </p>
        </AuthShell>
    );
}

export function SignUp() {
    const config = useAuthConfig();
    const navigate = useNavigate();
    const [username, setUsername] = useState('');
    const [email, setEmail] = useState('');
    const [password, setPassword] = useState('');
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState<string | null>(null);

    if (config && !config.accounts_enabled) {
        return <Unavailable message={config.unavailable_message} />;
    }

    const submit = async (e: React.FormEvent) => {
        e.preventDefault();
        setBusy(true);
        setError(null);
        try {
            const result = await authService.signup(username, password, email);
            setSignedIn(true);
            // Carry this browser's choices INTO the new account rather than
            // pulling the account's empty defaults down over them - someone
            // who spent an evening as a guest picking a board should keep it.
            await seedAccountFromLocal();
            navigate('/', { state: { claimed: result.claimed_games } });
            window.location.reload();
        } catch (err) {
            setError(err instanceof Error ? err.message : 'Could not create the account.');
            setBusy(false);
        }
    };

    return (
        <AuthShell
            title="Create your account"
            sub="So your games and your coaching history stop belonging to one browser."
        >
            <p className="auth-note">
                The games you have already played in this browser will be moved to your new
                account. They stay claimable for 30 days.
            </p>

            <form className="auth-form" onSubmit={submit}>
                <label className="acct-label" htmlFor="signup-user">Username</label>
                <input
                    id="signup-user" className="acct-input" type="text" autoComplete="username"
                    value={username} required autoFocus minLength={3} maxLength={32}
                    onChange={(e) => setUsername(e.target.value)}
                />
                <label className="acct-label" htmlFor="signup-email">Email</label>
                <input
                    id="signup-email" className="acct-input" type="email" autoComplete="email"
                    value={email} required
                    onChange={(e) => setEmail(e.target.value)}
                />
                <label className="acct-label" htmlFor="signup-pw">Password</label>
                <input
                    id="signup-pw" className="acct-input" type="password" autoComplete="new-password"
                    value={password} required minLength={8}
                    onChange={(e) => setPassword(e.target.value)}
                />
                <button className="acct-btn acct-btn-primary auth-submit" type="submit" disabled={busy}>
                    {busy ? 'Creating…' : 'Create account'}
                </button>
            </form>

            {config?.google && <GoogleButton label="Continue with Google" />}

            {error && <p className="acct-error">{error}</p>}

            <p className="auth-alt">
                Already have an account? <Link className="auth-link" to="/signin">Sign in</Link>
                <br />
                <Link className="auth-minor" to="/">Keep playing as a guest</Link>
            </p>
        </AuthShell>
    );
}
