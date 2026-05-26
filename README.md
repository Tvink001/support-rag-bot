# P4_RAG — Claude RAG FAQ Telegram bot

> 🚧 **Built via `prompts.md` (Step 0 → Prompt 12).** This README is a skeleton;
> the portfolio case study is written in the final prompt with real eval numbers.

An AI knowledge-base assistant for Telegram: it answers customer questions
**strictly from a company's uploaded documents** (PDF / DOCX / TXT) using
Retrieval-Augmented Generation, **cites its sources**, never invents facts, and
escalates genuinely hard questions to a human manager.

## Stack

- **Python 3.11 + aiogram 3.x**
- **Claude Haiku 4.5** — grounded answer generation (native citations + structured output)
- **Voyage AI** — embeddings (Claude has no embeddings API; Voyage is Anthropic's recommendation)
- **Supabase (pgvector + Postgres)** — vectors + conversation memory + escalations + feedback
- **Groq Whisper-large-v3-turbo** — voice input (free tier)
- **Railway** — deploy (webhook; no persistent volume — Supabase is managed)

## WOW features

1. **Hybrid search (BM25 + RRF)** — vector + Postgres full-text, fused via Reciprocal Rank Fusion.
2. **Auto-learn FAQ from manager** — a manager's resolution can be saved into the KB in one click.

## Docs

- `project_specs.md` — single source of truth (architecture, data model, RAG pipeline, quality gates)
- `prompts.md` — the atomic build sequence
- `full_pipeline.md` — operator guide (Russian)
- `docs/architecture.md` — design rationale
- `docs/supabase-schema.md` — data model

_Sample environment: see `.env.example`._
