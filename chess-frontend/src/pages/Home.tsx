import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import App from '../App';
import { authService } from '../services/authService';
import { BUILD_VERSION } from '../buildInfo';
import '../components/AccountMenu.css';
import './beta.css';
import './home.css';

/**
 * The public homepage, and the switch that decides whether `/` is it.
 *
 * WHO SEES IT
 * -----------
 * A signed-out visitor who has never entered the app from this browser. The
 * evidence is App.tsx's own `chess-mode` key: the app writes it on every
 * mount, so a browser that has it has been in, and every CTA here writes it
 * before the app mounts - "Analyze a game" as `postmortem`, so the app opens
 * on Review. From then on `/` is the board again, so the ~15 "Back to the
 * board" links keep their meaning, a guest who has games is not asked to
 * introduce themselves to their own tool on every visit, and a verify script
 * that seeds `chess-mode` (most do) lands on the board as before. A signed-in
 * person never sees it. A URL carrying a query (`?practice=…` from the
 * profile) bypasses it, because that link was minted from inside the app.
 *
 * It only exists when the closed-beta gate is open: while gated, BetaGate
 * draws the door in front of every route, this one included, and the door
 * carries its own copy of the pitch.
 *
 * WHAT IT PROMISES
 * ----------------
 * Only what Review's Correction Card already does (CLAUDE.md §21): the key
 * decision, the intention prompt, the diagnosis, the practice position, and
 * the improvement profile. No numbers, no testimonials, no features that are
 * not on the other side of the button.
 */
/** App.tsx's mode key. Present means "has been in"; its value is the mode. */
const MODE_KEY = 'chess-mode';

function hasEntered(): boolean {
    try {
        return localStorage.getItem(MODE_KEY) !== null;
    } catch {
        return true; // no storage: the board, never a homepage that cannot be dismissed
    }
}

function markEntered(mode: 'game' | 'postmortem' = 'postmortem') {
    try {
        localStorage.setItem(MODE_KEY, mode);
    } catch {
        // storage unavailable - the choice just does not outlive this render
    }
}

/** `/`: the board for anyone who has been in; the homepage for a stranger. */
export function HomeOrApp() {
    const [entered, setEntered] = useState<boolean | null>(() =>
        hasEntered() || window.location.search !== '' ? true : null,
    );

    useEffect(() => {
        if (entered !== null) return;
        let live = true;
        authService.me()
            .then((me) => { if (live) setEntered(me.signed_in); })
            .catch(() => { if (live) setEntered(false); });
        return () => { live = false; };
    }, [entered]);

    // Same reasoning as BetaGate: nothing beats a board that is replaced.
    if (entered === null) return null;
    if (entered) return <App />;
    return <Home onEnter={(mode) => { markEntered(mode); setEntered(true); }} />;
}

const STEPS: [string, string][] = [
    ['Review your game', 'Upload a PGN or play a game inside Zugzwang.'],
    ['Find the key decision', 'Zugzwang highlights the move most worth learning from.'],
    ['Explain your intention', 'Were you trying to attack, defend, improve king safety, or were you unsure?'],
    ['Get a saved lesson', 'Your Correction Card connects your intention to what the position actually needed.'],
    ['Practise and remember', 'Zugzwang saves the lesson and looks for repeated patterns across future games.'],
];

export function Home({ onEnter }: { onEnter: (mode?: 'game' | 'postmortem') => void }) {
    return (
        <div className="beta-page home-page">
            <main className="beta-card beta-doc home-card">
                <p className="beta-wordmark">Zugzwang</p>

                <h1 className="beta-title home-title">
                    The chess coach that remembers why you keep making the same mistakes.
                </h1>
                <p className="beta-value home-subhero">
                    Most chess tools show the best move. Zugzwang helps you understand the
                    decision behind your mistake — then turns it into a lesson you can practise.
                </p>

                <p className="home-lede">
                    Bring one of your games.<br />
                    We find the decision that mattered.<br />
                    You tell us what you were trying to do.<br />
                    Zugzwang explains what the position needed instead.<br />
                    Then you practise the idea and save it to your improvement profile.
                </p>

                <div className="home-ctas">
                    <button
                        type="button"
                        className="acct-btn acct-btn-primary home-cta"
                        data-testid="home-analyze"
                        onClick={() => onEnter('postmortem')}
                    >
                        Analyze a game
                    </button>
                    <button
                        type="button"
                        className="acct-btn home-cta"
                        data-testid="home-guest"
                        onClick={() => onEnter('game')}
                    >
                        Play as guest
                    </button>
                    <Link
                        className="acct-btn acct-btn-quiet home-cta"
                        data-testid="home-signup"
                        to="/signup"
                        onClick={() => markEntered()}
                    >
                        Create account
                    </Link>
                </div>

                <h2 className="beta-access-title home-h2">How it works</h2>
                <ol className="home-steps">
                    {STEPS.map(([title, body]) => (
                        <li key={title}>
                            <strong>{title}</strong>
                            <span>{body}</span>
                        </li>
                    ))}
                </ol>

                <h2 className="beta-access-title home-h2">Guest, account, or Pro</h2>
                <div className="home-tiers">
                    <div>
                        <strong>Try as guest</strong>
                        <span>Play and review without creating an account.</span>
                    </div>
                    <div>
                        <strong>Create a free account</strong>
                        <span>Save your games, corrections, and improvement profile.</span>
                    </div>
                    <div>
                        <strong>Upgrade to Pro</strong>
                        <span>
                            Unlock the full recurring-pattern history in your improvement
                            profile as Zugzwang learns from your games.
                        </span>
                    </div>
                </div>
            </main>

            <footer className="beta-footer">
                <Link className="beta-footer-link" to="/terms">Terms</Link>
                <span className="beta-footer-sep" aria-hidden="true">·</span>
                <Link className="beta-footer-link" to="/privacy">Privacy</Link>
                <span className="beta-footer-sep" aria-hidden="true">·</span>
                <Link className="beta-footer-link" to="/signin" onClick={() => markEntered()}>Sign in</Link>
                <span className="beta-footer-version" title="The build this page came from">
                    {BUILD_VERSION}
                </span>
            </footer>
        </div>
    );
}
