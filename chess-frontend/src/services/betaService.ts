/**
 * Client for /api/beta/*.
 *
 * WHAT THIS FILE IS AND IS NOT
 * ----------------------------
 * It is how the page finds out whether to draw the application or the landing
 * surface. It is **not** what protects anything, and it is worth being blunt
 * about that in the one file somebody would reach for if they wanted to
 * "improve" the gate: every answer here is advisory. The server refuses
 * unauthorized requests in `beta_gate.py`, before any route runs, on the
 * strength of a signed cookie it minted itself. Someone who edits the state
 * this file returns gets a page that draws a chessboard and an API that
 * answers 403 to every single thing that board tries to do.
 *
 * So there is nothing cached in `localStorage` here, and nothing remembered
 * across a reload. A cached "yes" would be a client-side authorization
 * decision with a longer life than the truth, which is precisely the thing
 * this feature exists to not have.
 */

import { apiJson } from './http';

export interface BetaStatus {
    /** Whether the deployment is gated at all. False re-opens the app. */
    beta_required: boolean;
    /** Whether THIS visitor may use it. */
    has_access: boolean;
    /** Whether they are signed in, so the landing page can tell a locked-out
     *  account from a brand-new visitor and say something true to each. */
    signed_in: boolean;
}

export const betaService = {
    async status(): Promise<BetaStatus> {
        return apiJson<BetaStatus>('/api/beta/status');
    },

    /**
     * Spend a code for whoever this browser is.
     *
     * Note what is NOT sent: any identity, any account id, any claim about who
     * the grant is for. The server takes that from the cookie it signed. There
     * is no parameter here for a caller to tamper with, which is deliberate
     * rather than incidental.
     *
     * Throws with the server's own refusal text, which is one message for
     * every kind of bad code - missing, disabled, expired, used up. The server
     * does not distinguish them and neither should this.
     */
    async redeem(code: string): Promise<{ has_access: boolean; already_had_access: boolean }> {
        return apiJson('/api/beta/redeem', {
            method: 'POST',
            body: JSON.stringify({ code }),
        });
    },
};
