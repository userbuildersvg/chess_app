-- The Improvement Profile: imported games, and the evidence found in them.
--
-- WHY THESE ARE NOT `games` AND `moves`
-- ------------------------------------
-- `games` holds what a player PLAYED here, and `reweight_candidates()` reads
-- it - `owner like 'user:%'` - to steer what the AI plays against everybody.
-- Imported PGNs are games somebody else may have played, in any strength, from
-- any source. Putting them in `games` would let anyone poison the global move
-- pool by importing a pile of grandmaster games, anonymously and repeatedly.
--
-- So they live here instead, deliberately outside every query that feeds the
-- engine's candidate pool. If a later change needs imported games in a global
-- calculation, that is a decision to take on purpose and write down - not one
-- to inherit by reusing a table.
--
-- OWNERSHIP, THE SAME SHAPE AS EVERYWHERE ELSE
-- -------------------------------------------
-- `owner` is the opaque identity string from identity.py, exactly as
-- `games.owner` is: `user:42` or `guest:8f2a…`. Every read filters on it.
--
-- It is on BOTH tables rather than only on the parent. Findings cascade with
-- their game, so the column is redundant for deletion - but the profile
-- aggregation groups a person's findings across every game they own, and
-- joining to the parent on the hot path of the one query this feature exists
-- to run is a cost with nothing to show for it.

create table if not exists imported_games (
    id           bigserial primary key,
    owner        text   not null,

    -- The PGN as it arrived, after validation. Kept whole so a game can be
    -- re-analysed at a different depth, opened in Review, or re-detected
    -- against a taxonomy that has since grown, without asking for the file
    -- again. It is the only copy: nothing else in this schema can reconstruct
    -- the moves.
    pgn          text   not null,

    -- Headers worth showing in a list. Nullable because a PGN is not obliged
    -- to carry any of them, and a game with no Event is still a game.
    white        text,
    black        text,
    result       text,
    played_on    text,
    event        text,

    -- Which side the account played, as 'white' | 'black'. This is the whole
    -- point of the import: a finding is only about the person if it is about
    -- their moves. Told by the importer, never guessed from the names - a
    -- username match is a coincidence waiting to mislabel somebody's data.
    player_color text   not null,

    ply_count    integer not null default 0,
    source_name  text,
    created_at   double precision not null,

    -- pending -> analysing -> done | failed. A text column and not an enum:
    -- adding a state to an enum in Postgres is a migration with a lock on it,
    -- and this state machine is young.
    state        text   not null default 'pending',
    -- Why it failed, in words a person can act on. Null unless state='failed'.
    error        text,
    analysed_at  double precision
);

-- The worker's claim query: the oldest pending game, oldest first. Partial, so
-- the index holds only the rows the worker ever looks at rather than every
-- game ever imported - which is the overwhelming majority once a library
-- settles down.
create index if not exists idx_imported_pending
    on imported_games (created_at)
    where state = 'pending';

-- Every list, every profile read, every delete.
create index if not exists idx_imported_owner on imported_games (owner, created_at desc);


-- One row per ply where a detector found something. THE evidence: every claim
-- the profile makes is a count over this table, and every claim can therefore
-- be shown back as specific moves in specific games.
create table if not exists game_findings (
    id         bigserial primary key,
    game_id    bigint not null references imported_games (id) on delete cascade,
    owner      text   not null,

    ply        integer not null,
    -- A theme id from pattern_detectors.THEMES. Text rather than a foreign key
    -- to a themes table: the taxonomy is code, it is versioned with the code,
    -- and a lookup table would be a second place to add a theme and forget.
    theme      text   not null,
    -- 'minor' | 'serious' | 'critical', from how much the move actually cost.
    severity   text   not null,
    -- Centipawns lost by the move, as the grader measured it. Null where the
    -- detector fired on something other than evaluation loss.
    cpl        integer,

    -- Enough to show the moment back without re-reading the PGN.
    fen_before text   not null,
    move_san   text   not null,
    best_san   text,
    phase      text   not null,
    created_at double precision not null
);

-- The aggregation this feature exists for: everything one person's games have
-- shown, grouped by theme.
create index if not exists idx_findings_owner_theme on game_findings (owner, theme);

-- Deleting a game takes its findings with it through the cascade, which needs
-- this to not be a sequential scan.
create index if not exists idx_findings_game on game_findings (game_id);
