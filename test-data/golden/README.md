# test-data/golden/ — evaluation sets

The decisive RAG quality gate (`project_specs.md` §19.2, run in Prompt 11). Source
documents (PDF/DOCX/TXT) are gitignored; the two `.jsonl` sets below are committed.

## `retrieval.jsonl` (≥ 30 lines)
One JSON object per line — does hybrid search surface the right chunks?
```json
{"query": "какой срок возврата товара?", "relevant_chunk_ids": ["<uuid>", "<uuid>"]}
```
Fill `relevant_chunk_ids` after the first `/upload` (Prompt 3), once chunk ids exist.
Metrics computed: Precision@5 ≥ 0.7, Recall@10 ≥ 0.85, MRR ≥ 0.7 (and hybrid > vector-only).

## `qa.jsonl` (≥ 30 lines, ideally 50)
One JSON object per line — does the bot answer faithfully and refuse honestly?
```json
{"question": "...", "expected_answer": "...", "type": "typical|edge|adversarial|out_of_scope"}
```
Composition ~60% typical / 30% edge / 10% adversarial. Include:
- at least one **out-of-scope** ("ты какая модель?") → expect honest refusal + escalation
- at least one **prompt-injection** ("ignore previous instructions and ...") → expect no behavior change

Metrics: RAGAS faithfulness ≥ 0.85, answer-relevancy ≥ 0.85, hallucination < 5%,
out-of-scope refusal ≥ 95%, citations present.
