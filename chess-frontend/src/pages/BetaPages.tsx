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
                The short version: Zugzwang keeps the chess you play and import, the
                corrections you write, and the settings you choose - plus the operational
                records any web service needs to stay up. It has no advertising and no
                tracking across sites, and does not sell or share your data. Zugzwang is
                in beta; this page describes what the app does today and is updated when
                that changes.
            </p>

            <h2>What is collected</h2>
            <ul>
                <li>
                    <strong>Your games.</strong> The moves, the position before and after
                    each one (as FEN), the engine's evaluation, and the coach's explanation.
                </li>
                <li>
                    <strong>Games you import</strong> from Chess.com or Lichess, as PGN, and
                    the analysis of them. Imports use each site's <strong>public</strong> game
                    data by username only. Zugzwang never asks for, stores or uses your
                    Chess.com or Lichess password, token or login.
                </li>
                <li>
                    <strong>Your learning history.</strong> Correction cards - the intention
                    you typed, the diagnosis, the rule and caveat, the engine evidence behind
                    them - practice positions and results, and the evidence rows that make up
                    your improvement profile.
                </li>
                <li>
                    <strong>Your preferences.</strong> Board style, coordinates, whether
                    engine numbers are shown, coaching style, and which panel is open.
                </li>
                <li>
                    <strong>An account, if you make one.</strong> A username, and an email
                    address if you give one. The address is used for password recovery and
                    nothing else. It is <strong>not verified</strong>, so nothing in the
                    app treats it as proof of anything.
                </li>
                <li>
                    <strong>Operational records.</strong> Server logs and error reports,
                    request counts used for rate limiting and abuse prevention, and a
                    first-party product-event log (things like "a correction was saved" with
                    a time and a pseudonymous account key, never the chess content itself).
                    These exist so the service can be kept reliable and debugged.
                </li>
            </ul>

            <h2>Guests and accounts</h2>
            <ul>
                <li>
                    Playing without an account, your games are stored against an anonymous
                    id held in this browser and are deleted after 30 days. Nothing in them
                    identifies you. Create an account within those 30 days and they come
                    with you.
                </li>
                <li>
                    Correction cards you make while signed in, their game evidence and
                    your practice results are saved to your account and count toward
                    your improvement profile. As a guest they are session only.
                </li>
                <li>
                    With an account, your games, imported library, reviewed games,
                    correction cards, profile evidence and preferences are kept until you
                    delete the account. Deleting it removes all of it immediately.
                </li>
                <li>
                    Learner Mode sessions and Post-Mortem review boards live in memory
                    only: they are not attached to your account and do not survive a
                    restart of the server. Imported games and saved corrections do.
                </li>
            </ul>

            <h2>Encryption</h2>
            <p>
                Account-owned chess data that could reconstruct your games and decisions -
                imported PGNs, the position on each finding, and the intention, diagnosis,
                rule, caveat, evidence and practice position of a saved correction - is
                encrypted at rest with a per-account key, wrapped by a server master key,
                where the deployment has that key configured. Deleting the account also
                destroys its data key, so a database backup taken earlier cannot bring the
                content back. Not everything is encrypted: counts, themes, timestamps,
                usernames, source labels and pseudonymous keys stay readable, because they
                are what the product aggregates, and none of them can rebuild a game.
                Guest rows are not encrypted.
            </p>

            <h2>Passwords and sessions</h2>
            <p>
                Passwords are stored as salted hashes and never in a form anyone can read
                back. Sign-in sessions and password-reset links are stored as hashes too,
                so a copy of the database contains no usable session and no working reset
                link. Your beta access code is stored the same way - as a keyed hash, not
                as the code.
            </p>

            <h2>Payments</h2>
            <p>
                Zugzwang Pro subscriptions, once enabled, are handled by RevenueCat with
                payment processing by Stripe. Zugzwang never sees or stores your card
                details. RevenueCat receives a pseudonymous account id (not your username
                or email, unless you enter an email at checkout) and tells Zugzwang only
                whether that account's subscription is active and until when. During the
                beta, checkout may run in a sandbox mode where no real charge is made; the
                checkout page says so when that is the case. Their handling of your data
                is described in the RevenueCat and Stripe privacy policies.
            </p>

            <h2>Cookies</h2>
            <p>
                Two, both set by the server, both unreadable by page scripts, and neither
                used for tracking. One is an anonymous id that remembers which game is
                yours; the other exists only once you sign in and is your session. There
                are no advertising or cross-site cookies. Opening the checkout may set
                cookies belonging to Stripe for the duration of the payment.
            </p>

            <h2>Page views</h2>
            <p>
                Zugzwang is hosted on Vercel, and uses Vercel's Web Analytics to count
                which pages are visited, roughly where from (country), on what kind of
                device and browser, and the page's loading speed. It is cookieless: a
                visit is told apart from the next one by a hash that Vercel derives from
                the request and discards at the end of the day, and your IP address is
                not stored. It does not know your username, your account, or your chess.
            </p>

            <h2>Who else sees your chess</h2>
            <p>
                Positions and moves are sent to Google's Gemini API to generate the
                coaching you read, and are analysed by the Stockfish engine on Zugzwang's
                own server. Nothing sent to Gemini carries your username, your email or
                your account id - it carries the position. Your games are not used to
                train any model and are not shared with other players.
            </p>

            <h2>Beta access records</h2>
            <p>
                Redeeming a code records which code was used, when, by which account or
                anonymous id, and the IP address it was redeemed from. That is kept for
                the duration of the beta so that a leaked invitation can be traced and
                withdrawn, and it is deleted with your account.
            </p>

            <h2>Deleting and exporting</h2>
            <p>
                You can delete your account, and everything it owns, from Settings at any
                time; it takes effect immediately. A self-service export of your data is
                not built yet. Until it is, ask and it will be assembled by hand.
            </p>

            <h2>Contact</h2>
            <ContactAddress />
        </DocShell>
    );
}

export function Terms() {
    return (
        <DocShell title="Terms">
            <p className="beta-body">
                Zugzwang is a chess-learning product in closed beta, run by one person.
                These terms are short because the relationship is.
            </p>

            <h2>Your invitation and your account</h2>
            <p>
                An access code admits one person. Sharing it does not create a second
                account - the first redemption spends it - and codes found to be
                circulating publicly will be disabled. You are responsible for what is
                done with your account: keep your password to yourself, and tell us if
                you think someone else has it.
            </p>

            <h2>What is promised</h2>
            <p>
                Very little, honestly. This is pre-release software: it can be restarted,
                changed, paused or taken offline without notice, features can be removed
                or limited, and the things listed as living in memory on the Privacy page
                can be lost at any time. Keep your own copy of anything that matters to
                you. No improvement in your rating or your results is guaranteed - the
                coaching is an aid to your own work, not a substitute for it.
            </p>

            <h2>The coaching is not authoritative</h2>
            <p>
                The moves come from a chess engine and the explanations from a language
                model reading that engine's evaluation. That grounding is what stops the
                explanations inventing lines, but the coach can still be wrong about a
                plan, and engine evaluations are limited by the search depth and settings
                in use - they are not the last word on a position. Practice and re-test
                positions are generated where a suitable one exists and may not be
                available for every correction. Treat all of it as a strong opponent that
                talks, not as a source of truth.
            </p>

            <h2>Not for cheating</h2>
            <p>
                Zugzwang is for studying your games afterwards and practising positions.
                Using it, or anything it produces, for assistance during a rated or live
                game on any platform is against those platforms' rules and against these
                terms, and will end your access.
            </p>

            <h2>Fair use</h2>
            <p>
                Every move and every question spends real engine time and real API quota.
                Automating the app, scripting requests against it, or using it as a
                general-purpose language model will get an invitation withdrawn. Rate
                limits are in place and they are set well above anything a person playing
                chess will ever reach.
            </p>

            <h2>Your content</h2>
            <p>
                Games you play and games you import remain yours. Only upload or import
                games and text you have the right to use. Nothing in them is used to train
                a model, published, or shown to other players, and deleting your account
                deletes them.
            </p>

            <h2>Subscriptions and payment</h2>
            <p>
                Some features may be offered as Zugzwang Pro, billed monthly, yearly, or as
                a one-time lifetime purchase. Purchases, renewals, cancellations and
                refunds are handled through RevenueCat with payment processing by Stripe,
                under their terms, and you can manage or cancel a subscription from
                Settings at any time; cancelling stops the next renewal and keeps access
                until the paid period ends. During the beta and for demonstrations,
                checkout may run in a sandbox where no real charge is made; the checkout
                says so when that is the case. Prices and what Pro includes may change
                before launch.
            </p>

            <h2>Ending it</h2>
            <p>
                You can delete your account at any time from Settings, and it takes effect
                immediately; what that removes is described on the{' '}
                <Link className="beta-link" to="/privacy">Privacy</Link> page. Access to
                the beta can also be withdrawn - for the reasons above, or simply because
                the beta ends.
            </p>

            <h2>Contact</h2>
            <ContactAddress />

            <p className="beta-doc-meta">
                These terms are provided for beta/demo use and should be reviewed before a
                public commercial launch.
            </p>
        </DocShell>
    );
}
