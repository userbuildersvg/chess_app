-- Accounts that have no password, because they were created by signing in
-- with a provider.
--
-- Such a row still carries a salt and a hash - random ones - so that nothing
-- reading the table can distinguish "no password" from "a password I cannot
-- guess", and so that a bug elsewhere cannot turn an empty column into a
-- match. This flag is what actually decides, and `verify_password` refuses a
-- row where it is false while still paying the same hashing cost, so the
-- refusal cannot be told apart from a wrong password by timing it.
--
-- DEFAULT true, so every account that existed before this migration keeps
-- working: they all have real passwords by definition.

alter table users add column if not exists has_password boolean not null default true;
