import { useEffect, useState } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import App, { REVIEW_FOCUS_KEY } from '../App';
import { authService } from '../services/authService';
import { BUILD_VERSION } from '../buildInfo';
import { getCustomPieces } from '../pieceThemes';
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

/**
 * `/?home` - the header's Home link (App.tsx).
 *
 * `/` is the board for anyone who has been in, which is what makes the app's
 * own "back to the board" links work, so a visitor already inside it needs a
 * way to say "show me the public page anyway". The flag does exactly that
 * and nothing else: it is read once, stripped from the URL, and neither
 * `chess-mode` nor the session is touched - so the page they came from is
 * still there when they go back in.
 */
const HOME_FLAG = 'home';

/** `/`: the board for anyone who has been in; the homepage for a stranger. */
export function HomeOrApp() {
    // Read from the ROUTER's location, not from window: the header's Home
    // link is a client-side navigation to this same route, so this component
    // is not remounted and an initializer would never run again. The flag is
    // consumed here and the URL replaced, so a reload is an ordinary visit.
    const location = useLocation();
    const navigate = useNavigate();
    const [forced, setForced] = useState(() => new URLSearchParams(window.location.search).has(HOME_FLAG));
    useEffect(() => {
        const params = new URLSearchParams(location.search);
        if (!params.has(HOME_FLAG)) return;
        setForced(true);
        params.delete(HOME_FLAG);
        const rest = params.toString();
        navigate({ pathname: location.pathname, search: rest ? `?${rest}` : '' }, { replace: true });
    }, [location.search, location.pathname, navigate]);
    const [entered, setEntered] = useState<boolean | null>(() =>
        hasEntered() || window.location.search !== '' ? true : null,
    );
    const [signedIn, setSignedIn] = useState(false);

    useEffect(() => {
        let live = true;
        authService.me()
            .then((me) => { if (!live) return; setSignedIn(me.signed_in); if (entered === null) setEntered(me.signed_in); })
            .catch(() => { if (live && entered === null) setEntered(false); });
        return () => { live = false; };
    }, [entered]);

    // Same reasoning as BetaGate: nothing beats a board that is replaced.
    if (entered === null) return null;
    if (entered && !forced) return <App />;
    return (
        <Home
            // Someone who arrived here from inside the app has somewhere to go
            // back to; a stranger does not.
            backToApp={forced && hasEntered() ? () => setForced(false) : undefined}
            signedIn={signedIn}
            onEnter={(mode) => { markEntered(mode); setForced(false); setEntered(true); }}
        />
    );
}

/**
 * The decorative board in the hero.
 *
 * A real middlegame rather than the starting position - a board with every
 * piece still home reads as a diagram of the rules, not as a game worth
 * reviewing - with e2-e4 marked the way Review marks the move that produced
 * the position. The pieces are the app's own (`pieceThemes`, drawn in this
 * repo), so there is nothing to license and nothing that can drift from the
 * board people use. Ranks 8 down to 1, the way the board is drawn.
 */
const HERO_POSITION = [
    'r..q.rk.',
    'ppp..ppp',
    '...p.n..',
    '..b.p...',
    '..B.P...',
    '...P.N..',
    'PPP..PPP',
    'R..Q.RK.',
].join('');
/** e2 and e4 - the move that is marked. Index 0 is a8. */
const HERO_MARKS = new Set([36, 52]);

function HeroBoard() {
    const pieces = getCustomPieces('stencil');
    return (
        <div className="home-board">
            {Array.from({ length: 64 }, (_, i) => {
                const dark = ((i % 8) + Math.floor(i / 8)) % 2 === 1;
                const letter = HERO_POSITION[i];
                const key = letter === '.' ? null
                    : `${letter === letter.toUpperCase() ? 'w' : 'b'}${letter.toUpperCase()}`;
                const Piece = key ? pieces[key] : null;
                return (
                    <span key={i} className={`home-sq ${dark ? 'is-dark' : ''} ${HERO_MARKS.has(i) ? 'is-mark' : ''}`}>
                        {Piece ? <Piece /> : null}
                    </span>
                );
            })}
        </div>
    );
}

/** The loop, in the order it happens. Each is a thing Review actually does. */
const STEPS: [string, string][] = [
    ['Analyze a game', 'Upload a PGN from Chess.com, Lichess or any app - or review a game you just played here. The engine grades every move.'],
    ['Find the decision that mattered', 'Not the longest list of mistakes: the one move the game turned on, with the evaluation before and after it.'],
    ['Explain what you were trying to do', 'Attack, defend, simplify, or not sure. The coach reads your intention against what the position actually needed.'],
    ['Save the lesson', 'The diagnosis becomes a saved lesson filed under a theme - one per theme, so a repeated mistake meets the same lesson again.'],
    ['Practise the idea', 'A fresh, engine-verified position that tests the same idea, with a hint if you want one. Play it on the board.'],
    ['Build My improvement', 'Bring more games and Zugzwang shows which mistakes recur, with the games as evidence and a practice position for each.'],
];

const REVIEW_GIVES: [string, string][] = [
    ['Every move graded', 'Best, good, inaccuracy, mistake, blunder - the same scale in Play and Review, from the engine, with the centipawn cost.'],
    ['Your biggest learning opportunity', 'The decision that changed the game, named first, with how the evaluation moved and a close-call caveat when the evidence is thin.'],
    ['A coach holding the evidence', 'Ask about any position. The explanation is grounded on the engine\'s lines and on your stated intention, not invented.'],
    ['Play what you wish you had played', 'Branch off any move and the engine answers. Come back to the game whenever you like.'],
];

export function Home({ onEnter, backToApp, signedIn = false }: {
    onEnter: (mode?: 'game' | 'postmortem') => void;
    /** Present only for someone who came here from inside the app. */
    backToApp?: () => void;
    signedIn?: boolean;
}) {
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
                        {backToApp && (
                            <button type="button" className="home-bar-link" onClick={backToApp} data-testid="home-back-to-app">
                                Back to the app
                            </button>
                        )}
                        {!signedIn && (
                            <Link className="home-bar-link" to="/signin" onClick={() => markEntered()}>Sign in</Link>
                        )}
                        {!signedIn && (
                            <Link className="acct-btn acct-btn-primary home-bar-cta" to="/signup" onClick={() => markEntered()}>
                                Create account
                            </Link>
                        )}
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
                            <HeroBoard />
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
                                A review ends in a saved lesson: what you were trying to do, what the
                                position needed, and the theme it belongs to - <em>King safety was
                                conceded</em>, <em>The opponent's threat went unanswered</em>, <em>The attack
                                came too early</em>, and five more.
                            </p>
                            <p>
                                There is one lesson per theme. Make the same kind of mistake in another
                                game and you meet the same lesson with the new evidence added, rather than
                                a new lecture. Signed in, lessons are saved to your account; as a guest
                                they last for the session.
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
                        {/* Cards shaped like cards, so each one does what it
                            looks like it does. They read as three choices and
                            used to be three paragraphs. */}
                        <div className="home-tiers">
                            <button type="button" className="home-tier" onClick={() => onEnter('game')} data-testid="tier-guest">
                                <strong>Try as a guest</strong>
                                <span>Review a game, play against the coach, practise a position. Lessons and practice last for the session; sign up later and your games come with you.</span>
                                <span className="home-tier-go">Start as a guest</span>
                            </button>
                            <Link className="home-tier" to="/signup" onClick={() => markEntered()} data-testid="tier-account">
                                <strong>Create a free account</strong>
                                <span>Your games, saved lessons, practice results and My improvement, saved and picked up on any device.</span>
                                <span className="home-tier-go">Create an account</span>
                            </Link>
                            {/* Pro is bought inside the app, from Settings, where
                                the account that would own it exists. Signed out
                                there is nothing to upgrade, so the card says so
                                rather than opening a paywall that cannot finish. */}
                            <Link
                                className="home-tier"
                                to={signedIn ? '/settings#subscription' : '/signup'}
                                onClick={() => markEntered()}
                                data-testid="tier-pro"
                            >
                                <strong>Upgrade to Pro</strong>
                                <span>The full recurring-pattern history in your improvement profile, more saved lessons and more practice re-tests.</span>
                                <span className="home-tier-go">
                                    {signedIn ? 'Go to your subscription' : 'Create an account to upgrade'}
                                </span>
                            </Link>
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
