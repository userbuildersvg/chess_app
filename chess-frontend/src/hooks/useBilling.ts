/**
 * One place that knows whether this account is Pro and how to sell it.
 *
 * Settings, the Improvement Profile and the header all ask the same two
 * questions - "is this person Pro?" and "open the paywall" - so they share
 * this rather than three copies of the status/paywall/refresh dance. The
 * answer is the SERVER's (`/api/billing/status`); the SDK is only used to
 * draw the paywall and to hand back the management URL.
 */

import { useCallback, useEffect, useState } from 'react';
import {
    BillingConflictError, BillingSetupError, billingService, sdkAvailable, type BillingStatus,
} from '../services/billingService';

/** Reasons a second look cannot fix. Polling through these is just noise. */
const HOPELESS = ['provider_key_rejected', 'not_configured'];

export function useBilling() {
    const [status, setStatus] = useState<BillingStatus | null>(null);
    const [manageUrl, setManageUrl] = useState<string | null>(null);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [justBought, setJustBought] = useState(false);

    const load = useCallback(async (fresh = false) => {
        const s = await billingService.status(fresh);
        setStatus(s);
        if (s.app_user_id && sdkAvailable()) {
            // The management URL only exists once RevenueCat knows the customer
            // has a subscription; a failure here just hides the Manage link.
            billingService.customerInfo(s.app_user_id)
                .then((info) => setManageUrl(info.managementURL))
                .catch(() => setManageUrl(null));
        }
        return s;
    }, []);

    useEffect(() => {
        load().catch((e) => setError(e instanceof Error ? e.message : 'Subscription status could not be loaded.'));
    }, [load]);

    /**
      * A web purchase and RevenueCat's subscriber record settle separately, so
      * the very first fresh lookup after checkout can still honestly say Free.
      * Ask again for a few seconds before believing it, and do not publish the
      * intermediate answers - flashing "Free" at somebody who has just paid is
      * how a working purchase looks broken.
      */
    const confirmPro = useCallback(async () => {
        for (let attempt = 0; attempt < 6; attempt++) {
            const s = await billingService.status(true).catch(() => null);
            if (s?.pro) { setStatus(s); return true; }
            // A rejected key answers the same way six times. Stop, and let the
            // caller say something true instead of spinning.
            if (s && HOPELESS.includes(s.reason ?? '')) { setStatus(s); return false; }
            await new Promise((r) => setTimeout(r, 1500));
        }
        await load(true).catch(() => undefined);   // settle on the server's last word
        return false;
    }, [load]);

    /** Open the paywall. Resolves true if a purchase completed and the server now agrees. */
    const openPaywall = useCallback(async (email?: string | null) => {
        if (!status?.app_user_id) return false;
        setBusy(true);
        setError(null);
        try {
            const bought = await billingService.presentPaywall(status.app_user_id, email ?? undefined);
            if (!bought) return false;
            setJustBought(true);
            const confirmed = await confirmPro();
            if (!confirmed) {
                // Never "the purchase failed": it may well not have.
                setError('Payment went through, but Pro has not appeared yet. '
                    + 'This can take a moment - reload this page shortly.');
            }
            return confirmed;
        } catch (e) {
            // A conflict is usually the happy case wearing an error's clothes:
            // this customer already owns it. Ask the server once - never open
            // checkout again, which returns the identical 409 - and let the
            // answer decide what to say.
            if (e instanceof BillingConflictError) {
                const s = await billingService.status(true).catch(() => null);
                if (s?.pro) { setStatus(s); setJustBought(true); return true; }
                setError('Checkout could not be started: this customer already has a '
                    + 'subscription or an unfinished checkout. Use Refresh below, or a '
                    + 'different test customer.');
                return false;
            }
            // A setup gap is named as such so nobody reads it as a card decline.
            setError(e instanceof BillingSetupError
                ? `Setup needed: ${e.message}`
                : e instanceof Error ? e.message : 'The purchase could not be completed.');
            return false;
        } finally {
            setBusy(false);
        }
    }, [status?.app_user_id, confirmPro]);

    /** Ask the server again, by hand. The way out of a stale or conflicted state. */
    const refresh = useCallback(async () => {
        setBusy(true);
        setError(null);
        try {
            return await load(true);
        } catch (e) {
            setError(e instanceof Error ? e.message : 'Subscription status could not be loaded.');
            return null;
        } finally {
            setBusy(false);
        }
    }, [load]);

    return {
        status,
        pro: status?.pro === true,
        /** Signed in, not Pro, and the server actually checked - the only state that should sell. */
        canUpgrade: status?.signed_in === true && !status.pro,
        available: sdkAvailable(),
        manageUrl,
        busy,
        error,
        justBought,
        openPaywall,
        refresh,
        reload: load,
    };
}
