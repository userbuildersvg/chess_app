-- Admin access granted by a one-time invite code (admin_invites.py).
--
-- `is_admin` is the second way an account becomes admin, beside the
-- ADMIN_EMAILS allowlist. A redemption row per consumed code hash is what
-- makes a code one-time: the primary key refuses the second redemption in
-- the same transaction that would have granted it. Only the HMAC of the code
-- is ever stored - the raw code exists on the founder's screen and nowhere
-- else.
alter table users add column if not exists is_admin boolean not null default false;

create table if not exists admin_invite_redemptions (
    code_hash   text primary key,
    user_id     bigint not null references users (id) on delete cascade,
    redeemed_at double precision not null
);
