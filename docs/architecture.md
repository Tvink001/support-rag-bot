# Architecture — P4_RAG

> 🚧 Placeholder — written in Prompt 12 for a portfolio audience (other
> engineers, hiring reviewers). Each section answers **why**, not **what**.
> The "what" lives in `project_specs.md`.

Planned sections (see `project_specs.md` §24):

- **Why RAG + citations** — grounding is the product; a bot that invents facts is
  worse than no bot. Native Claude citations make every answer traceable to a source.
- **Why Voyage for embeddings** — Claude has no embeddings API (Anthropic
  recommends Voyage); the stack stays Anthropic-aligned and multilingual (RU/UK).
- **Why Supabase (pgvector + Postgres)** — one managed store for vectors + state;
  RLS, backups, SQL analytics; eliminates the persistent-volume management the
  Chroma path would force on Railway.
- **Why hybrid search (BM25 + RRF)** — vector recall + keyword precision on rare
  terms / article numbers; measurable lift over vector-only on the golden set.
- **Grounding & escalation philosophy** — honest "I don't know" + human hand-off
  below a similarity threshold beats a confident hallucination.
- **AI as an enhancement layer** — voice (Groq Whisper) is a UX nicety; the bot
  works text-first if it's down.
- **Error handling & idempotency** — per-message try/except; write-after-success
  ordering; sanitized error channel; double-click-safe callbacks.
- **System diagram** — Mermaid (mirrors `project_specs.md` §6).
