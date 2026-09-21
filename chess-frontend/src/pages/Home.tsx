import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import App, { REVIEW_FOCUS_KEY } from '../App';
import { authService } from '../services/authService';
import { BUILD_VERSION } from '../buildInfo';
import { ThemeToggle } from '../components/ThemeToggle';
import '../components/AccountMenu.css';
import './home.css';

/**
 * The public homepage, and the switch that decides whether `/` is it.
 *
 * WHO SEES IT
 * -----------
 * A signed-out visitor who has never entered the app from this browser. The
 * evidence is App.tsx's own `chess-mode` key: the app writes it on every
 * mount, so a browser that has it has been in, and every CTA here writes it
 * before the app mounts - "Analyze a game right now" as `postmortem`, so the
 * app opens on Review (and on Review ONLY: it also sets App's review-focus
 * flag for this tab, so Play and Learn stay out of the way until the person
 * asks for them or signs in). From then on `/` is the board again, so the
 * ~15 "Back to the board" links keep their meaning, a guest who has games is
 * not asked to introduce themselves to their own tool on every visit, and a
 * verify script that seeds `chess-mode` (most do) lands on the board as
 * before. A signed-in person never sees it. A URL carrying a query
 * (`?practice=…` from the profile) bypasses it, because that link was minted
 * from inside the app.
 *
 * `/` is public during Shipaton even while private/account routes remain
 * behind the beta gate. The backend separately allowlists the guest demo APIs.
 *
 * WHAT IT PROMISES
 * ----------------
 * Only what Review, the Correction Card and the Improvement Profile already
 * do (CLAUDE.md §14, §21, §24): every move graded, the key decision, the
 * intention prompt, the diagnosis, the practice position, the saved lesson,
 * recurring patterns after ten analysed games. No numbers, no testimonials,
 * no features that are not on the other side of the button.
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

/** The loop, in the order it happens. Each is a thing Review actually does. */
const STEPS: [string, string][] = [
    ['Analyze a game', 'Upload a PGN from Chess.com, Lichess or any app - or review a game you just played here. The engine grades every move.'],
    ['Find the decision that mattered', 'Not the longest list of mistakes: the one move the game turned on, with the evaluation before and after it.'],
    ['Explain what you were trying to do', 'Attack, defend, simplify, or not sure. The coach reads your intention against what the position actually needed.'],
    ['Save the lesson', 'The diagnosis becomes a Correction Card filed under a theme - one card per theme, so a repeated mistake meets the same card again.'],
    ['Practise the idea', 'A fresh, engine-verified position that tests the same idea, with a hint if you want one. Play it on the board.'],
    ['Build My improvement', 'Bring more games and Zugzwang shows which mistakes recur, with the games as evidence and a practice position for each.'],
];

const REVIEW_GIVES: [string, string][] = [
    ['Every move graded', 'Best, good, inaccuracy, mistake, blunder - the same scale in Play and Review, from the engine, with the centipawn cost.'],
    ['Your biggest learning opportunity', 'The decision that changed the game, named first, with how the evaluation moved and a close-call caveat when the evidence is thin.'],
    ['A coach holding the evidence', 'Ask about any position. The explanation is grounded on the engine\'s lines and on your stated intention, not invented.'],
    ['Play what you wish you had played', 'Branch off any move and the engine answers. Come back to the game whenever you like.'],
];

export function Home({ onEnter }: { onEnter: (mode?: 'game' | 'postmortem') => void }) {
    const analyze = () => {
        try { sessionStorage.setItem(REVIEW_FOCUS_KEY, '1'); } catch { /* fine */ }
        onEnter('postmortem');
    };

    return (
        <div className="home">
            <header className="home-bar">
                <div className="home-wrap home-bar-inner">
                    <span className="home-wordmark">Zugzwang</span>
                    <nav className="home-bar-nav" aria-label="Account">
                        <Link className="home-bar-link" to="/signin" onClick={() => markEntered()}>Sign in</Link>
                        <Link className="acct-btn acct-btn-primary home-bar-cta" to="/signup" onClick={() => markEntered()}>
                            Create account
                        </Link>
                        {/* Mounting it is also what applies a stored theme to this page. */}
                        <ThemeToggle />
                    </nav>
                </div>
            </header>

            <main>
                {/* ---- Hero ---------------------------------------------------- */}
                <section className="home-hero">
                    <div className="home-wrap home-hero-inner">
                        <div className="home-hero-copy">
                            <p className="home-eyebrow">A chess coach for the games you already played</p>
                            <h1 className="home-title">
                                The chess coach that remembers why you keep making the same mistakes.
                            </h1>
                            <p className="home-subhero">
                                Most chess tools show you the best move. Zugzwang finds the decision your
                                game turned on, asks what you were trying to do, and turns the answer into a
                                lesson you can practise - and meet again if you repeat it.
                            </p>
                            <div className="home-ctas">
                                <button
                                    type="button"
                                    className="acct-btn acct-btn-primary home-cta"
                                    data-testid="home-analyze"
                                    onClick={analyze}
                                >
                                    Analyze a game right now
                                </button>
                                <button
                                    type="button"
                                    className="acct-btn home-cta"
                                    data-testid="home-guest"
                                    onClick={() => onEnter('game')}
                                >
                                    Play as guest
                                </button>
                            </div>
                            <p className="home-fine">
                                No account needed to review a game. An account keeps your reviews, lessons and
                                improvement profile.
                            </p>
                        </div>

                        {/* An illustration, not a screenshot: the board drawn in
                            the app's own square colours, with the two squares of
                            one move marked the way Review marks them. */}
                        <div className="home-hero-art" aria-hidden="true">
                            <div className="home-board">
                                {Array.from({ length: 64 }, (_, i) => {
                                    const file = i % 8;
                                    const rank = Math.floor(i / 8);
                                    const dark = (file + rank) % 2 === 1;
                                    const mark = i === 28 || i === 44;
                                    return <span key={i} className={`home-sq ${dark ? 'is-dark' : ''} ${mark ? 'is-mark' : ''}`} />;
                                })}
                            </div>
                            <div className="home-card-float">
                                <span className="home-card-kicker">Your biggest learning opportunity</span>
                                <span className="home-card-line">The decision that mattered - then what you meant by it.</span>
                            </div>
                        </div>
                    </div>
                </section>

                {/* ---- How it works -------------------------------------------- */}
                <section className="home-section" id="how">
                    <div className="home-wrap">
                        <h2 className="home-h2">How Zugzwang works</h2>
                        <p className="home-section-lede">
                            One loop, from the game you played to the mistake you stop making.
                        </p>
                        <ol className="home-steps">
                            {STEPS.map(([title, body]) => (
                                <li key={title}>
                                    <strong>{title}</strong>
                                    <span>{body}</span>
                                </li>
                            ))}
                        </ol>
                    </div>
                </section>

                {/* ---- One game ------------------------------------------------ */}
                <section className="home-section home-section-alt" id="one-game">
                    <div className="home-wrap">
                        <h2 className="home-h2">What you get from one game</h2>
                        <p className="home-section-lede">
                            A review is useful on its own, before any account and before any history.
                        </p>
                        <div className="home-grid-2">
                            {REVIEW_GIVES.map(([title, body]) => (
                                <div className="home-tile" key={title}>
                                    <strong>{title}</strong>
                                    <span>{body}</span>
                                </div>
                            ))}
                        </div>
                    </div>
                </section>

                {/* ---- Lessons + improvement ----------------------------------- */}
                <section className="home-section" id="lessons">
                    <div className="home-wrap home-split">
                        <div>
                            <h2 className="home-h2">Saved lessons</h2>
                            <p>
                                A review ends in a Correction Card: what you were trying to do, what the
                                position needed, and the theme it belongs to - <em>King safety was
                                conceded</em>, <em>The opponent's threat went unanswered</em>, <em>The attack
                                came too early</em>, and five more.
                            </p>
                            <p>
                                There is one card per theme. Make the same kind of mistake in another game
                                and you meet the same card with the new evidence added, rather than a new
                                lecture. Signed in, cards are saved to your account; as a guest they last
                                for the session.
                            </p>
                        </div>
                        <div>
                            <h2 className="home-h2">My improvement over time</h2>
                            <p>
                                Import your games - recent public games from Chess.com or Lichess by
                                username, or PGN files - and Zugzwang analyses them on the server while
                                you do something else.
                            </p>
                            <p>
                                After ten analysed games it starts naming the mistakes you actually repeat,
                                each with the games it was seen in and a practice position to work on it.
                                Ten is the threshold on purpose: one game is an anecdote.
                            </p>
                        </div>
                    </div>
                </section>

                {/* ---- Plans --------------------------------------------------- */}
                <section className="home-section home-section-alt" id="plans">
                    <div className="home-wrap">
                        <h2 className="home-h2">Guest, account, or Pro</h2>
                        <div className="home-tiers">
                            <div>
                                <strong>Try as a guest</strong>
                                <span>Review a game, play against the coach, practise a position. Lessons and practice last for the session; sign up later and your games come with you.</span>
                            </div>
                            <div>
                                <strong>Create a free account</strong>
                                <span>Your games, Correction Cards, practice results and improvement profile, saved and picked up on any device.</span>
                            </div>
                            <div>
                                <strong>Upgrade to Pro</strong>
                                <span>The full recurring-pattern history in your improvement profile, more saved lessons and more practice re-tests.</span>
                            </div>
                        </div>
                    </div>
                </section>

                {/* ---- Final CTA ----------------------------------------------- */}
                <section className="home-final">
                    <div className="home-wrap home-final-inner">
                        <h2 className="home-final-title">Bring one game. Find the decision that mattered.</h2>
                        <div className="home-ctas">
                            <button type="button" className="acct-btn acct-btn-primary home-cta" onClick={analyze}>
                                Analyze a game right now
                            </button>
                            <Link className="acct-btn home-cta" to="/signup" data-testid="home-signup" onClick={() => markEntered()}>
                                Create account
                            </Link>
                        </div>
                    </div>
                </section>
            </main>

            <footer className="home-footer">
                <div className="home-wrap home-footer-inner">
                    <span className="home-footer-brand">Zugzwang</span>
                    <nav className="home-footer-links" aria-label="Legal">
                        <Link to="/about" onClick={() => markEntered()}>About</Link>
                        <Link to="/terms">Terms</Link>
                        <Link to="/privacy">Privacy</Link>
                        <Link to="/signin" onClick={() => markEntered()}>Sign in</Link>
                    </nav>
                    <span className="home-footer-version" title="The build this page came from">{BUILD_VERSION}</span>
                </div>
            </footer>
        </div>
    );
}
