-- Closed beta access.
--
-- Zugzwang is not publicly reachable any more. A visitor gets the beta
-- landing page and nothing else until they redeem an invitation code, and
-- that decision is made on the server for every single API request
-- (`beta_gate.py`). Nothing here is a frontend convenience: the tables below
-- ARE the authorization state.
--
--
-- WHY THE CODE ITSELF IS NOT IN THIS SCHEMA
-- -----------------------------------------
-- Same reasoning as `sessions.token_hash` and `password_resets.token_hash`,
-- and for the same reason it applies more strongly here than to a password: a
-- beta code is a bearer credential that is handed out over email, chat and
-- screenshots, and it grants access to the whole product. A dump of this
-- table must not be a list of working invitations.
--
-- So `code_hash` is HMAC-SHA256 of the canonical code under a server-side
-- pepper (`beta_service._pepper()`), never the code. The pepper matters: the
-- code space is `ZG-BETA-XXXX-XXXX` over a 32-character alphabet, which is 40
-- bits - enough that nobody guesses one online against a rate limiter, and
-- NOT enough to resist an offline sweep of a leaked table with a bare
-- SHA-256. A keyed hash makes the leaked table useless without the key.
--
-- A keyed hash is also why lookup is by primary key rather than by scanning
-- and comparing: the index does the work, there is no per-row comparison to
-- time, and the one comparison that does happen uses `compare_digest`.
--
--
-- WHY REDEMPTIONS ARE THEIR OWN TABLE
-- -----------------------------------
-- `beta_codes.uses` and a `claimed_by` column on the same row would answer
-- "was this used" for a single-use code and nothing at all for a code with
-- `max_uses > 1`. The redemption row is the durable grant - it is what
-- `has_access()` reads on every request - so it has to exist once per
-- redeemer, not once per code.
--
-- It is also what makes access survive the guest-to-account transition. A
-- redemption is bound to an identity string, exactly as `games.owner` is
-- (§13): `guest:8f2a…` when a visitor redeems from the landing page, rewritten
-- to `user:42` when they sign up, in the same one-way way guest games are
-- claimed. `users.beta_access` is then set as well, so a signed-in account's
-- authorization is one column on a row it already loads.

-- ---------------------------------------------------------------------
-- The codes
-- ---------------------------------------------------------------------

create table if not exists beta_codes (
    id         bigserial primary key,
    -- HMAC-SHA256(canonical code) as hex. Unique, because two codes hashing
    -- the same is either a duplicate generation or a bug, and neither should
    -- be storable.
    code_hash  text not null,
    -- The first group only - "ZG-BETA-7K4M-...." - so a human can tell two
    -- codes apart in the admin listing and in a support conversation without
    -- the table holding anything redeemable. Four of the eight secret
    -- characters, which is 20 bits: identifying, not guessable.
    code_label text not null,
    created_at double precision not null,
    -- Who minted it. Free text, written by the admin tool - "david", "batch
    -- 2026-09-08". Not a foreign key: the person generating codes is an
    -- operator with a shell, not necessarily an account in this database.
    created_by text,
    -- NULL means it never expires. Deliberately nullable rather than a
    -- far-future default, so "no expiry" is a state the schema can express
    -- and not a magic timestamp somebody has to recognise.
    expires_at double precision,
    enabled    boolean not null default true,
    -- One code, one tester, unless an operator says otherwise. The check is
    -- what makes `uses < max_uses` a meaningful guard rather than a
    -- convention.
    max_uses   integer not null default 1 check (max_uses >= 1),
    uses       integer not null default 0 check (uses >= 0),
    notes      text
);

create unique index if not exists idx_beta_codes_hash on beta_codes (code_hash);
-- The admin listing orders by this, and the expiry sweep reads it.
create index if not exists idx_beta_codes_created on beta_codes (created_at);

-- ---------------------------------------------------------------------
-- The grants
-- ---------------------------------------------------------------------

create table if not exists beta_redemptions (
    id          bigserial primary key,
    code_id     bigint not null references beta_codes (id) on delete cascade,
    -- The identity string, unparsed, exactly as `games.owner` holds it.
    -- "guest:8f2a…" until the redeemer signs up, "user:42" afterwards.
    identity    text not null,
    -- Set when the grant is transferred onto an account, so the admin tool
    -- can answer "which account did this code become" without parsing the
    -- identity string. NULL while the grant still belongs to a guest.
    user_id     bigint references users (id) on delete cascade,
    redeemed_at double precision not null,
    -- Kept for support: which address burned the code. Never shown to a
    -- caller, never used for authorization.
    redeemed_ip text
);

-- The authorization read. Every gated request that is not signed in resolves
-- through this exact lookup, so it is the one index here that must not
-- sequentially scan.
create unique index if not exists idx_beta_redemptions_identity
    on beta_redemptions (identity);
create index if not exists idx_beta_redemptions_code on beta_redemptions (code_id);
create index if not exists idx_beta_redemptions_user on beta_redemptions (user_id);

-- ---------------------------------------------------------------------
-- The account's own flag
-- ---------------------------------------------------------------------
--
-- Denormalised from `beta_redemptions` on purpose. A signed-in request has an
-- account id in hand and this turns its authorization into a column on a row
-- the app already knows how to read, rather than a second query keyed on a
-- string. The redemption row remains the audit trail of WHICH code granted
-- it; this is the answer to "may this account use the app".
--
-- Additive, like every migration in this directory: an existing account gets
-- `false` and is gated exactly like anyone else, which is the correct
-- behaviour for a build whose whole point is that access is now closed.

alter table users add column if not exists beta_access boolean not null default false;

-- Answers "how many testers are in" without scanning the accounts that are
-- not, which is most of them once this ships.
create index if not exists idx_users_beta_access on users (beta_access)
    where beta_access;
