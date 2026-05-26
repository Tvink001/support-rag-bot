# Supabase / Postgres schema — P4_RAG

> 🚧 Placeholder — expanded in Prompt 12 to mirror the final `db/schema.sql`.
> Authoritative design lives in `project_specs.md` §7; DDL in `db/schema.sql`
> (created in Prompt 3).

Tables (column-by-column detail filled at the end):

- **`sources`** — uploaded documents: `id, filename, file_type (pdf|docx|txt|faq),
  uploaded_by, uploaded_at, chunk_count, sha256, priority, status`.
- **`chunks`** — the knowledge base: `id, source_id, chunk_index, content,
  embedding vector(VOYAGE_EMBED_DIM), fts tsvector, token_count, priority,
  metadata jsonb, created_at`. Indexes: HNSW on `embedding` (cosine), GIN on `fts`.
- **`messages`** — conversation memory: `id, user_id, role, content, created_at`.
- **`escalations`** — manager hand-offs: `id, user_id, question, status,
  manager_id, manager_msg_id, taken_at, resolved_at, resolution_text,
  cooldown_until, created_at`.
- **`feedback`** — 👍/👎: `id, user_id, question, answer, rating, cited_source_ids,
  created_at`.

Functions: `match_chunks(query_embedding, match_count, min_similarity)` (vector),
`hybrid_search(query_embedding, query_text, match_count, ...)` (vector + FTS via
RRF). Index params + RLS posture finalized in Prompt 1 / Prompt 3.
