-- =============================================================================
-- P4_RAG — knowledge base + state schema (project_specs.md §7)
-- pgvector DDL / HNSW params / match function verified via Context7
-- (/llmstxt/supabase_llms-full_txt, 2026-05-27). Run once in the Supabase SQL
-- editor (Dashboard -> SQL Editor -> New query -> paste -> Run). Idempotent.
-- =============================================================================

-- pgvector lives in the `extensions` schema on Supabase. Qualify the vector type
-- as extensions.vector(...) everywhere; keep `extensions` on the search_path so
-- the HNSW operator class and the <=> operator resolve during this run.
create extension if not exists vector with schema extensions;
set search_path = public, extensions;

-- --- sources: uploaded documents ---------------------------------------------
create table if not exists public.sources (
    id          uuid primary key default gen_random_uuid(),
    filename    text        not null,
    file_type   text        not null,                 -- pdf | docx | txt | faq
    uploaded_by bigint      not null,                 -- Telegram user_id
    uploaded_at timestamptz not null default now(),
    chunk_count int         not null default 0,
    sha256      text        not null,                 -- content hash (dedup)
    priority    int         not null default 0,       -- 100 = auto-learned FAQ (WOW 2)
    status      text        not null default 'active' -- active | deleted
);
-- one active source per content hash (dedup / skip re-ingest)
create unique index if not exists sources_sha256_active_uidx
    on public.sources (sha256) where status = 'active';

-- --- chunks: the knowledge base (vector + keyword) ---------------------------
create table if not exists public.chunks (
    id          uuid primary key default gen_random_uuid(),
    source_id   uuid        not null references public.sources (id) on delete cascade,
    chunk_index int         not null,
    content     text        not null,
    embedding   vector(1024) not null,     -- Voyage voyage-3.5 (§9.3)
    -- 'simple' config (no stemmer) is language-agnostic for RU/UK/EN (OQ-3).
    -- The 2-arg to_tsvector(regconfig, text) is IMMUTABLE -> valid in a generated column.
    fts         tsvector generated always as (to_tsvector('simple', content)) stored,
    token_count int         not null default 0,
    priority    int         not null default 0,       -- inherited from source
    metadata    jsonb       not null default '{}'::jsonb, -- {page, char_start, char_end}
    created_at  timestamptz not null default now()
);
-- HNSW (Supabase-recommended default; OQ-6). Requires pgvector >= 0.7.0.
create index if not exists chunks_embedding_hnsw_idx
    on public.chunks using hnsw (embedding vector_cosine_ops)
    with (m = 16, ef_construction = 128);
-- GIN over the generated tsvector for the BM25/FTS arm (WOW 1).
create index if not exists chunks_fts_gin_idx on public.chunks using gin (fts);
create index if not exists chunks_source_id_idx on public.chunks (source_id);

-- --- messages: conversation memory -------------------------------------------
create table if not exists public.messages (
    id         bigserial   primary key,
    user_id    bigint      not null,
    role       text        not null,                  -- user | assistant
    content    text        not null,
    created_at timestamptz not null default now()
);
create index if not exists messages_user_created_idx
    on public.messages (user_id, created_at desc);

-- --- escalations: manager hand-offs ------------------------------------------
create table if not exists public.escalations (
    id              uuid        primary key default gen_random_uuid(),
    user_id         bigint      not null,
    question        text        not null,
    status          text        not null default 'open', -- open | taken | resolved
    manager_id      bigint,
    manager_msg_id  bigint,
    taken_at        timestamptz,
    resolved_at     timestamptz,
    resolution_text text,
    cooldown_until  timestamptz,                          -- per-user "bot muted until"
    created_at      timestamptz not null default now()
);
create index if not exists escalations_user_created_idx
    on public.escalations (user_id, created_at desc);

-- --- feedback: 👍 / 👎 --------------------------------------------------------
create table if not exists public.feedback (
    id               bigserial   primary key,
    user_id          bigint      not null,
    question         text        not null,
    answer           text        not null,
    rating           smallint    not null,               -- +1 | -1
    cited_source_ids jsonb       not null default '[]'::jsonb,
    created_at       timestamptz not null default now()
);

-- --- match_chunks: vector-only cosine search (RAG baseline, §11) --------------
-- similarity = 1 - cosine_distance; ORDER BY the distance (<=>) directly so the
-- HNSW index is used. Explicit search_path so <=> / vector resolve at call time.
create or replace function public.match_chunks(
    query_embedding vector(1024),
    match_count     int   default 5,
    min_similarity  float default 0.0
) returns table (
    id          uuid,
    source_id   uuid,
    chunk_index int,
    content     text,
    similarity  float,
    metadata    jsonb,
    filename    text
)
language sql
stable
set search_path = extensions, public, pg_temp
as $$
    select
        c.id,
        c.source_id,
        c.chunk_index,
        c.content,
        1 - (c.embedding <=> query_embedding) as similarity,
        c.metadata,
        s.filename
    from public.chunks c
    join public.sources s on s.id = c.source_id
    where s.status = 'active'
      and 1 - (c.embedding <=> query_embedding) >= min_similarity
    order by c.embedding <=> query_embedding
    limit match_count;
$$;

-- --- RLS: deny-by-default (the bot connects as the table owner / service_role,
--     both of which bypass RLS; this only locks down the public PostgREST API). §21
alter table public.sources     enable row level security;
alter table public.chunks      enable row level security;
alter table public.messages    enable row level security;
alter table public.escalations enable row level security;
alter table public.feedback    enable row level security;
