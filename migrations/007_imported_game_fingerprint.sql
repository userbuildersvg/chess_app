-- A deterministic identity for an imported game, so the same game cannot be
-- counted twice.
--
-- WHY THIS MATTERS MORE THAN IT LOOKS
-- ----------------------------------
-- Every claim the improvement profile makes is a COUNT: how many findings, and
-- across how many games. Confidence is derived from the number of distinct
-- games a theme appears in. So importing the same PGN five times did not just
-- clutter the library - it turned one game into five games' worth of evidence
-- and pushed a theme from "low confidence" to "high" without a single new move
-- being played. That is the feature lying, which is worse than the feature
-- being empty.
--
-- WHAT THE FINGERPRINT IS OVER
-- ----------------------------
-- The starting position and the move sequence in UCI - the game itself, and
-- nothing about the file it arrived in. Headers are deliberately excluded:
-- the same game exported from two sites differs in Event, Site, Date and the
-- spelling of both players' names, and none of that makes it a different game.
--
-- Player colour is excluded too, which is a real decision rather than an
-- oversight. Re-importing a game you already have, having noticed it was filed
-- under the wrong colour, is refused as a duplicate - the fix is to remove it
-- and add it again, which is a step, and the alternative is worse: a game
-- present twice under both colours, contributing evidence for a player who
-- made only half those moves.
--
-- NULLABLE, AND WHY THAT IS SAFE
-- ------------------------------
-- Postgres unique indexes permit many NULLs, so rows that predate this column
-- do not collide and do not need backfilling - which could not be done in SQL
-- anyway, since computing one means replaying the moves. The index is written
-- partial to say that out loud rather than leave it as a property somebody has
-- to remember about NULL.

alter table imported_games add column if not exists fingerprint text;

-- Per OWNER, not globally. Two people importing the same famous game are two
-- people each with a game, and deduplicating across accounts would mean the
-- second person silently getting nothing.
create unique index if not exists idx_imported_fingerprint
    on imported_games (owner, fingerprint)
    where fingerprint is not null;
