import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { REVIEW_FOCUS_KEY } from '../App';
import { SAMPLE_KEY } from '../sampleGame';
import { ThemeToggle } from '../components/ThemeToggle';
import '../components/AccountMenu.css';
import './home.css';

/**
 * `/judge` - the public path for hackathon judges: one bundled game through
 * the core loop, no account and no invitation.
 *
 * It opens nothing new. Both buttons do what the homepage's "Analyze a game
 * right now" does - Review, focused, for this tab - and "Start sample review"
 * also leaves SAMPLE_KEY for PostMortem to import the bundled PGN on mount.
 * The APIs the loop calls are the guest demo routes beta_gate.py already
 * names; this page makes no request of its own, so loading it costs nothing.
 */
const STEPS = [
    'Start the sample review - the engine grades every move (no AI call).',
    'Open the Report and select "Your biggest learning opportunity".',
    'Say what you were trying to do, then generate the saved lesson (one AI call).',
    'Practise the idea on the main board, then End practice to return to the review.',
];

export function Judge() {
    const navigate = useNavigate();
    const [going, setGoing] = useState(false);

    const start = (sample: boolean) => {
        if (going) return;
        setGoing(true);
        try {
            localStorage.setItem('chess-mode', 'postmortem');
            sessionStorage.setItem(REVIEW_FOCUS_KEY, '1');
            if (sample) sessionStorage.setItem(SAMPLE_KEY, '1');
        } catch {
            // No storage: Review is still the default mode; the sample just
            // is not opened, and the dropzone is there instead.
        }
        navigate('/');
    };

    return (
        <div className="home">
            <header className="home-bar">
                <div className="home-wrap home-bar-inner">
                    <Link className="home-wordmark" to="/?home">Zugzwang</Link>
                    <nav className="home-bar-nav" aria-label="Theme"><ThemeToggle /></nav>
                </div>
            </header>
            <main>
                <section className="home-section">
                    <div className="home-wrap">
                        <p className="home-eyebrow">Judge demo path</p>
                        <h1 className="home-h2">Try the core loop in 2 minutes</h1>
                        <ul className="home-section-lede">
                            <li>This uses a bundled sample game - Morphy's Opera Game, Paris 1858.</li>
                            <li>No account and no invitation are required.</li>
                            <li>It walks the core product loop: review, the key decision, a saved lesson, practice.</li>
                            <li>Pro (RevenueCat) is optional and not needed to evaluate this demo.</li>
                        </ul>
                        <ol className="home-steps">
                            {STEPS.map((s) => <li key={s}><span>{s}</span></li>)}
                        </ol>
                        <div className="home-ctas">
                            <button type="button" className="acct-btn acct-btn-primary home-cta" data-testid="judge-sample"
                                disabled={going} onClick={() => start(true)}>
                                Start sample review
                            </button>
                            <button type="button" className="acct-btn home-cta" data-testid="judge-own"
                                disabled={going} onClick={() => start(false)}>
                                Use my own PGN
                            </button>
                        </div>
                    </div>
                </section>
            </main>
        </div>
    );
}
