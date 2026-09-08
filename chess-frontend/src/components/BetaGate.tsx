import { useEffect, useState, type ReactNode } from 'react';
import { useLocation } from 'react-router-dom';
import { betaService, type BetaStatus } from '../services/betaService';
import { BetaLanding } from '../pages/BetaLanding';

/**
 * What the browser draws while the server is refusing everything.
 *
 * READ THIS BEFORE CHANGING IT
 * ----------------------------
 * This component decides what is on screen. It does not decide what is
 * allowed. `beta_gate.py` refuses every guarded API request from an identity
 * with no redeemed code, before any route runs, on the strength of a cookie
 * the server signed - so a person who deletes this component from a running
 * page, flips its state in React DevTools, or blocks the status request
 * entirely gets a chessboard that cannot make a move, a Review that cannot
 * import, and a Profile that cannot load.
 *
 * The consequence for anyone editing this file: getting it wrong is a UI bug,
 * not a security hole. Do not add a "trusted" flag to `localStorage` to make
 * the first paint faster, and do not cache the answer across a reload. The
 * server is asked once per page load, which is cheap, and the answer is never
 * older than the page.
 *
 * WHY SOME ROUTES ARE STILL REACHABLE WHEN LOCKED OUT
 * ---------------------------------------------------
 * A returning tester's access lives on their account, not on this browser -
 * so on a new device they have no grant until they sign in, and `/signin` has
 * to work or their invitation is unusable. Password recovery follows the same
 * argument. The information pages are open because a page explaining what the
 * product does and what it keeps is worth nothing behind the door it explains.
 *
 * `/signup` is deliberately NOT on the list. Redeeming comes first, and an
 * account created before one would be an account with no access - a state the
 * product would then have to explain.
 */
const OPEN_ROUTES = [
    '/signin',
    '/forgot-password',
    '/reset-password',
    '/about',
    '/privacy',
    '/terms',
    '/contact',
    '/request-access',
];

export function BetaGate({ children }: { children: ReactNode }) {
    const [status, setStatus] = useState<BetaStatus | null>(null);
    const location = useLocation();

    useEffect(() => {
        let live = true;
        betaService.status()
            .then((s) => { if (live) setStatus(s); })
            // A failed status call is treated as no access, matching the server,
            // which fails closed for the same reason. Drawing the app because
            // the question could not be asked would produce a board where every
            // action answers 403, which is a worse experience than the landing
            // page and a misleading one.
            .catch(() => {
                if (live) setStatus({ beta_required: true, has_access: false, signed_in: false });
            });
        return () => { live = false; };
    }, []);

    // Nothing at all until the answer arrives. Deliberately not a spinner:
    // this resolves in one request against the same origin, and a spinner that
    // appears for 80ms on every load is more visible than the wait it
    // describes. Deliberately not the app either - painting the board and then
    // replacing it with a locked door is the worst of both.
    if (status === null) {
        return null;
    }

    if (!status.beta_required || status.has_access) {
        return <>{children}</>;
    }

    if (OPEN_ROUTES.includes(location.pathname)) {
        return <>{children}</>;
    }

    return (
        <BetaLanding
            signedIn={status.signed_in}
            // A full reload rather than a state update. Access changing does
            // not alter one component's props - it changes what every service
            // in the app is permitted to fetch, and several of them read their
            // starting state during the first render. The sign-in page reloads
            // after hydrating preferences for the same reason.
            onGranted={() => { window.location.href = '/'; }}
        />
    );
}
