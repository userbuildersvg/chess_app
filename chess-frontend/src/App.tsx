import { useEffect, useState } from 'react';
import { ChessBoard } from './components/ChessBoard';
import type { ReviewHandoff } from './components/ChessBoard';
import { Sandbox } from './components/Sandbox';
import { PostMortem } from './components/PostMortem';
import { ThemeToggle } from './components/ThemeToggle';
import { AccountMenu } from './components/AccountMenu';
import { ImportPrompt } from './components/ImportPrompt';
import { authService } from './services/authService';
import { setSignedIn } from './services/preferences';
import type { GameState } from './types/chess';
import './components/ChessBoard.css';
import './App.css';
import './styles/study.css';
import './styles/mobile.css';

type Mode = 'game' | 'sandbox' | 'postmortem';

// The three permanent experiences: play a game, explore a position, review a
// game you have already played. Labelled with what you do rather than with
// what the mode is called internally - "Review" is the verb; "Post-Mortem" is
// the feature's name and lives in the code, not in a segmented control sized
// for one-word labels.
const MODES: { id: Mode; label: string; hint: string }[] = [
    { id: 'game', label: 'Play', hint: 'Play with coaching' },
    { id: 'sandbox', label: 'Learn', hint: 'Practise a position' },
    { id: 'postmortem', label: 'Review', hint: 'Analyze a finished game' },
];

/**
 * Which mode a refresh comes back to.
 *
 * Every other piece of "where was I" in this app already survives a reload -
 * the rail section, the piece set, the display switches, the real game's board
 * and its chat - and the mode was the one thing that did not, so a refresh
 * while reading a demonstration dropped you back into a game you were not
 * playing. Persisting the mode is what makes the rest of that state reachable.
 */
const MODE_KEY = 'chess-mode';

function initialMode(): Mode {
    // A practice handoff from the profile names its session in the URL;
    // that wins over whatever mode the tab was last in.
    if (new URLSearchParams(window.location.search).has('practice')) {
        return 'sandbox';
    }
    try {
        const stored = localStorage.getItem(MODE_KEY);
        if (MODES.some(m => m.id === stored)) {
            return stored as Mode;
        }
    } catch {
        // localStorage unavailable (private browsing) - open on Review.
    }
    // Review first: the product's front door is "analyze a game you already
    // played" (UX experiment), so a stranger lands there rather than on Play.
    return 'postmortem';
}

function App() {
    const [, setGameState] = useState<GameState | null>(null);
    const [mode, setMode] = useState<Mode>(initialMode);

    // Whether preferences should also be written to the account. Asked once,
    // here, because `preferences.ts` is read during render and a fetch there
    // would be a bug rather than a feature.
    useEffect(() => {
        authService.me()
            .then((me) => setSignedIn(me.signed_in))
            .catch(() => setSignedIn(false));
    }, []);
    // Whether Learner Mode has ever been opened. Mounting Sandbox eagerly
    // would open a server-side sandbox session on every page load, including
    // for people who never leave the game - and sessions are capped at 50 with
    // an hour's idle sweep, which matters because this is meant to go public.
    // So it mounts on first use and then stays mounted for the rest of the
    // visit, which is what actually preserves the session.
    //
    // Seeded from the restored mode: someone who reloads while in Learner Mode
    // is opening it, so the lazy mount has already been earned. Someone who
    // reloads in the game still costs the server nothing.
    const [sandboxOpened, setSandboxOpened] = useState(() => initialMode() === 'sandbox');
    // Post-Mortem mounts lazily for the same reason Learner Mode does, and the
    // cost it defers is larger: a review holds a whole game tree and its
    // analysis server-side, against a store capped at 20. A visitor who never
    // opens Review never occupies one of those slots. Once opened it stays
    // mounted, which is what preserves the imported game while you glance at
    // the board next door.
    const [postmortemOpened, setPostmortemOpened] = useState(() => initialMode() === 'postmortem');
    // "Review this game" (CLAUDE.md §32). Play opens the review on the server
    // and hands its id here; Review picks it up through a prop rather than
    // through localStorage, because Review may already be mounted with its
    // own game open and would never re-read a key it only reads on mount.
    // The nonce makes reviewing a second game with the same id (impossible)
    // or two games in a row (ordinary) both re-fire the effect.
    const [reviewHandoff, setReviewHandoff] = useState<(ReviewHandoff & { nonce: number }) | null>(null);

    useEffect(() => {
        try {
            localStorage.setItem(MODE_KEY, mode);
        } catch {
            // localStorage unavailable - the mode just won't outlive the tab.
        }
    }, [mode]);

    const changeMode = (next: Mode) => {
        if (next === 'sandbox') {
            setSandboxOpened(true);
        }
        if (next === 'postmortem') {
            setPostmortemOpened(true);
        }
        setMode(next);
    };
    const reviewPlayGame = (handoff: ReviewHandoff) => {
        setReviewHandoff({ ...handoff, nonce: Date.now() });
        changeMode('postmortem');
    };

    // ChessBoard stays mounted while the sandbox is open, hidden rather than
    // unmounted. The real game lives in module-level state on the server, but
    // the board's *client* state (chat transcript, review data, which rail
    // section you had open) is local - unmounting would throw all of it away
    // every time someone glanced at the sandbox and came back.
    return (
        <div className="app">
            <header className="app-header">
                <div className="app-header-inner">
                    <span className="app-wordmark">Zugzwang</span>

                    {/* A segmented control rather than the previous single
                        button. That button was labelled with its DESTINATION
                        ("Learner Mode" / "Back to game"), so the mode you were
                        actually in never appeared on screen - you had to infer
                        it from the label of the control that would leave it.
                        Both modes are now visible and the active one is
                        marked, which is also what makes the switch reversible
                        at a glance. */}
                    <nav className="app-modes" aria-label="Mode">
                        {MODES.map(item => (
                            <button
                                key={item.id}
                                type="button"
                                className="app-mode"
                                aria-current={mode === item.id ? 'page' : undefined}
                                title={item.hint}
                                onClick={() => changeMode(item.id)}
                            >
                                {item.label}
                            </button>
                        ))}
                    </nav>

                    <div className="app-header-actions">
                        {/* The account controls sit before the theme toggle
                            because who you are outranks how the page looks.
                            While accounts are switched off both entry points
                            open a notice explaining guest mode - see
                            AccountMenu.tsx. */}
                        <AccountMenu />
                        <ThemeToggle />
                    </div>
                </div>
            </header>
            <main className="app-main">
                <div hidden={mode !== 'game'} className="app-mode-pane">
                    <ChessBoard onGameStateChange={setGameState} onReviewGame={reviewPlayGame} />
                </div>
                {/* Hidden rather than unmounted, for the same reason the game
                    pane is. Unmounting Sandbox threw away its whole session -
                    the scenario, the move tree and the coach conversation -
                    and opened a brand-new one on the way back, so glancing at
                    the game cost you the position you were studying. */}
                {sandboxOpened && (
                    <div hidden={mode !== 'sandbox'} className="app-mode-pane app-mode-pane-fill">
                        <Sandbox />
                    </div>
                )}
                {/* Hidden rather than unmounted, exactly as the other two panes
                    are. Unmounting would throw away the imported game, every
                    branch explored off it and the review conversation - so
                    glancing at the board next door would cost you the game you
                    brought. */}
                {postmortemOpened && (
                    <div hidden={mode !== 'postmortem'} className="app-mode-pane app-mode-pane-fill">
                        <PostMortem
                            handoff={reviewHandoff}
                            onBackToPlay={() => changeMode('game')}
                        />
                    </div>
                )}
            </main>
            {/* Once, after sign-up: import recent Chess.com / Lichess games.
                Decides for itself whether to show (signed in, not yet
                answered) and is skippable. See ImportPrompt.tsx. */}
            <ImportPrompt />
        </div>
    );
}

export default App;
