import { Link } from 'react-router-dom';
import { SiteFooter } from '../components/SiteFooter';
import { DataRetention } from '../components/DataRetention';
import '../components/AccountMenu.css';
import './account.css';

/**
 * What Zugzwang is, and what it keeps.
 *
 * Two jobs and no marketing. The first half says how the app actually works,
 * because "Stockfish proposes, Gemini decides" is the product and a person
 * using it deserves to know that the explanation they are reading came from a
 * model choosing among moves an engine ranked, not from the engine itself.
 *
 * The second half is the retention explanation, and it is the reason this page
 * exists now rather than later. Accounts are live, so there is durable data
 * belonging to identifiable people, and the app currently implies more
 * permanence than it has: sandbox sessions and Post-Mortem review boards
 * live in memory, while signed-in correction history is durable. Saying so
 * plainly is worth more than the rest of the page.
 *
 * It reuses the settings page's shell and card classes rather than inventing
 * its own. Two stylesheets describing one visual language drift the moment
 * either is edited.
 */
export function About() {
    return (
        <div className="settings-page">
            <div className="settings-shell">
                <header className="settings-head">
                    <p className="auth-brand">Zugzwang</p>
                    <h1 className="settings-h1">About</h1>
                    <Link className="auth-minor" to="/">Back to the board</Link>
                </header>

                <section className="settings-card">
                    <h2 className="settings-card-title">What this is</h2>
                    <p className="settings-card-sub">
                        Zugzwang is a chess coach you play against. A chess engine, Stockfish,
                        ranks the legal moves in a position and hands over a short list of the
                        reasonable ones; a language model, Gemini, picks one from that list and
                        explains why in its own words. That division is the whole idea. The engine
                        keeps the moves honest, and the model does the part an engine cannot: tell
                        you what it was thinking.
                    </p>
                    <p className="settings-card-sub">
                        It means the explanation you read belongs to the move that was actually
                        played, and that the strength you set is a real constraint rather than a
                        style of commentary.
                    </p>
                </section>

                <section className="settings-card">
                    <h2 className="settings-card-title">The three modes</h2>
                    <div className="settings-row">
                        <span className="settings-row-label">
                            Play
                            <span className="settings-row-hint">
                                A real game against the coach, with every half-move graded and a
                                chat about the position while you are in it.
                            </span>
                        </span>
                    </div>
                    <div className="settings-row">
                        <span className="settings-row-label">
                            Learn
                            <span className="settings-row-hint">
                                A sandbox. Describe a position in words, get a legal one, and try
                                ideas out with a coach watching.
                            </span>
                        </span>
                    </div>
                    <div className="settings-row">
                        <span className="settings-row-label">
                            Review
                            <span className="settings-row-hint">
                                Bring a game you played somewhere else as a PGN. Walk it, see every
                                move graded, branch off any position to play what you wish you had
                                played, and ask about it.
                            </span>
                        </span>
                    </div>
                </section>

                <DataRetention />

                <section className="settings-card">
                    <h2 className="settings-card-title">Honest limits</h2>
                    <p className="settings-card-sub">
                        Email addresses are collected but <strong>not verified</strong>, so nothing
                        in the app treats an address as proof of anything.
                    </p>
                    <p className="settings-card-sub">
                        The coach is a language model. It is grounded in the engine's evaluation of
                        the position in front of it, which is what stops it inventing lines, but it
                        is not an authority on chess and it can still be wrong about a plan.
                    </p>
                </section>

                <SiteFooter />
            </div>
        </div>
    );
}
