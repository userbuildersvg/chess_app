-- Password reset tokens.
--
-- ONLY THE HASH IS STORED
-- -----------------------
-- Same reasoning as `sessions.token_hash`, and it matters more here: a reset
-- token is a bearer credential that takes over an account, and it arrives by
-- email, which is not a confidential channel. A database dump must not hand
-- anyone a working reset link, so the token itself exists exactly twice - in
-- the email that was sent, and in the URL the person clicks. Never here.
--
-- SHA-256 without a salt, deliberately, exactly as sessions are: these are
-- 256 bits of `secrets` output, not passwords. There is no dictionary to
-- attack and nothing for a salt to defend against, and a slow hash on a
-- lookup that happens on every click buys nothing.
--
-- SINGLE USE, AND THE ROW PROVES IT
-- ---------------------------------
-- `used_at` is set rather than the row deleted, so that a second click on the
-- same link can be distinguished from a link that never existed. Both are
-- refused, but the distinction is the difference between "you already used
-- this, request another" and a blank stare - and an audit trail of which
-- tokens were actually redeemed is worth keeping while accounts are young.
--
-- The sweep of old rows rides on `expires_at`; see `purge_expired_resets()`.

create table if not exists password_resets (
    token_hash text primary key,
    user_id    bigint not null references users (id) on delete cascade,
    created_at double precision not null,
    expires_at double precision not null,
    used_at    double precision
);

-- Every reset invalidates the account's other outstanding tokens, so this
-- lookup runs on every successful reset as well as on every deletion cascade.
create index if not exists idx_password_resets_user on password_resets (user_id);

-- Drives the expiry sweep.
create index if not exists idx_password_resets_expiry on password_resets (expires_at);
