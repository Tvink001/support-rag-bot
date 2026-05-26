"""Async Postgres connectivity for Supabase (pgvector + state tables).

Transport (per project_specs.md §9.4 / OQ-4): supabase-py async ``rpc()`` is the
default for hybrid search; raw SQL uses ``asyncpg`` over ``DATABASE_URL`` with the
**session** pooler (port 5432). PostgREST cannot execute a raw ``SELECT 1``, so
the connectivity probe below uses ``asyncpg`` directly. The connection pool for
retrieval is added in the retrieval prompt; this module only proves creds today.

Connects are retried with backoff: transient ``OSError`` (DNS ``gaierror``,
refused/reset, timeout) is common on intercepted corporate networks and as
short-lived blips in the cloud. Non-transient errors (e.g. bad password →
``asyncpg.PostgresError``) propagate immediately.
"""

import asyncio
import logging

import asyncpg

logger = logging.getLogger(__name__)

_CONNECT_RETRIES = 5
_CONNECT_TIMEOUT_SECONDS = 10.0
_CONNECT_BACKOFF_SECONDS = 1.0  # linear backoff: 1+2+3+4s ≈ 10s total budget


async def ping(database_url: str) -> None:
    """Open a short-lived connection and run ``SELECT 1`` to prove DB creds work.

    Uses ``ssl="require"`` (encrypt without strict cert verification) so it works
    against the Supabase pooler from any network. Retries transient network/DNS
    failures; the final failure (or any non-transient error) propagates.
    """
    for attempt in range(1, _CONNECT_RETRIES + 1):
        try:
            conn = await asyncpg.connect(
                database_url, ssl="require", timeout=_CONNECT_TIMEOUT_SECONDS
            )
            try:
                value = await conn.fetchval("SELECT 1")
                if value != 1:
                    raise RuntimeError(f"Unexpected SELECT 1 result: {value!r}")
                return
            finally:
                await conn.close()
        except OSError as exc:
            logger.warning(
                "DB connect attempt %d/%d failed (transient): %s",
                attempt,
                _CONNECT_RETRIES,
                exc,
            )
            if attempt >= _CONNECT_RETRIES:
                raise
            await asyncio.sleep(_CONNECT_BACKOFF_SECONDS * attempt)
