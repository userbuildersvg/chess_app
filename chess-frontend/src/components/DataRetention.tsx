/**
 * What Zugzwang keeps, and for how long.
 *
 * ONE COMPONENT, TWO PLACES
 * -------------------------
 * It renders on both `/about` and `/settings`. Written twice it would be
 * wrong in one of them within a release - the retention rules are enforced by
 * `GUEST_RETENTION_DAYS`, by `purge_unclaimed_guest_games()` and by which
 * stores are in memory, and none of those changes announce themselves to a
 * paragraph of prose somebody wrote once.
 *
 * Every claim here is one the code actually enforces:
 *
 *   * 30 days for unclaimed guest games   - GUEST_RETENTION_DAYS, swept daily
 *   * claimed games belong to the account - claim_guest_games rewrites owner
 *   * account data until deletion         - delete_user cascades
 *   * tokens stored hashed only           - auth_service, both kinds
 *   * corrections/sandbox/reviews are memory - CLAUDE.md section 13, "what is
 *                                           NOT persisted, deliberately"
 *
 * That last one is the reason this component exists at all. Everything else
 * here is reassuring; that one is the disappointment, and a person finding it
 * out by losing something is a worse way to learn it than reading it here.
 */
export function DataRetention() {
    return (
        <section className="settings-card">
            <h2 className="settings-card-title">Your data</h2>
            <p className="settings-card-sub">
                Plainly, because the honest version is short.
            </p>

            <div className="settings-row">
                <span className="settings-row-label">
                    Playing as a guest
                    <span className="settings-row-hint">
                        Your games are saved against an anonymous id in this browser and kept for
                        30 days. Nothing identifies you. If you create an account within those 30
                        days, the games come with you.
                    </span>
                </span>
            </div>

            <div className="settings-row">
                <span className="settings-row-label">
                    With an account
                    <span className="settings-row-hint">
                        Your games, the moves in them and your board preferences are kept until you
                        delete the account. Deleting it removes all of it immediately.
                    </span>
                </span>
            </div>

            <div className="settings-row">
                <span className="settings-row-label">
                    Passwords and sessions
                    <span className="settings-row-hint">
                        Passwords are stored as salted hashes, never in a form anyone can read
                        back. Sign-in sessions and password-reset links are stored the same way, so
                        the database holds no usable copy of either.
                    </span>
                </span>
            </div>

            <div className="settings-row">
                <span className="settings-row-label">
                    What is <em>not</em> saved
                    <span className="settings-row-hint">
                        Learner Mode sessions, Post-Mortem reviews and the corrections and practice
                        from the learning loop all live in memory only. They are not attached to
                        your account and they do not survive a restart of the server. If something
                        there matters to you, keep your own copy.
                    </span>
                </span>
            </div>
        </section>
    );
}
