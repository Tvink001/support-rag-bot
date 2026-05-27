# Supabase / Postgres schema — P4_RAG

Mirrors `db/schema.sql` (the authoritative DDL). One managed Postgres holds the vector
knowledge base **and** all bot state. `pgvector` lives in the `extensions` schema;
`vector(1024)` matches Voyage `voyage-3.5`. Apply once in the Supabase SQL editor
(idempotent: `create … if not exists` / `create or replace function`).

## `sources` — uploaded documents

| column | type | notes |
|---|---|---|
| `id` | uuid PK | `gen_random_uuid()` |
| `filename` | text | original name (or `FAQ: …` for auto-learned) |
| `file_type` | text | `pdf` · `docx` · `txt` · `faq` (WOW 2) |
| `uploaded_by` | bigint | Telegram user id |
| `uploaded_at` | timestamptz | `now()` |
| `chunk_count` | int | filled at ingest |
| `sha256` | text | content hash — dedup |
| `priority` | int | `0` normal, `100` auto-learned FAQ |
| `status` | text | `active` · `deleted` (soft delete) |

Partial unique index `sources_sha256_active_uidx (sha256) where status='active'` — one
active source per content hash (dedup + the WOW 2 double-tap guard).

## `chunks` — the knowledge base (vector + keyword)

| column | type | notes |
|---|---|---|
| `id` | uuid PK | |
| `source_id` | uuid FK→sources | `on delete cascade` |
| `chunk_index` | int | order within source |
| `content` | text | chunk text |
| `embedding` | vector(1024) | Voyage embedding |
| `fts` | tsvector | **generated** `to_tsvector('simple', content)` (2-arg = IMMUTABLE) |
| `token_count` | int | ~chars/4 estimate |
| `priority` | int | inherited from source |
| `metadata` | jsonb | `{page, char_start, char_end}` or `{type:"faq", …}` |
| `created_at` | timestamptz | |

Indexes: **HNSW** on `embedding` (`vector_cosine_ops`, `m=16, ef_construction=128`);
**GIN** on `fts`; btree on `source_id`. `'simple'` config is language-agnostic for
RU/UK/EN (Postgres has a `russian` config but no `ukrainian`).

## `messages` — conversation memory (§13)

`id bigserial PK · user_id bigint · role text(user|assistant) · content text ·
created_at timestamptz`. Index `(user_id, created_at desc)`. Read window =
`CONVERSATION_MEMORY_TURNS` rows (newest-first, reversed to chronological).

## `escalations` — manager hand-offs (§14)

`id uuid PK · user_id bigint · question text · status text(open|taken|resolved) ·
manager_id bigint · manager_msg_id bigint · taken_at · resolved_at · resolution_text ·
cooldown_until timestamptz · created_at`. The per-user "bot muted until" is
`cooldown_until` (state lives here, not in FSM, so it survives redeploys).

## `feedback` — 👍 / 👎 (§16)

`id bigserial PK · user_id bigint · question text · answer text · rating smallint(+1|-1)
· cited_source_ids jsonb · created_at`. Recorded as an **upsert** on
(user_id, question, answer) — a second tap updates the rating, never duplicates.

## Functions

- **`match_chunks(query_embedding vector(1024), match_count int, min_similarity float)`**
  — vector-only cosine search; `similarity = 1 - (embedding <=> query_embedding)`, ordered
  by the distance operator so the HNSW index is used.
- **`keyword_search(query_embedding vector(1024), query_text text, match_count int)`** —
  the FTS/BM25 arm (WOW 1): `ts_rank_cd(fts, websearch_to_tsquery('simple', query_text))`,
  filtered by `fts @@ …`; also returns each hit's cosine `similarity` so the hybrid gate
  has a real number for keyword-only hits. **RRF fusion is done in Python**
  (`bot/rag/rrf.py`, pure + unit-tested), not in SQL.

Both functions `set search_path = extensions, public, pg_temp` so `vector` / `<=>` resolve
at call time. The pool also runs `set search_path = public, extensions` on each connection.

## Row-Level Security

RLS is **enabled, deny-by-default** on all five tables (no public policies). The bot
connects as the table owner via the session pooler (and uses the `service_role` key
elsewhere) — both **bypass RLS** — so this only locks down the public PostgREST API.
