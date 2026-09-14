-- Account security pass (password_security.py, data_keys.py).
--
-- `password_scheme` says which scheme produced a user's hash: 'pbkdf2' for
-- everything before this migration (PBKDF2 over the password itself) and
-- 'pbkdf2_pepper_v1' once PASSWORD_PEPPER is set and the account has
-- signed in again. Verification reads it; a successful login rehashes.
alter table users add column if not exists password_scheme text not null default 'pbkdf2';

-- One random data key per account, wrapped under APP_MASTER_KEY. Sensitive
-- fields are AES-GCM'd under it; deleting the account zeroes the wrapped
-- key and sets destroyed_at, which is what makes stale encrypted copies in
-- backups unreadable.
create table if not exists user_data_keys (
    -- No foreign key on purpose: the zeroed row outlives the account as the
    -- record that its key was destroyed.
    user_id            bigint primary key,
    encrypted_data_key bytea not null,
    key_version        integer not null default 1,
    created_at         double precision not null,
    destroyed_at       double precision
);
