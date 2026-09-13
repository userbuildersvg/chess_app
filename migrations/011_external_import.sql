-- Where an imported game came from, and the evidence a review adds to it.
--
-- SOURCE SEPARATION
-- -----------------
-- `source_name` was a filename ("season.pgn"). It stays, but it is not a
-- source: the product now imports public games by username from Chess.com and
-- Lichess, and a library that cannot say which site a game came from cannot
-- show them apart or dedupe them by the site's own id. `source` is a small
-- vocabulary - 'chesscom' | 'lichess' | 'manual' - and rows from before this
-- migration are 'manual', which is what they were.
--
-- `external_id` is the site's own game id (the tail of the Link/Site URL).
-- Unique per (owner, source) so the same game fetched twice is refused by the
-- database rather than by a check that two requests can both pass. The
-- fingerprint index from 007 still stands behind it for games with no id.
--
-- No passwords, no tokens: nothing here can hold a credential for either site.
-- Public games are fetched by username and that username is all that is kept.

alter table imported_games add column if not exists source          text not null default 'manual';
alter table imported_games add column if not exists source_username text;
alter table imported_games add column if not exists external_id     text;
alter table imported_games add column if not exists time_control    text;
alter table imported_games add column if not exists rated           boolean;
alter table imported_games add column if not exists variant         text;
alter table imported_games add column if not exists opening         text;
-- When the game was last opened in Review. Reviews themselves are in memory
-- (CLAUDE.md section 13), so this is the durable trace that one happened.
alter table imported_games add column if not exists reviewed_at     double precision;

create unique index if not exists idx_imported_external
    on imported_games (owner, source, external_id)
    where external_id is not null;

create index if not exists idx_imported_owner_source
    on imported_games (owner, source, created_at desc);

-- A finding's provenance. 'detector' rows are written by the profile worker
-- (pattern_detectors.py) and are the only rows the recurrence claims count.
-- 'correction' rows are written when a review of an imported game produces a
-- correction card: the same controlled theme, tagged with where the game came
-- from through its parent row. They are evidence for the source-tagged
-- summary, kept apart so a model-interpreted diagnosis cannot inflate a
-- deterministic count.
alter table game_findings add column if not exists origin     text not null default 'detector';
alter table game_findings add column if not exists confidence double precision;
