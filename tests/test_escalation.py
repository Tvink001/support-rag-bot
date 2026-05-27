"""Escalation: pure threshold/cooldown helpers + Take/Suggest/relay handlers (§14)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

from bot.handlers.escalation import (
    EscalateCB,
    compute_cooldown_until,
    escalate,
    is_below_threshold,
    is_in_cooldown,
    on_manager_suggestion,
    on_take,
)
from bot.models import Escalation

_NOW = datetime(2026, 5, 27, 12, 0, tzinfo=timezone.utc)


# --- pure helpers ------------------------------------------------------------
def test_is_below_threshold() -> None:
    assert is_below_threshold(0.4, 0.6, has_chunks=True) is True  # weak best hit
    assert is_below_threshold(0.9, 0.6, has_chunks=True) is False  # strong hit
    assert is_below_threshold(0.99, 0.6, has_chunks=False) is True  # no chunks at all


def test_is_in_cooldown() -> None:
    future = _NOW + timedelta(hours=1)
    past = _NOW - timedelta(hours=1)
    assert is_in_cooldown("taken", future, _NOW) is True
    assert is_in_cooldown("taken", past, _NOW) is False  # cooldown elapsed
    assert is_in_cooldown("open", future, _NOW) is False  # only 'taken' mutes
    assert is_in_cooldown("taken", None, _NOW) is False


def test_compute_cooldown_until() -> None:
    assert compute_cooldown_until(_NOW, 24) == _NOW + timedelta(hours=24)


# --- handlers ----------------------------------------------------------------
class _FakeDB:
    def __init__(self, *, take_result: bool = True, resolve: Escalation | None = None) -> None:
        self._take_result = take_result
        self._resolve = resolve
        self.taken: tuple[object, int, datetime] | None = None
        self.created: tuple[int, str] | None = None
        self.manager_msg: tuple[object, int] | None = None

    async def create_escalation(self, user_id: int, question: str) -> object:
        self.created = (user_id, question)
        return uuid4()

    async def set_escalation_manager_msg(self, escalation_id: object, manager_msg_id: int) -> None:
        self.manager_msg = (escalation_id, manager_msg_id)

    async def take_escalation(
        self, escalation_id: object, manager_id: int, cooldown_until: datetime
    ) -> bool:
        self.taken = (escalation_id, manager_id, cooldown_until)
        return self._take_result

    async def resolve_escalation(
        self, escalation_id: object, resolution_text: str
    ) -> Escalation | None:
        return self._resolve


async def test_escalate_opens_row_tells_user_and_posts_to_managers() -> None:
    db = _FakeDB()
    bot = AsyncMock()
    bot.send_message.return_value = SimpleNamespace(message_id=42)
    message = AsyncMock()
    user = SimpleNamespace(id=5, full_name="Иван", username="ivan")

    await escalate(message, db=db, bot=bot, question="Как вернуть товар?", user=user)

    assert db.created == (5, "Как вернуть товар?")  # escalation opened
    message.answer.assert_awaited_once()  # user told honestly
    bot.send_message.assert_awaited_once()  # posted to managers
    assert db.manager_msg is not None and db.manager_msg[1] == 42  # manager_msg_id saved


async def test_on_take_sets_cooldown_and_clears_buttons() -> None:
    db = _FakeDB(take_result=True)
    query = AsyncMock()
    query.from_user = SimpleNamespace(id=10, full_name="Менеджер")
    query.message = SimpleNamespace(text="post", html_text="post", edit_text=AsyncMock())
    escalation_id = uuid4()

    await on_take(
        query, callback_data=EscalateCB(action="take", escalation_id=str(escalation_id)), db=db
    )

    assert db.taken is not None
    assert db.taken[0] == escalation_id and db.taken[1] == 10
    assert db.taken[2] > datetime.now(timezone.utc)  # cooldown is in the future
    query.answer.assert_awaited_once()
    query.message.edit_text.assert_awaited_once()  # buttons removed


async def test_on_take_double_tap_is_noop() -> None:
    db = _FakeDB(take_result=False)  # already taken
    query = AsyncMock()
    query.from_user = SimpleNamespace(id=10, full_name="Менеджер")
    query.message = SimpleNamespace(text="post", html_text="post", edit_text=AsyncMock())

    await on_take(query, callback_data=EscalateCB(action="take", escalation_id=str(uuid4())), db=db)

    query.answer.assert_awaited_once_with("Уже в работе.")
    query.message.edit_text.assert_not_awaited()  # nothing to change on a 2nd tap


async def test_manager_suggestion_resolves_and_relays_to_user() -> None:
    escalation = Escalation(id=uuid4(), user_id=777, question="q", status="resolved")
    db = _FakeDB(resolve=escalation)
    state = AsyncMock()
    state.get_data.return_value = {"escalation_id": str(escalation.id)}
    bot = AsyncMock()
    message = AsyncMock()
    message.text = "Возврат в течение 14 дней."

    await on_manager_suggestion(message, state=state, bot=bot, db=db)

    state.clear.assert_awaited_once()
    bot.send_message.assert_awaited_once()
    assert bot.send_message.await_args.args[0] == 777  # delivered to the right user
    assert message.answer.await_count == 2  # confirm to the manager + "save as FAQ?" offer
    assert message.answer.await_args.kwargs.get("reply_markup") is not None  # WOW 2 offer
