import { useCallback, useEffect, useRef, useState } from 'react';
import { authService, type AuthConfig, type WhoAmI } from '../services/authService';
import { billingService } from '../services/billingService';
import { BUILD_VERSION } from '../buildInfo';
import './AccountMenu.css';
import { Link } from 'react-router-dom';

/**
 * The account UI: real, finished, and currently unavailable.
 *
 * Built the way it will actually ship - a Guest indicator, a Sign in entry
 * point, a Create account entry point, and a real form behind them - so that
 * enabling accounts is deleting a guard rather than building an interface.
 *
 * WHAT "UNAVAILABLE" MEANS HERE
 * -----------------------------
 * The buttons are NOT hidden and NOT disabled. Both are worse than they look:
 * a hidden control leaves the user with no idea accounts are coming, and a
 * greyed-out one gives them nothing to click and no explanation. Instead the
 * entry points work, and what opens is a plain notice saying accounts are not
 * available yet and that they are playing as a guest.
 *
 * The wording comes from the SERVER (`unavailable_message`), not from this
 * file, so what a user is told cannot drift from what the backend actually
 * does. And the backend refuses the routes regardless of what this component
 * renders - hiding a button in React would leave /api/auth/signup open to
 * anyone who opened devtools.
 *
 * When ACCOUNTS_ENABLED flips on the backend, `config.accounts_enabled`
 * becomes true and the same entry points open the real form instead. Nothing
 * in this file needs editing for that to happen.
 */

type Panel = 'closed' | 'notice';

export function AccountMenu() {
    const [config, setConfig] = useState<AuthConfig | null>(null);
    const [who, setWho] = useState<WhoAmI | null>(null);
    const [panel, setPanel] = useState<Panel>('closed');
    const [busy, setBusy] = useState(false);
    const dialogRef = useRef<HTMLDivElement | null>(null);
    const openerRef = useRef<HTMLButtonElement | null>(null);

    // "Upgrade" is shown only when the server has positively said Free; a
    // failed or unverified check hides it rather than nagging a Pro user.
    const [showUpgrade, setShowUpgrade] = useState(false);

    const refresh = useCallback(async () => {
        try {
            const [cfg, me] = await Promise.all([authService.config(), authService.me()]);
            setConfig(cfg);
            setWho(me);
            if (me.signed_in) {
                billingService.status()
                    .then((b) => setShowUpgrade(b.verified && !b.pro))
                    .catch(() => setShowUpgrade(false));
            } else {
                setShowUpgrade(false);
            }
        } catch {
            // The header must never be the thing that breaks the page. If the
            // account endpoints cannot be reached at all, fall back to the
            // truthful assumption: this is a guest, and accounts are off.
            setConfig({ accounts_enabled: false, guest_mode: true, google: false, unavailable_message: null, email_available: false });
            setWho({ signed_in: false, guest: true, username: null, accounts_enabled: false });
        }
    }, []);

    useEffect(() => {
        void refresh();
    }, [refresh]);

    const close = useCallback(() => {
        setPanel('closed');
        // Focus goes back to the control that opened the dialog. Without it,
        // closing drops the caret to the top of the document, which is
        // disorienting with a mouse and genuinely lost with a keyboard.
        openerRef.current?.focus();
    }, []);

    useEffect(() => {
        if (panel === 'closed') return;

        /** Everything inside the dialog a keyboard can reach, in document order. */
        const focusables = () => Array.from(
            dialogRef.current?.querySelectorAll<HTMLElement>(
                'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
            ) ?? [],
        ).filter(el => el.offsetParent !== null || getComputedStyle(el).position === 'fixed');

        const onKey = (e: KeyboardEvent) => {
            if (e.key === 'Escape') {
                close();
                return;
            }
            // Keep Tab inside the dialog.
            //
            // This element already says `aria-modal="true"`, which tells a
            // screen reader that everything behind it is inert - but the
            // browser does not enforce that for the keyboard, and it was not
            // enforced here either: one Tab from the open dialog landed on the
            // theme toggle in the header behind it, and from there the whole
            // page was reachable while the dialog was still up. A dialog that
            // claims to be modal and then lets focus walk out behind it is
            // worse than one that never claimed it, because the announcement
            // and the behaviour disagree.
            if (e.key !== 'Tab') return;
            const items = focusables();
            if (items.length === 0) return;
            const first = items[0];
            const last = items[items.length - 1];
            const active = document.activeElement as HTMLElement | null;
            const inside = !!active && dialogRef.current?.contains(active);
            if (!inside) {
                // Focus has already escaped (or never arrived) - bring it back
                // rather than letting Tab carry on through the page behind.
                e.preventDefault();
                (e.shiftKey ? last : first).focus();
                return;
            }
            if (e.shiftKey && active === first) {
                e.preventDefault();
                last.focus();
            } else if (!e.shiftKey && active === last) {
                e.preventDefault();
                first.focus();
            }
        };
        window.addEventListener('keydown', onKey);
        // Move focus into the dialog on open, for the same reason.
        const first = dialogRef.current?.querySelector<HTMLElement>(
            'input, button:not([disabled])',
        );
        first?.focus();
        return () => window.removeEventListener('keydown', onKey);
    }, [panel, close]);

    /** Sign in and Create account both land here while accounts are off. */
    /** The only panel left. Signing in is a page now, not a dropdown. */
    const openNotice = () => setPanel('notice');


    const signOut = async () => {
        setBusy(true);
        try {
            await authService.logout();
            await refresh();
        } finally {
            setBusy(false);
        }
    };

    const signedIn = who?.signed_in === true;

    /** Accounts are off, so both entry points explain rather than navigate. */
    const accountsOff = config !== null && !config.accounts_enabled;

    return (
        <div className="acct">
            {/* The app shell has no footer - it is height-fitted and CLAUDE.md
                traps 8 and 11 both record an element added to that layout
                pushing the board out of its own column - so About lives here,
                which is the only chrome a guest who never signs in ever sees.
                A link rather than a button, and the build id rides in the
                title so it is available without spending header width on it. */}
            <Link
                className="acct-about"
                to="/about"
                title={`About Zugzwang - build ${BUILD_VERSION}`}
            >
                About
            </Link>
            {signedIn ? (
                <>
                    {/* The username is the way in to the account area. One
                        control rather than a chip plus a button: everything a
                        signed-in person might want - email, password, board
                        settings, sign out, deletion - is on the other side of
                        it, and duplicating one of those five in the header
                        would beg the question of why the other four are not
                        there too. */}
                    <Link className="acct-btn acct-btn-quiet acct-user" to="/settings" title="Account settings">
                        {who?.username}
                    </Link>
                    {showUpgrade && (
                        <Link className="acct-btn acct-btn-quiet" to="/settings#subscription" title="Zugzwang Pro" data-testid="header-upgrade">
                            Upgrade
                        </Link>
                    )}
                    <button
                        type="button"
                        className="acct-btn"
                        onClick={() => void signOut()}
                        disabled={busy}
                    >
                        Sign out
                    </button>
                </>
            ) : (
                <>
                    {/* Says what you ARE, not what is missing. A guest is a
                        real, working state - the whole app works - so the chip
                        is informational rather than a prompt to fix something.
                        The title carries the one fact a person cannot work out
                        for themselves: their history is retained, and claiming
                        it is what signing up does. */}
                    <span
                        className="acct-who acct-who-guest"
                        title="Playing as a guest - your games are kept in this browser for 30 days, and become yours if you create an account"
                    >
                        Guest
                    </span>
                    {accountsOff ? (
                        <>
                            <button
                                type="button"
                                className="acct-btn"
                                ref={openerRef}
                                onClick={openNotice}
                            >
                                Sign in
                            </button>
                            <button
                                type="button"
                                className="acct-btn acct-btn-quiet"
                                onClick={openNotice}
                            >
                                Create account
                            </button>
                        </>
                    ) : (
                        <>
                            <Link className="acct-btn" to="/signin">Sign in</Link>
                            <Link className="acct-btn acct-btn-quiet" to="/signup">Create account</Link>
                        </>
                    )}
                </>
            )}

            {panel !== 'closed' && (
                <div className="acct-overlay" role="presentation" onClick={close}>
                    <div
                        className="acct-dialog"
                        role="dialog"
                        aria-modal="true"
                        aria-labelledby="acct-title"
                        ref={dialogRef}
                        onClick={e => e.stopPropagation()}
                    >
                                <h2 className="acct-title" id="acct-title">
                                    Accounts aren't available yet
                                </h2>
                                <p className="acct-body">
                                    {config?.unavailable_message ??
                                        "Accounts aren't available yet - you're playing as a guest."}
                                </p>
                                <p className="acct-body acct-body-dim">
                                    Everything works as a guest: play the coach, use Learner
                                    Mode, change the opponent level, ask about the position. Your
                                    games are recorded, but they are tied to this browser -
                                    clear your cookies or open the app elsewhere and you start
                                    from nothing. When accounts arrive, signing up will bring
                                    the history you built here with you.
                                </p>
                                <div className="acct-actions">
                                    <button type="button" className="acct-btn acct-btn-primary" onClick={close}>
                                        Keep playing as a guest
                                    </button>
                                </div>
                    </div>
                </div>
            )}
        </div>
    );
}
