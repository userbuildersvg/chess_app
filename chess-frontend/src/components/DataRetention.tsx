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
 *   * imported games and their findings  - deleted with the account by
 *                                           auth_service.delete_user, which
 *                                           has to name every owner-keyed
 *                                           table by hand (section 24)
 *
 * That last one is the reason this component exists at all. Everything else
 * here is reassuring; that one is the disappointment, and a person finding it
 * out by losing something is a worse way to learn it than reading it here.
 */
export function DataRetention() {
    return (
        <section className="settings-card" id="data" data-section="data">
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
                        delete the account. Deleting it removes your live account data and destroys
                        the account data key used by the live system. Historical backups may
                        temporarily contain older encrypted data, but Zugzwang does not normally
                        restore deleted accounts. Aggregate counts that name no game, position or
                        text may remain.
                    </span>
                </span>
            </div>

            <div className="settings-row">
                <span className="settings-row-label">
                    Games you import
                    <span className="settings-row-hint">
                        PGNs you add to your improvement profile are stored on your account,
                        encrypted with a key that exists only for your account, along with the
                        analysis of them and the evidence behind every pattern it reports. The
                        position behind each finding, and the intent and diagnosis text of your
                        saved corrections, are encrypted the same way.
                        Removing a game deletes its analysis with it, and deleting your account
                        deletes the whole library. None of it is used to train anything, and none of
                        it is shared with other players.
                    </span>
                </span>
            </div>

            <div className="settings-row">
                <span className="settings-row-label">
                    Passwords and sessions
                    <span className="settings-row-hint">
                        Passwords are never stored: the server keeps a salted PBKDF2-SHA256 hash
                        (600,000 rounds) that cannot be turned back into the password. Sign-in
                        sessions and password-reset links are stored hashed too, so the database
                        holds no usable copy of any of them. No password, PGN, position or
                        correction text is ever sent to product analytics.
                    </span>
                </span>
            </div>

            <div className="settings-row">
                <span className="settings-row-label">
                    Session-only work
                    <span className="settings-row-hint">
                        Learner Mode sessions and Post-Mortem review boards live in memory and do
                        not survive a server restart. Corrections made while signed in, their game
                        evidence, and correction practice results are saved to your account;
                        guest corrections remain session-only.
                    </span>
                </span>
            </div>
        </section>
    );
}
