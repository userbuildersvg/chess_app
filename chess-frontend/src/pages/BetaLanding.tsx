import { useState } from 'react';
import { Link } from 'react-router-dom';
import { betaService } from '../services/betaService';
import { BUILD_VERSION } from '../buildInfo';
import '../components/AccountMenu.css';
import './beta.css';

/**
 * The front door. What everybody who has not redeemed a code sees.
 *
 * WHAT THIS PAGE IS FOR
 * ---------------------
 * A person arriving here is in one of three states, and the page has to be
 * true for all of them without asking which: an invited tester with a code, a
 * returning tester whose access lives on their account, and someone who found
 * the URL. The first gets the code box, the second gets Sign in, and the third
 * gets an honest explanation and a way to ask - which is why "Request beta
 * access" is a real link rather than a dead-end message.
 *
 * WHAT IT IS NOT
 * --------------
 * It is not the security boundary, and it must never be improved into one.
 * `beta_gate.py` refuses unauthorized API requests before any route runs; this
 * page is what a browser draws while that is true. Everything below could be
 * deleted from a running browser and the API would answer exactly as it does
 * now. That separation is the whole design, and it is the reason there is no
 * "unlocked" flag stored anywhere on this side.
 *
 * THE DESIGN REGISTER
 * -------------------
 * The same one as the account surfaces: a single card on the obsidian ground,
 * `--cp-*` tokens, one accent used once. Zugzwang is an analytical tool and its
 * front door should read like the inside of it. No hero image, no gradient, no
 * countdown - a person who gets in should not feel they have arrived somewhere
 * else.
 */

/** How the code is written down, and what the box is shaped for. */
const CODE_PLACEHOLDER = 'ZG-BETA-XXXX-XXXX';

/**
 * Tidy what somebody typed into the shape the code is printed in.
 *
 * Uppercases, strips everything that is not alphanumeric, then re-inserts the
 * dashes. So a pasted `zg-beta-7k4m-2qx9`, a hand-typed `zgbeta7k4m2qx9` and a
 * copy that picked up a trailing space all become the same thing on screen.
 *
 * The server normalises independently (`beta_service.canonical`) and is the
 * authority - this is here so the box does not fight the person typing in it,
 * not so the client can decide what is valid.
 */
function format(raw: string): string {
    const clean = raw.replace(/[^A-Za-z0-9]/g, '').toUpperCase();
    // The literal prefix, whatever the person typed, so the groups below line
    // up with what is printed on the invitation.
    const withoutPrefix = clean.startsWith('ZGBETA') ? clean.slice(6) : clean;
    const groups = (withoutPrefix.match(/.{1,4}/g) ?? []).slice(0, 2);
    if (!clean) return '';
    return ['ZG', 'BETA', ...groups].join('-');
}

export function BetaLanding({ signedIn, onGranted }: { signedIn: boolean; onGranted: () => void }) {
    const [code, setCode] = useState('');
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState<string | null>(null);

    const submit = async (e: React.FormEvent) => {
        e.preventDefault();
        if (busy) return;
        setBusy(true);
        setError(null);
        try {
            await betaService.redeem(code);
            // Reload rather than re-render. Access changing is not a piece of
            // state this page owns - it changes what every service in the app
            // is allowed to fetch, and several of them read their starting
            // state during the first render. The same reasoning the sign-in
            // page gives for reloading after hydrating preferences.
            onGranted();
        } catch (err) {
            setError(err instanceof Error ? err.message : 'That code could not be checked. Try again.');
            setBusy(false);
        }
    };

    return (
        <div className="beta-page">
            <main className="beta-card">
                <p className="beta-wordmark">Zugzwang</p>
                <p className="beta-eyebrow">Private Beta</p>

                <h1 className="beta-title">
                    {signedIn ? 'This account has no invitation yet' : 'Access is by invitation'}
                </h1>

                <p className="beta-body">
                    Zugzwang is currently in a closed beta while we work with a limited number
                    of chess players to improve the learning experience.
                </p>
                <p className="beta-body">
                    {signedIn
                        ? 'You are signed in, but this account has not redeemed an access code. Enter one below and it stays with your account from then on.'
                        : 'Access requires a beta invitation. Enter your code below – you only ever need to do this once.'}
                </p>

                <form className="beta-form" onSubmit={submit}>
                    <label className="beta-label" htmlFor="beta-code">Access code</label>
                    <input
                        id="beta-code"
                        className="beta-input"
                        type="text"
                        inputMode="text"
                        autoComplete="one-time-code"
                        autoCapitalize="characters"
                        spellCheck={false}
                        autoFocus
                        placeholder={CODE_PLACEHOLDER}
                        value={code}
                        aria-describedby={error ? 'beta-error' : 'beta-hint'}
                        aria-invalid={error ? true : undefined}
                        onChange={(e) => { setCode(format(e.target.value)); setError(null); }}
                    />
                    <p className="beta-hint" id="beta-hint">
                        Codes look like {CODE_PLACEHOLDER}. Case and dashes do not matter.
                    </p>

                    {error && (
                        <p className="beta-error" id="beta-error" role="alert">{error}</p>
                    )}

                    <button
                        className="acct-btn acct-btn-primary beta-submit"
                        type="submit"
                        disabled={busy || code.length < CODE_PLACEHOLDER.length}
                    >
                        {busy ? 'Checking…' : 'Continue'}
                    </button>
                </form>

                {/* A returning tester on a new browser has no guest grant - their
                    access lives on their account - so signing in has to be
                    reachable from here or their invitation is unusable. */}
                {!signedIn && (
                    <p className="beta-alt">
                        Already a beta tester? <Link className="beta-link" to="/signin">Sign in</Link>
                    </p>
                )}
            </main>

            <footer className="beta-footer">
                <Link className="beta-footer-link" to="/request-access">Request beta access</Link>
                <span className="beta-footer-sep" aria-hidden="true">·</span>
                <Link className="beta-footer-link" to="/contact">Contact</Link>
                <span className="beta-footer-sep" aria-hidden="true">·</span>
                <Link className="beta-footer-link" to="/privacy">Privacy</Link>
                <span className="beta-footer-sep" aria-hidden="true">·</span>
                <Link className="beta-footer-link" to="/terms">Terms</Link>
                <span className="beta-footer-sep" aria-hidden="true">·</span>
                <Link className="beta-footer-link" to="/about">About</Link>
                <span className="beta-footer-version" title="The build this page came from">
                    {BUILD_VERSION}
                </span>
            </footer>
        </div>
    );
}
