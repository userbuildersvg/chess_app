import { Link } from 'react-router-dom';
import '../components/AccountMenu.css';
import './beta.css';

/**
 * The four pages behind the landing page's footer: request access, contact,
 * privacy and terms.
 *
 * WHY THEY EXIST AS PAGES
 * -----------------------
 * The landing page offers them, so they have to be real. A footer link that
 * goes nowhere on the one screen every uninvited visitor sees is the single
 * most visible broken thing the product could have, and "we will write those
 * later" is how it stays broken.
 *
 * They are reachable while locked out (`BetaGate.OPEN_ROUTES`). A privacy
 * policy behind the door it describes is not a privacy policy.
 *
 * WHAT IS IN THEM
 * ---------------
 * Only claims the code actually enforces. The retention section restates what
 * `DataRetention.tsx` says, and that component's docstring lists which line of
 * the backend makes each sentence true - `GUEST_RETENTION_DAYS`, the sweep,
 * `delete_user`'s cascade, and the stores that are deliberately in memory. If
 * one of those changes, this page is wrong and so is that component; they are
 * the two places to fix, and both are named here so neither is missed.
 *
 * There is no invented legal boilerplate. Zugzwang is a closed beta run by one
 * person, and a page of borrowed corporate clauses about arbitration venues
 * and third-party sub-processors would be less true than the short version.
 */

/**
 * Where a person writes to.
 *
 * Configured, not hard-coded: this deployment's address is not something the
 * frontend can know, and inventing one produces a page that looks helpful and
 * silently swallows every message sent to it. Set `VITE_CONTACT_EMAIL` in the
 * frontend's environment (Vercel, or `.env.local` for the dev server).
 */
const CONTACT_EMAIL: string | undefined = import.meta.env.VITE_CONTACT_EMAIL;

function DocShell({ title, children }: { title: string; children: React.ReactNode }) {
    return (
        <div className="beta-page">
            <main className="beta-card beta-doc">
                <p className="beta-wordmark">Zugzwang</p>
                <p className="beta-eyebrow">Private Beta</p>
                <h1 className="beta-title">{title}</h1>
                {children}
                <p className="beta-doc-meta">
                    <Link className="beta-link" to="/">Back to the access page</Link>
                </p>
            </main>
        </div>
    );
}

/** The address, or an honest admission that this deployment has not set one. */
function ContactAddress() {
    if (!CONTACT_EMAIL) {
        return (
            <p>
                This deployment has not been given a contact address
                (<code>VITE_CONTACT_EMAIL</code> is unset), so there is no address to
                show you here. If you were invited to this beta, reply to whoever sent
                you your access code.
            </p>
        );
    }
    return (
        <p>
            Write to <a className="beta-link" href={`mailto:${CONTACT_EMAIL}`}>{CONTACT_EMAIL}</a>.
            It is read by the person who builds this, so a reply may take a day or two
            but it will be a real one.
        </p>
    );
}

export function RequestAccess() {
    return (
        <DocShell title="Request beta access">
            <p className="beta-body">
                The beta is small on purpose. It is limited to a number of players small
                enough that every piece of feedback can actually be read and acted on,
                which stops being true somewhere well below a thousand people.
            </p>

            <h2>What to say</h2>
            <p>
                There is no form. Send a short message with your rough rating or level,
                where you normally play, and what you are trying to get better at. That
                last one matters most: Zugzwang is a coach, and the invitations go to
                players whose weaknesses the current version can actually say something
                useful about.
            </p>

            <ContactAddress />

            <h2>What you get</h2>
            <p>
                A code in the form <code>ZG-BETA-XXXX-XXXX</code>. It admits one person,
                it is entered once, and after that your access belongs to your account
                rather than to the browser you first used.
            </p>

            <h2>What it costs</h2>
            <p>
                Nothing. The beta is free and there is no payment in the product at all
                yet. If that changes, it will change for new accounts and it will be said
                plainly before it does, not applied quietly to people who were here first.
            </p>
        </DocShell>
    );
}

export function Contact() {
    return (
        <DocShell title="Contact">
            <ContactAddress />

            <h2>Reporting something broken</h2>
            <p>
                Useful bug reports for this app have three things in them: what you were
                doing, what happened, and the build id from the bottom of any account
                page. The last one costs you a glance and saves a day, because the
                frontend and the backend deploy separately and can briefly disagree about
                which version they are.
            </p>

            <h2>Deleting your account</h2>
            <p>
                You do not need to write to anybody for this. Settings has a delete
                button, it removes your games, your imported library and your preferences
                immediately, and it does not go through a person.
            </p>
        </DocShell>
    );
}

export function Privacy() {
    return (
        <DocShell title="Privacy">
            <p className="beta-body">
                The short version: Zugzwang keeps the chess you play and the settings you
                choose, and nothing else about you. It has no analytics, no advertising
                and no tracking of any kind, and it does not sell or share anything with
                anybody.
            </p>

            <h2>What is collected</h2>
            <ul>
                <li>
                    <strong>Your games.</strong> The moves, the position before and after
                    each one, the engine's evaluation, and the coach's explanation.
                </li>
                <li>
                    <strong>Your preferences.</strong> Board style, coordinates, whether
                    engine numbers are shown, and which panel is open.
                </li>
                <li>
                    <strong>An account, if you make one.</strong> A username, and an email
                    address if you give one. The address is used for password recovery and
                    nothing else. It is <strong>not verified</strong>, so nothing in the
                    app treats it as proof of anything.
                </li>
                <li>
                    <strong>Games you import</strong> into your improvement profile, and
                    the analysis of them.
                </li>
            </ul>

            <h2>How long it is kept</h2>
            <ul>
                <li>
                    Playing without an account, your games are stored against an anonymous
                    id held in this browser and are deleted after 30 days. Nothing in them
                    identifies you. Create an account within those 30 days and they come
                    with you.
                </li>
                <li>
                    With an account, your games, your imported library and your preferences
                    are kept until you delete the account. Deleting it removes all of it
                    immediately.
                </li>
                <li>
                    Learner Mode sessions, Post-Mortem reviews, and the corrections and
                    practice from the learning loop live in memory only. They are not
                    attached to your account and do not survive a restart of the server.
                </li>
            </ul>

            <h2>Passwords and sessions</h2>
            <p>
                Passwords are stored as salted hashes and never in a form anyone can read
                back. Sign-in sessions and password-reset links are stored as hashes too,
                so a copy of the database contains no usable session and no working reset
                link. Your beta access code is stored the same way - as a keyed hash, not
                as the code.
            </p>

            <h2>Cookies</h2>
            <p>
                Two, both set by the server, both unreadable by page scripts, and neither
                used for tracking. One is an anonymous id that remembers which game is
                yours; the other exists only once you sign in and is your session. There
                are no third-party cookies, because there are no third-party scripts.
            </p>

            <h2>Who else sees your chess</h2>
            <p>
                Positions and moves are sent to Google's Gemini API to generate the
                coaching you read, and are analysed locally by the Stockfish engine.
                Nothing sent to Gemini carries your username, your email or your account
                id - it carries the position. Your games are not used to train any model
                and are not shared with other players.
            </p>

            <h2>Beta access records</h2>
            <p>
                Redeeming a code records which code was used, when, by which account or
                anonymous id, and the IP address it was redeemed from. That is kept for
                the duration of the beta so that a leaked invitation can be traced and
                withdrawn, and it is deleted with your account.
            </p>
        </DocShell>
    );
}

export function Terms() {
    return (
        <DocShell title="Terms">
            <p className="beta-body">
                Zugzwang is a closed beta, free to use, run by one person. These terms are
                short because the relationship is.
            </p>

            <h2>Your invitation</h2>
            <p>
                An access code admits one person. Sharing it does not create a second
                account - the first redemption spends it - and codes found to be
                circulating publicly will be disabled so they cannot be redeemed again.
                Existing access can be withdrawn separately.
            </p>

            <h2>What is promised</h2>
            <p>
                Very little, honestly. This is pre-release software: it can be restarted,
                changed or taken offline without notice, features can be removed, and the
                things listed as living in memory on the Privacy page can be lost at any
                time. Keep your own copy of anything that matters to you.
            </p>

            <h2>The coaching is not authoritative</h2>
            <p>
                The moves come from a chess engine and the explanations from a language
                model reading that engine's evaluation. That grounding is what stops the
                explanations inventing lines, but the coach is not an authority and it can
                be wrong about a plan. Treat it as a strong opponent that talks, not as a
                source of truth.
            </p>

            <h2>Fair use</h2>
            <p>
                Every move and every question spends real engine time and real API quota
                on one shared key. Automating the app, scripting requests against it, or
                using it as a general-purpose language model is the one thing that will
                get an invitation withdrawn. Rate limits are in place and they are set
                well above anything a person playing chess will ever reach.
            </p>

            <h2>Your content</h2>
            <p>
                Games you play and games you import remain yours. Nothing in them is used
                to train a model, published, or shown to other players, and deleting your
                account deletes them.
            </p>

            <h2>Ending it</h2>
            <p>
                You can delete your account at any time from Settings, and it takes effect
                immediately. Access to the beta can also be withdrawn - for the fair-use
                reason above, or simply because the beta ends.
            </p>
        </DocShell>
    );
}
