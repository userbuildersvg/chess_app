/**
 * Settings > Subscription. Status from the server, paywall from RevenueCat.
 *
 * Only ever mounted for a signed-in account (Settings redirects guests), so a
 * purchase is always tied to `user:<id>` - that was the decision, and it is
 * why there is no anonymous-purchase transfer to handle here.
 *
 * The plan lines say what each plan INCLUDES. Nothing here says "N remaining"
 * because nothing counts yet; the only enforced limit is the profile's
 * recurring-theme visibility (billing_api.gate_findings).
 */

import { useBilling } from '../hooks/useBilling';
import { limitLines } from '../services/billingService';

export function SubscriptionSettings({ email }: { email: string | null }) {
    const { status, pro, available, manageUrl, busy, error, justBought, openPaywall } = useBilling();

    const planLabel = !status
        ? 'Loading…'
        : pro
            ? 'Pro active'
            : status.verified ? 'Free' : 'Could not be checked right now';
    const planHint = pro && status?.expires_at
        ? `Renews or ends ${new Date(status.expires_at).toLocaleDateString()}`
        : pro ? 'Lifetime' : null;

    return (
        <section className="settings-card" id="subscription" data-section="subscription">
            <h2 className="settings-card-title">Zugzwang Pro</h2>
            <p className="settings-card-sub">
                Unlock more reviews, more saved lessons, full improvement history, and more
                practice/re-tests.
            </p>
            <div className="settings-row">
                <span className="settings-row-label">
                    Plan
                    {planHint && <span className="settings-row-hint">{planHint}</span>}
                </span>
                <span className="settings-row-value" data-testid="billing-plan">{planLabel}</span>
            </div>
            {status && !pro && (
                <div className="settings-row">
                    <span className="settings-row-label">
                        Upgrade
                        <span className="settings-row-hint">Monthly, yearly, or once for life.</span>
                    </span>
                    <button
                        className="acct-btn acct-btn-primary" type="button" onClick={() => void openPaywall(email)}
                        disabled={busy || !available} data-testid="billing-upgrade"
                    >
                        {busy ? 'Opening…' : 'See plans'}
                    </button>
                </div>
            )}
            {manageUrl && (
                <div className="settings-row">
                    <span className="settings-row-label">
                        Manage
                        <span className="settings-row-hint">Change plan, update payment, or cancel.</span>
                    </span>
                    <a className="acct-btn acct-btn-quiet" href={manageUrl} target="_blank" rel="noopener noreferrer">
                        Manage subscription
                    </a>
                </div>
            )}
            {status?.limits_preview && (
                <dl className="billing-plans" data-testid="billing-plans">
                    <div>
                        <dt>Free includes</dt>
                        <dd>{limitLines(status.limits_preview.free)}</dd>
                    </div>
                    <div>
                        <dt>Pro includes</dt>
                        <dd>{limitLines(status.limits_preview.pro)}</dd>
                    </div>
                </dl>
            )}
            {justBought && <p className="auth-ok" data-testid="billing-ok">Welcome to Pro.</p>}
            {!available && status && !pro && (
                <p className="settings-row-hint" data-testid="billing-unconfigured">Purchases are not available in this build.</p>
            )}
            {status && !status.configured && (
                <p className="settings-row-hint">The server is not configured to verify subscriptions yet.</p>
            )}
            {error && <p className="acct-error" role="alert" data-testid="billing-error">{error}</p>}
        </section>
    );
}
