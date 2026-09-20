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

/** The half of the code that carries no entropy, shown beside the box. */
const CODE_PREFIX = 'ZG-BETA-';
/** The half that does. Eight characters, in two groups of four. */
const SECRET_LEN = 8;

/**
 * The eight characters a person actually typed, whatever they typed around them.
 *
 * The box holds the SECRET only, and `ZG-BETA-` is printed beside it as a fixed
 * adornment rather than sitting inside the field. That is not decoration - an
 * earlier version put the whole code in the box and re-inserted the prefix on
 * every keystroke, which meant the field re-parsed its own output: typing
 * `zgbeta…` one character at a time had the prefix's own letters consumed as
 * secret characters, and `ZG-BETA-DZPM-RYF7` came out as `ZG-BETA-ZGBE-TADZ`.
 * A field that silently corrupts a correct code is worse than no formatting at
 * all, and it only shows up when somebody types instead of pastes.
 *
 * So this never invents a character. It uppercases, drops punctuation, and
 * strips a `ZGBETA` prefix if one is there - which is what a paste of the whole
 * printed code looks like - and keeps at most eight characters.
 *
 * The server normalises independently (`beta_service.canonical`) and is the
 * authority. This exists so the box does not fight the person typing in it,
 * not so the client can decide what is valid.
 */
function secretOf(raw: string): string {
    let clean = raw.replace(/[^A-Za-z0-9]/g, '').toUpperCase();
    if (clean.startsWith('ZGBETA')) {
        clean = clean.slice('ZGBETA'.length);
    }
    return clean.slice(0, SECRET_LEN);
}

/** `XXXX-XXXX`, as it is printed on the invitation. */
function grouped(secret: string): string {
    return (secret.match(/.{1,4}/g) ?? []).join('-');
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
            // The field holds the secret; the wire carries the whole code.
            await betaService.redeem(CODE_PREFIX + code);
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
                    The chess coach that remembers why you keep making the same mistakes.
                </h1>

                <p className="beta-value">
                    Bring one of your games, or play one here. Zugzwang finds the decision
                    that mattered, asks what you were trying to do, and explains what the
                    position needed instead. You get a saved lesson, and you practise the idea.
                </p>

                <ol className="beta-loop" aria-label="How Zugzwang works">
                    <li>Bring a game</li>
                    <li>Key decision</li>
                    <li>Your aim</li>
                    <li>Saved lesson</li>
                    <li>Practise</li>
                </ol>

                <h2 className="beta-access-title">
                    {signedIn ? 'This account has no invitation yet' : 'Access is by invitation'}
                </h2>

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
                    {/* The prefix is a label, not a value. Every code starts with
                        it, it carries none of the entropy, and keeping it out of
                        the field is what stops the field from having to guess
                        whether a leading Z is part of the prefix or part of the
                        secret. Pasting the whole printed code still works -
                        `secretOf` strips it. */}
                    <div className="beta-field">
                        <span className="beta-prefix" aria-hidden="true">{CODE_PREFIX}</span>
                        <input
                            id="beta-code"
                            className="beta-input"
                            type="text"
                            inputMode="text"
                            autoComplete="one-time-code"
                            autoCapitalize="characters"
                            spellCheck={false}
                            autoFocus
                            placeholder="XXXX-XXXX"
                            value={grouped(code)}
                            aria-label={`Access code, after ${CODE_PREFIX}`}
                            aria-describedby={error ? 'beta-error' : 'beta-hint'}
                            aria-invalid={error ? true : undefined}
                            onChange={(e) => { setCode(secretOf(e.target.value)); setError(null); }}
                        />
                    </div>
                    <p className="beta-hint" id="beta-hint">
                        Codes look like {CODE_PLACEHOLDER}. Case and dashes do not
                        matter, and you can paste the whole thing.
                    </p>

                    {error && (
                        <p className="beta-error" id="beta-error" role="alert">{error}</p>
                    )}

                    <button
                        className="acct-btn acct-btn-primary beta-submit"
                        type="submit"
                        disabled={busy || code.length < SECRET_LEN}
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
