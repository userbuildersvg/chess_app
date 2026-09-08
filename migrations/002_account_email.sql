-- Email on accounts, and the identity Google OAuth links on.
--
-- Accounts were username + password. Email is added ALONGSIDE the username
-- rather than replacing it: the username is what the UI shows and what 85
-- passing tests are written against, and replacing a working identifier buys
-- nothing today. What email buys is two things the username cannot:
--
--   * a stable key to link a Google sign-in to an existing account, so
--     signing in with Google and signing in with a password reach the same
--     internal account rather than silently creating a second one;
--   * the address a password reset would have to send to, when that is built
--     (deliberately not in this milestone - see DEPLOY.md).
--
-- NULLABLE, deliberately. Existing accounts predate the column, and a NOT
-- NULL column with a made-up default would be a lie in every one of those
-- rows. Signup requires it going forward; the model does not pretend the old
-- rows have it.
--
-- Uniqueness is enforced case-insensitively on a separate email_ci column,
-- matching exactly how username_ci already works, so that "A@b.com" and
-- "a@b.com" cannot become two accounts. A PARTIAL unique index, because
-- several rows may legitimately have no email at all and SQL NULLs are not
-- equal to each other - without the WHERE clause this would be fine in
-- Postgres but would quietly stop meaning what it says if the predicate ever
-- changed.

alter table users add column if not exists email    text;
alter table users add column if not exists email_ci text;

create unique index if not exists idx_users_email_ci
    on users (email_ci) where email_ci is not null;

-- Federated logins. One row per (provider, subject), so a Google account is
-- linked to exactly one Zugzwang account and cannot be attached to a second.
--
-- `subject` is the provider's own immutable user id - Google's `sub` claim -
-- NOT the email address. Emails at Google can be changed and reassigned
-- within a workspace; `sub` cannot. Linking on the email is the classic way
-- to hand one person's account to another, and this table exists so that we
-- link on the thing that does not move.
create table if not exists federated_identities (
    provider   text   not null,
    subject    text   not null,
    user_id    bigint not null references users (id) on delete cascade,
    created_at double precision not null,
    primary key (provider, subject)
);

create index if not exists idx_federated_user on federated_identities (user_id);
