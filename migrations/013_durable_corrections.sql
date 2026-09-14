-- Account-owned correction cards, the game evidence behind each generation,
-- and attempts made from a verified evidence position. Guest cards remain in
-- the bounded in-memory store and never reach these tables.

create table if not exists account_corrections (
    id               text primary key,
    user_id          bigint not null references users (id) on delete cascade,
    theme            text not null,
    player_intent    text not null,
    missed_factor    text not null,
    diagnosis        text not null,
    correction_rule  text not null,
    confidence       double precision not null,
    caveat           text,
    status           text not null default 'open',
    occurrence_count integer not null default 1,
    created_at       double precision not null,
    last_seen_at     double precision not null,
    unique (user_id, theme)
);

create index if not exists idx_account_corrections_user
    on account_corrections (user_id, last_seen_at desc);

create table if not exists correction_evidence (
    id                          bigserial primary key,
    correction_id               text not null references account_corrections (id) on delete cascade,
    imported_game_id            bigint references imported_games (id) on delete cascade,
    profile_finding_id          bigint references game_findings (id) on delete set null,
    review_id                   text,
    node_id                     text,
    ply                         integer,
    played_move                 text,
    preferred_move              text,
    preferred_san               text,
    source                      text not null,
    source_username             text,
    evidence                    jsonb not null,
    practice_available          boolean not null,
    practice_unavailable_reason text,
    practice_fen                text,
    practice_expected_uci       text,
    practice_expected_san       text,
    practice_session_id         text,
    practice_started_at         double precision,
    practice_completed_at       double precision,
    practice_outcome            text,
    created_at                  double precision not null
);

create index if not exists idx_correction_evidence_card
    on correction_evidence (correction_id, created_at desc);
create index if not exists idx_correction_evidence_game
    on correction_evidence (imported_game_id);

create table if not exists correction_practice_attempts (
    id                     text primary key,
    correction_evidence_id bigint not null references correction_evidence (id) on delete cascade,
    played_uci             text not null,
    passed                 boolean not null,
    outcome                text not null,
    hints_used             integer not null default 0,
    response_ms            integer,
    created_at             double precision not null
);

create index if not exists idx_correction_attempts_evidence
    on correction_practice_attempts (correction_evidence_id, created_at);
