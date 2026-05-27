# Architecture — P4_RAG

Why each decision was made. The *what* (modules, columns, parameters) lives in
`project_specs.md` and `db/schema.sql`; this document is the reasoning a reviewer or a
future maintainer needs.

## The one idea: grounding is the product

A support bot that invents a price, a delivery window, or a warranty term is worse than
no bot — it creates liability and erodes trust. So every design choice serves one
constraint: **an answer must be traceable to a retrieved document, or it must not be
given.** Three mechanisms enforce it:

1. **Retrieval gate.** The question is embedded and run through hybrid search; if the
   best evidence is too weak (low cosine *and* no keyword match), the bot never calls the
   LLM — it escalates.
2. **Native citations.** Retrieved chunks are passed to Claude as `document` blocks with
   citations enabled, so cited spans are *guaranteed* to come from the context (we also
   post-verify the cited text exists in the chunk). The bot appends the source filename.
3. **Honest escalation.** "Не знаю, передам менеджеру" + a hand-off to a managers' chat
   beats a confident hallucination. A manager can *Take* the dialogue (the bot then stays
   silent for that user for a cooldown) or *Suggest* an answer that's relayed to the user.

## Why Claude has a co-processor stack (Voyage + Groq)

"Integrate Claude into RAG" is a trap: **Claude has no embeddings API.** Anthropic itself
recommends **Voyage AI**, so the system splits into three providers — generation = Claude
(Haiku 4.5, fastest/cheapest near-frontier), embeddings = Voyage (`voyage-3.5`, 1024-dim,
multilingual for RU/UK), speech-to-text = Groq Whisper. Each is the best free/cheap option
for its job; none is a default chosen by inertia.

A second Claude-specific constraint shaped the escalation design: **native citations and
structured output are mutually exclusive** in one call (verified during planning). Rather
than give up citations, the `needs_human` signal is a **system-prompt sentinel** — Claude
emits the exact token `[[ESCALATE]]` when the context can't answer, and the client maps it
to a flag. Citations stay on; the escalation signal rides alongside them.

## Why Supabase (pgvector + Postgres), not Chroma + SQLite

One managed Postgres holds **everything**: the `chunks` vectors (pgvector, HNSW cosine
index), conversation memory, escalations, and feedback. That collapses the brief's
two-store design into one and — critically — means **no persistent volume on Railway**
(the Chroma path would force volume management onto a stateless deploy). It also buys
Postgres full-text search for free, which powers WOW 1. Connection is via the **session
pooler** (the direct host is IPv6-only on the free tier) over `asyncpg`.

## Why hybrid search (BM25/FTS + RRF)

Vector search is strong on meaning but weak on *exact tokens* — SKUs, article numbers,
phone numbers, codes like `0-0-12`. Those are exactly what support users paste. So
retrieval runs two arms — pgvector cosine and Postgres `ts_rank_cd` over a generated
`tsvector` — and fuses them with **Reciprocal Rank Fusion** (`score = Σ 1/(60 + rank)`).
RRF needs no score normalisation between arms, which is why it's the standard fusion
choice. The gate was then made hybrid-aware: a low-cosine result with a strong keyword
match is **answered, not escalated** — otherwise WOW 1's wins would be thrown away by the
vector-only threshold.

## Error handling & idempotency

- **One bad input never crashes the bot.** Every handler wraps its external calls; a
  failed embedding, a Whisper error, or an unparseable upload logs and shows a friendly
  message — it does *not* reach the global handler. The global handler is reserved for
  genuinely unexpected exceptions: it sanitises (redacts secret-shaped strings, base64;
  never logs the message body), alerts the managers, and reports to Sentry.
- **Write-after-success.** A guard flag is written only *after* its side effect commits —
  `escalations.status='taken'` + cooldown flips only after the manager-take succeeds; an
  auto-learned FAQ is stored only after the embed + insert succeed. Transient failures
  self-heal on retry because no false "done" exists.
- **Double-clicks are no-ops.** Inline-button handlers transition state only from the
  expected prior state (e.g. *Take* only flips an `open` row) and swallow Telegram's
  "message is not modified". Feedback is an upsert keyed by (user, question, answer); a
  second auto-learn of the same Q→A is deduped by a content hash + unique index.

## AI as an enhancement layer

Voice is a UX nicety, not a dependency: a transcription failure falls back to "напишите
текстом" and the bot keeps working text-first. The same shared `answer_question` pipeline
serves both text and transcribed voice — voice is just a different front door.

## Cost & safety posture

`ANTHROPIC_MAX_TOKENS ≤ 1024` caps a single answer; the real hard stop is the Anthropic
**Workspace spend limit** (the console is the only true cap). Per-answer usage tokens are
logged; measured cost is ≈ **$0.0068/dialogue**. Secrets live only in `.env` / Railway
Variables; the webhook validates `X-Telegram-Bot-Api-Secret-Token`; the `service_role`
key is server-side only; the Q&A handler is private-chat-only so the managers' group is
never RAG-answered.

## System diagram

See the Mermaid flowchart in `README.md` (it mirrors `project_specs.md` §6): user →
(voice→Whisper) → cooldown gate → hybrid retrieve → RRF → grounding gate →
{Claude+citations | escalate} → answer with source + feedback; manager resolution →
auto-learn FAQ.
