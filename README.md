# Document-Grounded FAQ Bot
> A Telegram support bot that answers strictly from a company's uploaded documents (PDF / DOCX / TXT), cites its sources on every reply, never invents facts, and hands genuinely hard questions to a human manager.

**Live demo:** [LIVE_DEMO_URL]

![demo](docs/screenshots/demo.gif)
<!-- ~90 s demo: /upload → grounded cited answer → voice question → out-of-KB escalation → manager Take → 👍 -->

## Overview

Support teams spend most of their day answering the same questions out of the same documents. A naive LLM "wrapper" cheerfully invents answers when the knowledge base doesn't cover the question, which is the exact failure mode that erodes trust. This bot inverts that: it only answers from retrieved document chunks, cites every claim back to the source span, and when retrieval fails it escalates to a managers' chat instead of guessing. When a manager resolves an escalation, that human answer is saved back to the knowledge base as a high-priority chunk — so the next identical question is answered by the bot, not a human.

## Key Features

- **Grounded answers only.** Every reply quotes the source span and attaches a "Источник" footer with the file name and page; if the retrieved chunks don't contain the answer, the bot says so honestly instead of guessing.
- **Hybrid retrieval (BM25 + vector + RRF).** Vector cosine catches semantically similar wording; Postgres full-text (`ts_rank_cd`) catches exact tokens (SKUs, article numbers, phone numbers like `0-0-12`); Reciprocal Rank Fusion at `k=60` combines them.
- **Escalation to a managers' chat** with two inline actions: **Take** (the manager replies in private, the bot stops responding for that user for the cooldown window) and **Suggest** (manager drafts a reply that the bot delivers verbatim).
- **Auto-learn from manager resolutions.** When a manager takes or suggests, the resulting Q→A pair is saved to the KB as a high-priority chunk; the next user asking the same question gets the bot's answer.
- **Voice questions** via Groq Whisper Large v3 Turbo — transcribed in place, processed identically to text.
- **Conversation memory** — last N turns of the user's dialogue are passed to the model so follow-ups like "and the price?" resolve correctly.

## Tech Stack

**Language + bot framework**
- Python 3.11
- aiogram 3.28 (webhook in production, polling in development) — FSM, middleware, CallbackData

**Generation + embeddings**
- Anthropic Haiku 4.5 — grounded generation with native citations on `document` blocks
- Voyage `voyage-3.5` — 1024-dim multilingual embeddings (RU/UK/EN)
- Groq `whisper-large-v3-turbo` — voice transcription, free tier

**Storage**
- Supabase (Postgres + pgvector) — vectors *and* state (memory, escalations, feedback) in one managed store
- HNSW index on the embedding column + GIN index on the FTS column

**Runtime + ops**
- Railway with Docker, webhook mode, `/health` for healthcheck
- Sentry for unhandled exceptions
- `ruff` + `mypy` strict + `pytest` (65 tests covering pure logic + handler integration with mocked I/O)

## Architecture Highlights

**1. Hybrid retrieval with RRF, not a vector-only baseline.** Vector cosine misses exact tokens — SKUs, article numbers, phone numbers like `0-0-12`. The keyword arm (Postgres FTS via `ts_rank_cd`) catches those, and RRF fusion at `k=60` produces a single ranked list per query. In golden-set evaluation the hybrid ranking lifts MRR from 0.861 (vector only) to 0.878 — small in the headline number, but it's specifically the type of question where pure vector silently fails.

**2. Grounding via native document-block citations.** Claude's `document` content blocks support inline citations: the model returns text spans tagged with the source chunk they were drawn from, and the bot renders those as "Источник: …" lines below the answer. Because citations and structured output are mutually exclusive on the Anthropic API, the `needs_human` escalation signal is encoded as a system-prompt **sentinel** (`[[ESCALATE]]`) in the response — parsed deterministically, no JSON schema, no failure mode of "schema validation passed but answer was bad".

**3. Auto-learn loop closes the retrieval gap.** Every manager resolution (Take or Suggest) creates a new high-priority chunk in the KB tagged with the original question, the manager's answer, and a higher boost score than ingested document chunks. The next user asking that question retrieves the synthetic chunk first, so the bot can answer without escalating. Retrieval gaps are closed by humans, but only once each.

**4. One Postgres for vectors AND state.** Supabase's `pgvector` extension means the same database holds the vector index, the conversation memory table, the escalations queue, and the feedback log. No second store (Chroma + SQLite + Redis...) means no cross-store consistency problem on escalation cleanup, no extra deploy surface on Railway, and no persistent volume needed.

**5. Voice and text on the same retrieval path.** The Groq Whisper transcription writes directly into the same handler that text messages hit; there's no parallel pipeline for voice. The voice path is half a network call longer than text, never a separate code path that could drift.

## Status

Case study / portfolio project. Quality gate locked behind a golden-set evaluation (`test-data/run_eval.py`) measuring faithfulness, answer relevancy, out-of-scope refusal, citations present, recall@10, MRR, cost-per-dialogue, and prompt-injection resistance. The "ТехноХаб" knowledge base in the demo is fictional.
