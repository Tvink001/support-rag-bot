"""Feedback buttons: one row per answer, idempotent on double-tap, footer parsing (§16)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

from bot.handlers.feedback import FeedbackCB, _parse_cited_sources, handle_feedback
from bot.models import FeedbackContext


class _FakeDB:
    """In-memory feedback store with the same upsert semantics as the real method."""

    def __init__(self, context: FeedbackContext | None) -> None:
        self._context = context
        self.rows: list[dict[str, object]] = []

    async def get_feedback_context(self, assistant_msg_id: int) -> FeedbackContext | None:
        return self._context

    async def record_feedback(
        self,
        *,
        user_id: int,
        question: str,
        answer: str,
        rating: int,
        cited_source_ids: list[str],
    ) -> bool:
        for row in self.rows:
            if (row["user_id"], row["question"], row["answer"]) == (user_id, question, answer):
                row["rating"] = rating
                row["cited"] = cited_source_ids
                return False
        self.rows.append(
            {
                "user_id": user_id,
                "question": question,
                "answer": answer,
                "rating": rating,
                "cited": cited_source_ids,
            }
        )
        return True


def _query(text: str) -> AsyncMock:
    query = AsyncMock()
    query.message = SimpleNamespace(text=text, edit_reply_markup=AsyncMock())
    return query


async def test_feedback_writes_one_row_and_toasts() -> None:
    db = _FakeDB(FeedbackContext(user_id=555, question="Q", answer="A"))
    query = _query("A\n\nИсточник: faq.docx, prices.docx")

    await handle_feedback(query, callback_data=FeedbackCB(rating=1, msg_ref="42"), db=db)  # type: ignore[arg-type]

    assert len(db.rows) == 1
    assert db.rows[0]["rating"] == 1
    assert db.rows[0]["cited"] == ["faq.docx", "prices.docx"]
    query.answer.assert_awaited_once()
    query.message.edit_reply_markup.assert_awaited_once()  # buttons removed after tap


async def test_double_tap_is_idempotent_and_updates_rating() -> None:
    db = _FakeDB(FeedbackContext(user_id=555, question="Q", answer="A"))

    await handle_feedback(_query("A"), callback_data=FeedbackCB(rating=1, msg_ref="42"), db=db)  # type: ignore[arg-type]
    await handle_feedback(_query("A"), callback_data=FeedbackCB(rating=-1, msg_ref="42"), db=db)  # type: ignore[arg-type]

    assert len(db.rows) == 1  # second tap updated, did not duplicate
    assert db.rows[0]["rating"] == -1


async def test_unknown_reference_does_not_write() -> None:
    db = _FakeDB(None)  # get_feedback_context returns None
    query = _query("A")

    await handle_feedback(query, callback_data=FeedbackCB(rating=1, msg_ref="999"), db=db)  # type: ignore[arg-type]

    assert db.rows == []
    query.answer.assert_awaited_once()


def test_parse_cited_sources() -> None:
    assert _parse_cited_sources("Ответ.\n\nИсточник: a.docx, b.pdf") == ["a.docx", "b.pdf"]
    assert _parse_cited_sources("Ответ без источника") == []
    assert _parse_cited_sources(None) == []
    assert _parse_cited_sources("") == []
