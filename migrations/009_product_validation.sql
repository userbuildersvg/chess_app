-- Privacy-bounded correction-loop events and move-grade audit records.
-- Raw PGNs, intent text, chat text, credentials, and account ids do not
-- belong in either table. actor_key is a server-HMAC pseudonym.

create table if not exists product_events (
    id            bigserial primary key,
    event_name    text not null,
    actor_key     text not null,
    game_id       text,
    correction_id text,
    source_mode   text,
    occurred_at   double precision not null,
    properties    jsonb not null default '{}'::jsonb
);

create index if not exists idx_product_events_time
    on product_events (occurred_at desc);
create index if not exists idx_product_events_actor_time
    on product_events (actor_key, occurred_at desc);
create index if not exists idx_product_events_funnel
    on product_events (event_name, occurred_at desc);

create table if not exists move_grade_audits (
    id               bigserial primary key,
    actor_key        text not null,
    game_id          text,
    correction_id    text,
    source_flow      text not null,
    occurred_at      double precision not null,
    fen_before       text not null,
    move_played      text not null,
    fen_after        text,
    side_to_move     text,
    player_color     text,
    engine_best      text,
    eval_before      double precision,
    eval_after       double precision,
    eval_perspective text not null default 'mover',
    engine_depth     integer,
    grade_assigned   text,
    key_decision     boolean not null default false,
    provider         text,
    model            text,
    fallback         boolean not null default false,
    timeout          boolean not null default false
);

create index if not exists idx_move_grade_audits_time
    on move_grade_audits (occurred_at desc);
create index if not exists idx_move_grade_audits_position
    on move_grade_audits (fen_before, move_played);
