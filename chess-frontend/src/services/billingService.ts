/**
 * Subscriptions: RevenueCat's Web SDK in the browser, the server's word on
 * whether the account is Pro.
 *
 * Division of labour, deliberately:
 *   - `status()` asks OUR backend (`billing_api.py`). That is the answer any
 *     feature limit trusts, and it also hands back the App User ID the SDK
 *     must be configured with, so both halves agree on who is buying.
 *   - The SDK does what only it can: draw the dashboard-built paywall and
 *     run the checkout. Its `CustomerInfo` is used for the management URL;
 *     the server is re-asked with `fresh=1` straight after a purchase to
 *     make it official.
 *
 * Env (all PUBLIC - they ship in the bundle; the secret key never leaves
 * Render):
 *   VITE_REVENUECAT_ENABLED         "false" switches the SDK off (default on)
 *   VITE_REVENUECAT_PUBLIC_API_KEY  Web Billing key: rcb_sb_… sandbox, rcb_… live
 *   VITE_REVENUECAT_ENTITLEMENT_ID  default zugzwang_pro
 *   VITE_REVENUECAT_OFFERING_ID     default "default"
 *
 * Web has no Customer Center; RevenueCat exposes `managementURL` (the Stripe
 * customer portal) instead, which is what "Manage subscription" opens.
 */

import { ErrorCode, Purchases, PurchasesError, type CustomerInfo } from '@revenuecat/purchases-js';
import { apiJson } from './http';

const env = import.meta.env;
const PUBLIC_KEY: string | undefined = env.VITE_REVENUECAT_PUBLIC_API_KEY;
const ENABLED = env.VITE_REVENUECAT_ENABLED !== 'false';
export const ENTITLEMENT: string = env.VITE_REVENUECAT_ENTITLEMENT_ID || 'zugzwang_pro';
export const OFFERING_ID: string = env.VITE_REVENUECAT_OFFERING_ID || 'default';

export interface PlanLimits {
    monthly_reviews: number;
    monthly_corrections: number;
    profile_themes_visible: number | 'all';
    practice_retests: number;
}

export interface BillingStatus {
    configured: boolean;
    entitlement: string;
    signed_in: boolean;
    app_user_id: string | null;
    pro: boolean;
    plan: 'free' | 'pro';
    /** What each plan includes. "preview" because nothing counts usage yet:
     *  only profile_themes_visible is enforced (server-side, on /api/profile).
     *  Render as "includes", never as "remaining". */
    limits_preview: { free: PlanLimits; pro: PlanLimits };
    /** False when the server could not reach RevenueCat - "unknown", not "no". */
    verified: boolean;
    /** Why an answer is unverified: `unauthorized`, `no_subscriber`, `timeout`,
     *  `rate_limited`, `provider_error`, `unreachable`, `not_configured`.
     *  Null when verified. A code, never a secret - safe to show and to log. */
    reason?: string | null;
    expires_at: string | null;
    product: string | null;
}

/** One sentence of what a plan includes, from the server's numbers. */
export const limitLines = (l: PlanLimits) =>
    `${l.monthly_reviews} game reviews a month, ${l.monthly_corrections} saved lessons, `
    + `${l.profile_themes_visible === 'all' ? 'full recurring-pattern history' : 'your strongest recurring pattern'}, `
    + `${l.practice_retests} practice re-tests.`;

/** Thrown when the paywall cannot open for a reason only the dashboard fixes. */
export class BillingSetupError extends Error {}

export const sdkAvailable = () => ENABLED && Boolean(PUBLIC_KEY);

function sdk(appUserId: string): Purchases {
    if (!sdkAvailable()) throw new BillingSetupError('Purchases are not enabled in this build.');
    if (!Purchases.isConfigured()) return Purchases.configure({ apiKey: PUBLIC_KEY!, appUserId });
    const p = Purchases.getSharedInstance();
    // Sign-out then sign-in as someone else on the same tab: follow the account.
    if (p.getAppUserId() !== appUserId) void p.changeUser(appUserId);
    return p;
}

export const isPro = (info: CustomerInfo) => ENTITLEMENT in info.entitlements.active;

export const billingService = {
    // `no-store`: a fresh check is polled after a purchase, and the browser
    // serving its own copy of the previous answer would defeat the point.
    status: (fresh = false) =>
        apiJson<BillingStatus>(`/api/billing/status${fresh ? '?fresh=1' : ''}`,
            fresh ? { cache: 'no-store' } : undefined),

    customerInfo: (appUserId: string) => sdk(appUserId).getCustomerInfo(),

    /**
     * Show the paywall attached to OFFERING_ID. Resolves `true` on a completed
     * purchase, `false` when the user closed it. A missing offering or a
     * missing paywall is a BillingSetupError naming what to configure; any
     * other failure is thrown as-is for the caller to show.
     */
    async presentPaywall(appUserId: string, customerEmail?: string): Promise<boolean> {
        const purchases = sdk(appUserId);
        const offering = (await purchases.getOfferings()).all[OFFERING_ID];
        if (!offering) {
            throw new BillingSetupError(`RevenueCat offering "${OFFERING_ID}" is not configured or has no packages.`);
        }
        if (!offering.hasPaywall) {
            throw new BillingSetupError(
                `RevenueCat offering "${OFFERING_ID}" has no paywall attached. Build one in the dashboard (Paywalls) and attach it to this offering.`,
            );
        }
        try {
            await purchases.presentPaywall({ offering, customerEmail });
            return true;
        } catch (e) {
            if (e instanceof PurchasesError && e.errorCode === ErrorCode.UserCancelledError) return false;
            throw e;
        }
    },
};
