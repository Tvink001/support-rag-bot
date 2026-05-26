# learnings.md — P4_RAG

> Running log of project-specific patterns, gotchas, and reusable solutions.
> Future projects grep this file by tag.

---

## Format

Each entry:

```
### YYYY-MM-DD — Short title — #tag #tag

3–10 lines explaining the discovery. Lead with the lesson, not the story.
Include the load-bearing code/config snippet if there is one.
```

Tags in use:
`#anthropic` `#voyage` `#supabase` `#pgvector` `#rag` `#aiogram` `#groq`
`#async` `#context7` `#cost` `#security` `#error-handling` `#idempotency`
`#windows` `#mcp` `#debugging` `#portfolio-polish` `#blocker`

## Maintenance

Operator (Artem💜) maintains this file. Claude Code suggests new entries at the
end of every prompt; the operator decides whether to accept, edit, or drop them.
Append-only — if a previous entry turns out wrong, add a corrective entry below
referencing the original by date+title rather than rewriting.

---

## 2026-05 — Cross-project seeds (carried from P1 / P2 / P3)

These cost real debug time earlier; encoding here prevents paying again.

### 2026-05-26 — Brief-specified library/model versions are timestamps, not specs — #context7
P4.md targets `gpt-4o-mini`, OpenAI embeddings, Chroma — all 2024-era choices.
Treat every version/model/library string in a brief as "a sample from when it was
written", not a requirement. Run Context7 before pinning anything in
`pyproject.toml` or quoting a model id / price / dimension. (Same lesson from P1
"Claude 3.5"→4.x and P2 "APScheduler v3"→v4.)

### 2026-05-26 — Never use sync I/O directly in an async handler — #async
The single most common production bug in async Python bots: a blocking call
(sync SDK, `requests`, file read) inside an async handler stalls the event loop
for every user at once. Every Voyage / Anthropic / Supabase / Groq call goes
through a native async client or `await asyncio.to_thread(...)`. A light load
test (~100 concurrent questions via `asyncio.gather`) catches a missed wrap.

### 2026-05-26 — Write-after-success idempotency — #idempotency
Don't write a "done"/guard flag before the side effect; write it after the side
effect commits. In P4: `escalations.status=taken` + `cooldown_until` flips only
after the manager-take callback succeeds; an auto-learned FAQ chunk is written
only after the Voyage embed + upsert succeed. Transient failures self-heal on
retry because no false "done" exists. (P1/P2 pattern.)

### 2026-05-26 — Don't throw on partial / user-side failures — #error-handling
A failed Whisper transcription, an unparseable upload page, a below-threshold
retrieval are USER-FLOW events, not bugs. Log + show a friendly message + stay in
flow. Only genuinely unexpected exceptions go to the global error handler →
sanitized manager alert + Sentry. Throwing on user-side noise spams the error
channel (P1 lesson: the `anyUpdate` keystroke storm).

### 2026-05-26 — callback_data is bytes (max 64), use the CallbackData factory — #aiogram
Never build `callback_data` from raw f-strings on user-controlled values. Use
aiogram's `CallbackData` factory (`FeedbackCB`, `EscalateCB`, `SaveFaqCB`,
`DeleteSourceCB`). The 64-byte budget is bytes, not chars — ASCII ids are safe;
Cyrillic/emoji in callback_data eats the budget fast (use opaque ids keyed to a
table instead). (P3 #telegram.)

### 2026-05-26 — Production FSM storage is never MemoryStorage — #aiogram
`MemoryStorage` loses all state on every redeploy. Production uses a persistent
backend (Redis or a Postgres/Supabase-backed storage). For P4 the per-user
escalation cooldown lives in Postgres (`escalations.cooldown_until`), not FSM, so
it survives restarts regardless. Confirm the storage choice in Prompt 1/2.

### 2026-05-26 — `.mcp.json` on Windows + corporate TLS — #mcp #windows
From P1/P2/P3: (1) `${VAR}` does NOT resolve from `.env` on Windows PowerShell —
inline values, gitignore the file, keep a template in `project_specs.md` §4;
(2) `npx` can 30 s-timeout the MCP startup — install Context7 globally and point
at the `.cmd` shim if so; (3) corporate TLS interception breaks Node HTTPS with
`UNABLE_TO_VERIFY_LEAF_SIGNATURE` — the secure fix is `NODE_USE_SYSTEM_CA=1` in
the server's env block (NOT `NODE_TLS_REJECT_UNAUTHORIZED=0`). P4 only needs
Context7, so the surface is small.

### 2026-05-26 — Telegram editMessageText "message is not modified" = 400, treat as no-op — #telegram #error-handling
Double-clicking an inline button (Take / Save-as-FAQ) makes the handler try to
edit a message to its already-current state → Telegram 400 "message is not
modified". This is not a real error — the desired state already holds. Catch and
ignore it (or guard on current state) so it doesn't cascade into the error path.

---

## 2026-05-26 — P4 pre-build seeds (Context7-verified during architecture research)

### 2026-05-26 — Claude has NO embeddings API — Anthropic recommends Voyage AI — #anthropic #voyage #rag #context7
THE load-bearing fact for this project. Verified via Context7
`/websites/platform_claude_en` (build-with-claude/embeddings): *"Anthropic does
not offer its own embedding model. Voyage AI is recommended as an embeddings
provider."* So "integrate Claude into RAG" splits into three layers and only one
is Claude: **generation = Claude** (Haiku 4.5); **embeddings = Voyage** (separate
provider, separate key); **speech-to-text = Groq Whisper** (separate again).
The brief's "embeddings via OpenAI or Claude" is impossible for the Claude half.
Chose Voyage over OpenAI: Anthropic's own recommendation, multilingual RU/UK,
has a free tier (OpenAI has no real free API tier — P2 finding).

### 2026-05-26 — Claude Haiku 4.5 model id; legacy 3.5 ids retired — #anthropic #context7
Verified via `/websites/platform_claude_en` (api + migration guide): alias
`claude-haiku-4-5`, dated `claude-haiku-4-5-20251001`. **Legacy
`claude-3-5-haiku-20241022` is no longer supported.** Haiku 4.5 = fastest +
cheapest Claude with near-frontier quality — the right model for FAQ generation.
Confirm current input/output pricing in Prompt 1 before quoting cost numbers.

### 2026-05-26 — Anthropic structured output = output_config.format (json_schema), NOT response_format — #anthropic #context7
The modern way to force a JSON shape (e.g. our `needs_human` escalation boolean)
is `output_config: {format: {type: "json_schema", schema: {...}}}` — not the
legacy `response_format: "json_object"`. Example pattern (harmlessness screen)
returns a constrained `{is_harmful: boolean}`; ours returns `{needs_human:
boolean, ...}`. Strict tool-use (`tool_choice` + `input_schema`, `strict: true`)
is the alternative. Still validate the parsed JSON with Pydantic (defense in depth).

### 2026-05-26 — Anthropic native citations: document blocks + cache_control — #anthropic #rag #context7
Verified via `/websites/platform_claude_en` (build-with-claude/citations). Pass
each retrieved chunk as a `document` content block with `"citations": {"enabled":
true}`; Claude returns answers whose cited spans are GUARANTEED to come from the
provided context — far stronger than asking it to cite in free text. Combine with
`"cache_control": {"type": "ephemeral"}` on stable content. OPEN QUESTION (OQ-2):
whether citations compose with `output_config.format` in one call — if not, the
answer call uses citations and the escalation signal comes from the retrieval gate
or a sentinel. Resolve in Prompt 1.

### 2026-05-26 — Prompt caching: cache the system prompt, NEVER the retrieved chunks — #anthropic #cost
Retrieved chunks change every query → caching them yields 0% hit and pays the
cache-write surcharge N times. Put `cache_control: ephemeral` on the (stable)
system prompt only. Min cacheable prefix differs by model (Opus 4.x = 4096;
Sonnet/older = 1024; **Haiku 4.5 threshold to confirm in Prompt 1** — if our
system prompt is below it, caching silently does nothing: check
`usage.cache_creation_input_tokens` on the first call). Default cache TTL is
short — set an explicit `ttl` for longer-lived prefixes.

### 2026-05-26 — Embedding dimension freezes the pgvector column — verify Voyage model FIRST — #voyage #pgvector
`vector(N)` in `db/schema.sql`, `VOYAGE_EMBED_DIM` in `.env`, and the chosen
Voyage model's output dimension must all match. Changing the model later means
re-ingesting the entire KB (old vectors are incompatible with new query vectors).
So OQ-1 (model + dimension) is resolved in Prompt 1, before any schema or ingest.
Voyage docs ID: `/websites/voyageai`. Use `input_type="document"` at ingest and
`input_type="query"` at retrieval.

### 2026-05-26 — Supabase pgvector + Postgres FTS hybrid via RRF is the recommended pattern — #supabase #pgvector #rag
For this budget tier the Quality-checklist recommends Supabase: pgvector cosine
(`<=>`, HNSW index `vector_cosine_ops`) for the vector arm + Postgres `tsvector`/
`ts_rank` (GIN index) for the keyword/BM25 arm, fused via Reciprocal Rank Fusion
(`score = Σ 1/(k + rank_i)`, k=60). One managed DB also holds conversation memory,
escalations, feedback — so **no persistent volume is needed on Railway** (unlike
the Chroma brief). Confirm index type/params (HNSW vs IVFFlat) and whether to
query via supabase-py RPC or direct `asyncpg` on `DATABASE_URL` in Prompt 1
(OQ-4, OQ-6). IDs: `/supabase/supabase-py`, `/llmstxt/supabase_llms-full_txt`.

### 2026-05-26 — Groq Whisper-large-v3-turbo for voice (carryover from P2) — #groq #whisper
Reuse P2's `WhisperService`: `AsyncGroq` →
`audio.transcriptions.create(model="whisper-large-v3-turbo", file=(name, bytes,
"audio/ogg"), language="ru"/"uk", response_format="text")`. Permanent free tier
(no card), v3 model materially better on RU/UK than OpenAI `whisper-1` (v2),
OpenAI-compatible API. Telegram voice is OGG/OPUS — Groq accepts it natively.
Enforce a ~1 MB cap at the handler (a question isn't a podcast). Re-verify the
SDK signature + current model id via Context7 `/groq/groq-python` before pinning.

### 2026-05-26 — RAG quality gates (from Quality checklist §6.6 / Production-readiness Type 6) — #rag #portfolio-polish
The numbers that gate shipping P4 (project_specs.md §19.2): retrieval Precision@5
≥ 0.7, Recall@10 ≥ 0.85, MRR ≥ 0.7; RAGAS faithfulness ≥ 0.85, answer-relevancy
≥ 0.85, context-precision ≥ 0.7; hallucination rate < 5%; out-of-scope refusal
≥ 95%; citations on every grounded answer; adversarial/prompt-injection tests
pass; hybrid must beat vector-only on the golden set; cost/100 dialogues ≤ $0.20;
p95 answer < 5 s. Build golden sets (≥30 retrieval queries w/ relevant_chunk_ids,
≥30 Q&A pairs; ~60% typical / 30% edge / 10% adversarial). These are NOT optional
for a RAG bot — they ARE the portfolio differentiator vs a "GPT wrapper".

### 2026-05-26 — Prompt-injection defense: retrieved docs are data, not instructions — #security #rag
A KB document can contain "ignore previous instructions and ...". Defenses
(project_specs.md §21): wrap retrieved content in `document` blocks / delimited
tags; the system prompt explicitly says to treat document content as data and
ignore any instructions inside it; sanitize chunks at ingestion. There MUST be an
adversarial golden-set case proving an injected payload does not change behavior.

### 2026-05-26 — Hard cost cap = Anthropic Workspace spend limit (the only real stop) — #cost #anthropic
`ANTHROPIC_MAX_TOKENS` caps a single answer, but the only HARD stop against a
runaway loop is the Workspace spend limit set in the Anthropic console ($20/mo,
alert $10). (OpenAI removed hard caps — soft alerts only; one runaway loop can be
$500+/night, no refund.) Set it in Step 0.4 before the first API call. Log
`usage.input_tokens`/`output_tokens` per call and alert if daily spend > 2× avg.

---

_(Entries below this line are added as the build progresses — one short entry per
surprise, dated and tagged, after each completed prompt.)_

### 2026-05-26 — Step 0 smoke tests: creds live-verified; 2 facts pinned — #context7 #anthropic #voyage #groq #supabase
Ran live API smoke tests against the filled `.env` before any code:
- **Anthropic OK** — `claude-haiku-4-5` resolves live to `claude-haiku-4-5-20251001`
  (the API echoed the dated id); key valid. Confirms §2.4 / OQ model id.
- **Voyage OK** — `voyage-3.5` returns **dim = 1024** → live-confirms
  `VOYAGE_EMBED_DIM=1024` and the `vector(1024)` column. OQ-1 de-risked (still
  confirm voyage-3.5 is the best RU/UK option in Prompt 1; it supports other
  output dims too).
- **Groq OK** — `whisper-large-v3-turbo` present. **Supabase REST OK** — HTTP 200
  with the new `sb_secret_...` key. **Telegram token OK** (@P4RAG_302_bot).
- **Pending:** bot not yet reachable in the managers' group (getChat → "chat not
  found", getUpdates empty) — add bot to group, send `/cmd@P4RAG_302_bot`, re-read
  `chat.id`. `WEBHOOK_SECRET` + `DATABASE_URL` still hold `.env.example`
  placeholders (fine for local polling; fill before the webhook deploy / asyncpg path).
- Bot privacy mode is ON (`can_read_all_group_messages:false`) — matters for
  capturing a manager's free-text reply (escalation §14 / WOW 2 §18): design around
  replies-to-the-bot, or `/setprivacy → Disable` in BotFather.

### 2026-05-26 — This machine: native curl/Schannel blocks OCSP revocation → use `--ssl-no-revoke` — #windows #tls #debugging
The Bash tool's `curl` is Windows Schannel; plain HTTPS fails with
`CRYPT_E_NO_REVOCATION_CHECK (0x80092012)` (can't reach OCSP/CRL — corporate/AV
interception). Fix for curl smoke tests: add `--ssl-no-revoke`. For the Node MCP:
`NODE_USE_SYSTEM_CA=1` (already in `.mcp.json`). Python SDKs (anthropic/voyage/
groq/supabase) use OpenSSL+certifi, not Schannel, so they're usually unaffected —
but if a local run in Prompt 2+ throws SSL errors, the analog fix is
`SSL_CERT_FILE`/certifi or the `truststore` package. (Same root cause as P3 learnings.)

### 2026-05-26 — Prompt 1: citations × structured output are MUTUALLY EXCLUSIVE (OQ-2) — #anthropic #rag #context7
Anthropic docs: enabling citations on a document AND `output_config.format` in one
call → API error. The answer call uses **citations only**; `needs_human` comes from
the retrieval gate + a system-prompt sentinel, never structured output. (§9.2/§12.)

### 2026-05-27 — Prompt 1/2: pin versions via live pip in the TARGET Python (3.11) — #context7
Local python is 3.14; resolving pins there risks older versions for C-ext pkgs. Use
`py -3.11` venv → pip-resolve current stable, validate via `pip install -e .[dev]` +
gates. 2026-05 currents: aiogram 3.28.2, anthropic 0.104.1, voyageai 0.3.7, supabase
2.30.0, asyncpg 0.31.0, pgvector 0.4.2, pydantic 2.13.4, pydantic-settings 2.14.1,
mypy 2.1.0, pytest 9.0.3, pypdf 6.12.2, python-docx 1.2.0, truststore 0.10.4.
voyageai stubs fight mypy --strict (no_implicit_reexport on `Client`; float|int union
on `.embeddings`) → `[[tool.mypy.overrides]] module=["voyageai*"] follow_imports="skip"`.

### 2026-05-27 — Supabase connection saga: session pooler + SNI + flaky DNS — #supabase #async #tls #debugging
(1) Direct host `db.<ref>.supabase.co` is IPv6-ONLY on free tier → use the **session
pooler** (`aws-1-<region>.pooler.supabase.com:5432`, user `postgres.<ref>`); session
mode supports asyncpg prepared statements (transaction mode 6543 does not).
(2) Supavisor routes by **SNI** — connecting to the pooler by raw IP fails auth with a
MISLEADING "password authentication failed"; always connect by hostname.
(3) This machine's DNS intermittently fails (WSANO_RECOVERY / errno 11003) for minutes
→ pinned the pooler host in the Windows hosts file. asyncpg uses `ssl="require"` +
connect-retry. PostgREST can't run a raw `SELECT 1` → the connectivity probe uses asyncpg.

### 2026-05-27 — asyncpg + pgvector text literal; generated tsvector; HNSW — #pgvector #async
Insert/query vectors as the text literal `'[...]'::vector` (no numpy/codec). Run
`SET search_path = public, extensions` on each pooled conn (pool `init=`) so
unqualified `vector` / `<=>` resolve regardless of pgvector's schema. Generated FTS
column needs the **2-arg** `to_tsvector('simple', content)` (IMMUTABLE; 'simple' =
language-agnostic for RU/UK). HNSW (`m=16, ef_construction=128`) = Supabase default.

### 2026-05-27 — Pure chunker = char/4 token estimate, boundary-aware window — #rag
Don't call a tokenizer API in the chunker (keep it pure/offline/testable). chars/4
estimate + snap to paragraph/line/sentence/space, guaranteed non-zero overlap, exact
char offsets. Fine below Voyage's 32k context limit.

### 2026-05-27 — aiogram: register set_my_commands; commands are case-sensitive — #aiogram #debugging
Without `set_my_commands` there's no "/" autocomplete → users type blind. Commands are
**case-sensitive**: "/Sources" ≠ "/sources" → `Update is not handled` (silent). Register
a `BotCommand` menu at startup. Plain text with no matching handler is also "not
handled" — expected until the chat handler exists.

### 2026-05-27 — Prompt 4: truststore TLS, SDK retry, SKU→hybrid, real cost — #tls #anthropic #cost #rag
(1) Corporate TLS interception breaks Voyage/Anthropic (certifi) intermittently →
`import truststore; truststore.inject_into_ssl()` at startup (OS trust store has the
corporate CA; harmless on Railway/Linux). asyncpg (`ssl="require"`) was already immune.
(2) The anthropic SDK's built-in `max_retries` does exp backoff + honors `retry-after`
on 429/529 — no tenacity needed. (3) Vector-only retrieval misses SKUs/article numbers
(`TH-2003` → cosine 0.416 < 0.6 gate) → WOW 1 hybrid (Prompt 9) fixes it. (4) Real cost
≈ **$0.0076/answer** (in≈6.5k / out≈220 tok), under the $0.02 cap; system-prompt caching
did NOT engage (`cache_creation_input_tokens=0` → the ~400-token prompt is below Haiku
4.5's min cacheable length — confirms the §9.2 gap). (5) Voyage free tier = **3 RPM**
without a payment method.

### 2026-05-27 — Prompt 5: memory turns are plain text; document blocks only in the current turn — #anthropic #rag #context7
Context7 (`/websites/platform_claude_en`, working-with-messages): the Messages array
is alternating `{"role","content"}` turns. For RAG-with-memory, prior turns are
**plain-text strings**; the retrieved `document` blocks (+ `citations`) go **only in
the final/current user turn**, and the system prompt stays the top-level `system`
param (so the ephemeral cache prefix isn't disturbed by history). Consecutive
same-role messages are merged server-side. Loading the last N rows newest-first then
`reversed()` gives chronological order for the prompt.

### 2026-05-27 — Prompt 5: feedback callback can't carry Q/A — recover server-side, upsert for idempotency — #aiogram #idempotency
👍/👎 buttons can't stuff the question/answer into the 64-byte callback budget. Pattern:
persist the assistant turn → use its `messages.id` as the only callback payload
(`FeedbackCB(rating, msg_ref)`), then recover (user, question, answer) from `messages`
on tap (survives restarts). Idempotency = **upsert keyed by (user_id, question,
answer)** (a 2nd tap updates the rating, never duplicates) PLUS removing the keyboard
after the first tap (swallow "message is not modified"). Store the cited source
**filenames** (parsed from the visible "Источник: …" footer) in `cited_source_ids` —
more useful for the analytics loop than opaque UUIDs, and footer-parse needs no extra
store. `InlineKeyboardBuilder().button(callback_data=<CB instance>)` packs for you.

### 2026-05-27 — Prompt 6: needs_human via a text sentinel (citations block structured output) — #anthropic #rag
OQ-2 says native citations and `output_config.format` can't combine, so there's no
JSON `{needs_human: bool}` next to a cited answer. Workaround: the system prompt tells
Claude to emit EXACTLY `[[ESCALATE]]` (a constant `ESCALATION_SENTINEL`) when the
retrieved context can't answer; the client does `needs_human = SENTINEL in text` and
blanks the visible text. Clean, cheap, and keeps the answer call citations-only. The
rule-4 "ты какая модель?" override still wins (it answers, doesn't escalate).

### 2026-05-27 — Prompt 6: restrict the Q&A handler to private chats; manager FSM in a group — #aiogram #telegram
With the bot admin in the managers' group (privacy disabled for §18/WOW2), EVERY group
message would otherwise hit the RAG handler → noise + cost. Fix: gate the chat handler
with `F.chat.type == "private"`. The manager "Предложить ответ" reply-capture uses
aiogram FSM (`ManagerFlow.awaiting_suggestion`) — FSM keys on (chat_id, user_id), so a
group state is per-manager and the **state filter** prevents it catching ordinary users.
Per-user escalation cooldown lives in Postgres (`escalations.cooldown_until`), not FSM,
so it survives redeploys. `from aiogram.utils.text_decorations import html_decoration`
gives `.quote()` for safe HTML in manager posts; re-edit from `message.html_text`.

### 2026-05-27 — Prompt 7: Groq transcription return shape + reuse the pipeline for voice — #groq #whisper #async
`AsyncGroq` is natively async — `await client.audio.transcriptions.create(...)`, NO
`asyncio.to_thread` (unlike the sync Voyage client). Signature (Context7
`/groq/groq-python`): `model="whisper-large-v3-turbo"`, `file=(name, bytes,
"audio/ogg")` (3-tuple; Telegram voice is OGG/OPUS), `response_format="text"`,
optional `language`. The SDK types `create() -> Transcription`, but with
`response_format="text"` it can return a **plain string** — handle both:
`raw if isinstance(raw, str) else getattr(raw, "text", "")`, then `.strip()`. To feed
voice through the SAME RAG path, refactor the text handler into a shared
`answer_question(message, *, question, …)` that both the `F.text` and `F.voice`
handlers call — no pipeline duplication. Transcription failure = user-flow event:
friendly fallback, never raise to the global handler.
