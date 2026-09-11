import { useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { authService } from '../services/authService';
import '../components/AccountMenu.css';
import './account.css';
import { AuthShell } from './AuthShell';
import { useAuthConfig } from './useAuthConfig';

/**
 * The two halves of a password reset: asking for a link, and using one.
 *
 * The thing to preserve if either page is edited: **`/forgot-password` shows
 * the same confirmation whether or not the address has an account.** The
 * server is careful to answer identically; a frontend that said "no account
 * with that email" would hand back the enumeration oracle the backend just
 * went to some trouble to close. The success copy is deliberately written to
 * be true in both cases.
 */

export function ForgotPassword() {
    const config = useAuthConfig();
    const [email, setEmail] = useState('');
    const [busy, setBusy] = useState(false);
    const [sent, setSent] = useState<string | null>(null);
    // Distinct from `sent`: the server may have accepted the request and told
    // us it cannot send at all, which is a different screen from "check your
    // inbox". Telling someone to watch for an email that will never arrive is
    // worse than telling them the truth.
    const [available, setAvailable] = useState(true);
    const [error, setError] = useState<string | null>(null);

    const submit = async (e: React.FormEvent) => {
        e.preventDefault();
        setBusy(true);
        setError(null);
        try {
            const res = await authService.forgotPassword(email);
            setAvailable(res.email_available !== false);
            // The server writes this sentence. Showing its message rather than
            // one of our own keeps the promise on screen identical to the
            // promise the backend actually makes.
            setSent(res.message);
        } catch (err) {
            // Only a genuine transport or rate-limit failure reaches here -
            // an unknown address is a success, by design.
            setError(err instanceof Error ? err.message : 'Could not send the reset link.');
        }
        setBusy(false);
    };

    if (sent && !available) {
        return (
            <AuthShell title="Reset by email isn't available yet" sub={sent}>
                <p className="auth-note">
                    Your account and its history are safe and unaffected. This deployment
                    simply has no email provider configured yet, so there is nothing to
                    check your inbox for.
                </p>
                <Link className="acct-btn acct-btn-primary auth-submit" to="/signin">
                    Back to sign in
                </Link>
            </AuthShell>
        );
    }

    if (sent) {
        return (
            <AuthShell title="Check your email" sub={sent}>
                <p className="auth-note">
                    The link opens a page where you choose a new password. It works once. If
                    nothing arrives, check your spam folder before trying again - asking twice
                    invalidates the first link.
                </p>
                <Link className="acct-btn acct-btn-primary auth-submit" to="/signin">
                    Back to sign in
                </Link>
            </AuthShell>
        );
    }

    // Said BEFORE the form, not after submitting it. Today a person types
    // their address, waits for a round trip, and is only then told the
    // mechanism is switched off. This keys on configuration - the same answer
    // for every visitor - so it does not become a way to tell whether a
    // particular address is registered, which is the property the uniform
    // response on this endpoint exists to protect.
    if (config && !config.email_available) {
        return (
            <AuthShell
                title="Password reset is unavailable"
                sub="We can't send reset emails at the moment, so there is no way to recover a forgotten password yet."
            >
                <p className="auth-note">
                    If you are still signed in somewhere, you can change your password from
                    Settings without needing an email.
                </p>
                <Link className="acct-btn acct-btn-primary auth-submit" to="/settings">
                    Go to settings
                </Link>
                <p className="auth-alt">
                    <Link className="auth-link" to="/signin">Back to sign in</Link>
                    <br />
                    <Link className="auth-minor" to="/">Keep playing as a guest</Link>
                </p>
            </AuthShell>
        );
    }

    return (
        <AuthShell
            title="Reset your password"
            sub="Enter the email address on your account and we'll send you a link."
        >
            <form className="auth-form" onSubmit={submit}>
                <label className="acct-label" htmlFor="forgot-email">Email</label>
                <input
                    id="forgot-email" className="acct-input" type="email" autoComplete="email"
                    value={email} required autoFocus
                    onChange={(e) => setEmail(e.target.value)}
                />
                <button className="acct-btn acct-btn-primary auth-submit" type="submit" disabled={busy}>
                    {busy ? 'Sending…' : 'Send reset link'}
                </button>
            </form>

            {error && <p className="acct-error">{error}</p>}

            <p className="auth-alt">
                Remembered it? <Link className="auth-link" to="/signin">Sign in</Link>
                <br />
                <Link className="auth-minor" to="/">Keep playing as a guest</Link>
            </p>
        </AuthShell>
    );
}

export function ResetPassword() {
    const [params] = useSearchParams();
    const navigate = useNavigate();
    const token = params.get('token') ?? '';
    const [password, setPassword] = useState('');
    const [confirm, setConfirm] = useState('');
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [done, setDone] = useState(false);

    if (!token) {
        return (
            <AuthShell
                title="That link is incomplete"
                sub="It is missing its reset code. Copy the whole link out of the email, or ask for a new one."
            >
                <Link className="acct-btn acct-btn-primary auth-submit" to="/forgot-password">
                    Send a new link
                </Link>
            </AuthShell>
        );
    }

    if (done) {
        return (
            <AuthShell
                title="Password changed"
                sub="You have been signed out everywhere. Sign in with your new password."
            >
                <Link className="acct-btn acct-btn-primary auth-submit" to="/signin">Sign in</Link>
            </AuthShell>
        );
    }

    const submit = async (e: React.FormEvent) => {
        e.preventDefault();
        // Checked here rather than server-side because it is not a security
        // rule - it is a typo guard, and the server has no second field to
        // compare against.
        if (password !== confirm) {
            setError('Those two passwords are not the same.');
            return;
        }
        setBusy(true);
        setError(null);
        try {
            await authService.resetPassword(token, password);
            setDone(true);
        } catch (err) {
            setError(err instanceof Error ? err.message : 'Could not reset the password.');
            setBusy(false);
        }
    };

    return (
        <AuthShell
            title="Choose a new password"
            sub="This link works once. Everything currently signed in to your account will be signed out."
        >
            <form className="auth-form" onSubmit={submit}>
                <label className="acct-label" htmlFor="reset-pw">New password</label>
                <input
                    id="reset-pw" className="acct-input" type="password" autoComplete="new-password"
                    value={password} required minLength={8} autoFocus
                    onChange={(e) => setPassword(e.target.value)}
                />
                <label className="acct-label" htmlFor="reset-pw2">Repeat it</label>
                <input
                    id="reset-pw2" className="acct-input" type="password" autoComplete="new-password"
                    value={confirm} required minLength={8}
                    onChange={(e) => setConfirm(e.target.value)}
                />
                <button className="acct-btn acct-btn-primary auth-submit" type="submit" disabled={busy}>
                    {busy ? 'Saving…' : 'Set new password'}
                </button>
            </form>

            {error && <p className="acct-error">{error}</p>}

            <p className="auth-alt">
                <button
                    type="button"
                    className="auth-link"
                    style={{ background: 'none', border: 'none', cursor: 'pointer', padding: 0 }}
                    onClick={() => navigate('/forgot-password')}
                >
                    Ask for a new link
                </button>
            </p>
        </AuthShell>
    );
}
