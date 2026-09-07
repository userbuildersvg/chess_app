-- Preferences that belong to an account rather than to a browser.
--
-- The five preferences the app already has - board theme, coordinates, engine
-- numbers, move grading, and the open rail panel - have always lived in
-- `localStorage`. That is the right home for a guest, and the wrong one for
-- an account: everything else an account owns now follows the person to
-- another device, and their board would not.
--
-- ONE ROW PER ACCOUNT, NOT ONE PER SETTING
-- ----------------------------------------
-- A key/value table would allow partial writes without reading first, which
-- is the usual argument for it. It is not worth it here: this is five small
-- values written together whenever one of them changes, read once at sign-in,
-- and never queried across accounts. One JSONB document is one row to read,
-- one row to write, and no migration when a sixth preference is added -
-- which is the change most likely to happen.
--
-- The tradeoff accepted knowingly: nothing in the database constrains what
-- goes in here. `settings_service.py` is the only writer and it validates
-- against an explicit allowlist, so an unknown key never reaches the column.
-- If preferences ever grow to the point where they need per-key history or
-- per-key permissions, that is the moment for a real table, not before.

create table if not exists user_settings (
    user_id    bigint primary key references users (id) on delete cascade,
    prefs      jsonb not null default '{}'::jsonb,
    updated_at double precision not null
);
