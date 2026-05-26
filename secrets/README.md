# secrets/

Reserved for any local key files. **P4_RAG keeps all secrets in environment
variables** (`.env` locally, Railway Variables in production) — API keys for
Anthropic, Voyage, Groq, Supabase `service_role`, and the Telegram token. There
is no service-account JSON to drop here in v1.

Everything in this folder except this README is gitignored (`.gitignore`:
`secrets/*` + `!secrets/README.md`). Never commit a real key.
