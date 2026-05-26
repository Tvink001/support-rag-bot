"""Admin: the admin guard (messages + callbacks) and the /delete confirm flow (§16)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

from bot.handlers.admin import AdminFilter, DeleteSourceCB, on_delete_confirm


async def test_admin_filter_allows_admins_blocks_others() -> None:
    guard = AdminFilter()
    assert await guard(SimpleNamespace(from_user=SimpleNamespace(id=111))) is True  # in conftest
    assert await guard(SimpleNamespace(from_user=SimpleNamespace(id=999))) is False
    assert await guard(SimpleNamespace(from_user=None)) is False


async def test_delete_confirm_removes_chunks_and_reports() -> None:
    db = AsyncMock()
    db.soft_delete_source.return_value = 12  # 12 chunks removed
    query = AsyncMock()
    query.message = SimpleNamespace(edit_text=AsyncMock())
    source_id = uuid4()

    await on_delete_confirm(
        query, callback_data=DeleteSourceCB(action="confirm", source_id=str(source_id)), db=db
    )

    db.soft_delete_source.assert_awaited_once()
    assert db.soft_delete_source.await_args.args[0] == source_id
    query.answer.assert_awaited_once()
    assert "12" in query.message.edit_text.await_args.args[0]  # count reported


async def test_delete_confirm_is_idempotent_when_already_deleted() -> None:
    db = AsyncMock()
    db.soft_delete_source.return_value = None  # already gone
    query = AsyncMock()
    query.message = SimpleNamespace(edit_text=AsyncMock())

    await on_delete_confirm(
        query, callback_data=DeleteSourceCB(action="confirm", source_id=str(uuid4())), db=db
    )

    query.answer.assert_awaited_once()
    assert "уже" in query.message.edit_text.await_args.args[0].lower()
