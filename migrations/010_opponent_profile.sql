-- The opponent profile the AI played each move at ("beginner" ... "master",
-- opponent_profiles.py). Replaces the 1-20 `difficulty` integer, which is
-- left in place and no longer written: rows before this migration keep the
-- meaning they had, rows after it carry the profile id here.
alter table moves add column if not exists opponent_profile text;
