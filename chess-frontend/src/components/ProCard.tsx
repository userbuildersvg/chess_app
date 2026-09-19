/**
 * The Improvement Profile's compact Pro card. Free: what Pro unlocks and one
 * button that opens the same RevenueCat paywall Settings uses. Pro: one line
 * saying so. Guests and unverified states render nothing - a card that sells
 * Pro to someone who cannot buy it, or whose status could not be read, is
 * noise at best and a lie at worst.
 */

import { Link } from 'react-router-dom';
import { useBilling } from '../hooks/useBilling';

/** `onUpgraded` lets the page refetch what the server now shows more of. */
export function ProCard({ onUpgraded }: { onUpgraded?: () => void }) {
    const { status, pro, canUpgrade, available, busy, error, openPaywall } = useBilling();
    if (!status?.signed_in || !status.verified) return null;

    if (pro) {
        return (
            <p className="pf-pro pf-pro-active" data-testid="pf-pro-active">
                Pro active - full profile history enabled.
            </p>
        );
    }
    if (!canUpgrade) return null;
    return (
        <section className="pf-pro" data-testid="pf-pro-card">
            <p className="pf-pro-text">
                Zugzwang Pro unlocks full recurring-pattern history, more saved corrections, and
                more practice/re-test opportunities.
            </p>
            {available ? (
                <button
                    type="button" className="acct-btn acct-btn-primary" data-testid="pf-upgrade"
                    disabled={busy} onClick={() => void openPaywall().then((bought) => { if (bought) onUpgraded?.(); })}
                >
                    {busy ? 'Opening…' : 'Upgrade to Pro'}
                </button>
            ) : (
                <Link className="acct-btn acct-btn-primary" to="/settings#subscription" data-testid="pf-upgrade">
                    Upgrade to Pro
                </Link>
            )}
            {error && <p className="acct-error" role="alert">{error}</p>}
        </section>
    );
}
