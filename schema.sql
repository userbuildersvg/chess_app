-- Zugzwang - Postgres schema (Neon)
--
-- One database replaces the two SQLite files that lived on Render's
-- ephemeral disk: `data/accounts.db` (users, sessions) and
-- `data/learning.db` (games, moves). Both were deleted by every redeploy and
-- every spin-down, which is why DEPLOY.md lists persistent storage as the
-- blocker in front of ACCOUNTS_ENABLED.
--
-- Run once against DATABASE_URL. Safe to re-run: every statement is
-- IF NOT EXISTS.
--
--
-- OWNERSHIP IS THE IDENTITY STRING, VERBATIM
-- ------------------------------------------
-- `games.owner` holds exactly what `identity.py` resolves a request to -
-- "guest:8f2a1c…" or "user:42" - and no query in the application parses it.
-- That is deliberate, and it is the same seam CLAUDE.md §13 describes: the
-- rest of the app already passes one opaque string around, so the database
-- storing that same string means turning accounts on still changes only
-- *which string arrives*.
--
-- It also makes claiming a guest's history one UPDATE on one table. Ownership
-- lives ONLY on `games`; moves hang off game_id and follow automatically, so
-- no move row is ever rewritten.
--
-- The consequence to be aware of: guests now write here. Previously a guest's
-- learning database was in memory and died with the session
-- (guest_learning.py). Anonymous rows therefore accumulate, and
-- `idx_games_owner_unclaimed` exists so a retention sweep over unclaimed
-- guest games stays cheap.


-- ---------------------------------------------------------------------
-- Accounts (was data/accounts.db)
-- ---------------------------------------------------------------------

create table if not exists users (
    id            bigserial primary key,
    username      text   not null,
    -- Case-insensitive uniqueness enforced by the database rather than a
    -- check-then-insert two threads can both pass. Carried over unchanged
    -- from the SQLite schema, for the same reason.
    username_ci   text   not null,
    salt          bytea  not null,
    password_hash bytea  not null,
    -- Stored per row so raising the iteration count later rehashes users on
    -- sign-in instead of locking them out.
    iterations    integer not null,
    created_at    double precision not null,
    last_login_at double precision
);

create unique index if not exists idx_users_username_ci on users (username_ci);

create table if not exists sessions (
    -- Only the SHA-256 hash is stored, so a table dump hands over no live
    -- sessions.
    token_hash text primary key,
    user_id    bigint not null references users (id) on delete cascade,
    created_at double precision not null,
    expires_at double precision not null
);

create index if not exists idx_sessions_user   on sessions (user_id);
create index if not exists idx_sessions_expiry on sessions (expires_at);


-- ---------------------------------------------------------------------
-- Cross-game learning (was data/learning.db)
-- ---------------------------------------------------------------------

create table if not exists games (
    id                bigserial primary key,
    -- The identity string, unparsed. See the header.
    owner             text not null,
    started_at        text not null,
    ended_at          text,
    human_color       text not null,
    opening_signature text,
    result            text not null default 'in_progress',
    termination       text
);

-- Every profile read filters on owner first, then on result.
create index if not exists idx_games_owner on public.games (owner, result);
-- Supports the retention sweep over guest rows nobody ever claimed, without
-- scanning the accounts' games alongside them.
create index if not exists idx_games_owner_unclaimed on public.games (started_at)
    where owner like 'guest:%';

create table if not exists moves (
    id          bigserial primary key,
    game_id     bigint not null references games (id) on delete cascade,
    ply         integer not null,
    color       text not null,
    mover       text not null,
    move_uci    text not null,
    san         text,
    fen_before  text,
    fen_after   text,
    eval_before double precision,
    eval_after  double precision,
    difficulty  integer,
    source      text,
    explanation text,
    quality     text,
    -- One row per half-move per game. Also makes the asynchronous grade
    -- update (UPDATE ... WHERE game_id = ? AND ply = ?) provably hit at most
    -- one row - in SQLite that was true only by convention.
    unique (game_id, ply)
);

create index if not exists idx_moves_game       on moves (game_id);
-- Drives the exact-position lookup in reweight_candidates(), which runs on
-- every AI move. The one query here that must not sequentially scan.
create index if not exists idx_moves_fen_before on moves (fen_before);


-- ---------------------------------------------------------------------
-- Guest history that has already been handed to an account
-- ---------------------------------------------------------------------
--
-- Claiming is one-way and once per guest identity. Without this table, two
-- people sharing one browser would each inherit the other's games: the second
-- to sign up would claim the first one's rows, because the guest cookie is
-- still sitting there.

create table if not exists claimed_guests (
    guest_identity text primary key,
    user_identity  text not null,
    claimed_at     double precision not null
);
