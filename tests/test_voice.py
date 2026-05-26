"""Voice: transcription params/strip + handler caps, fallbacks, pipeline hand-off (§15)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from bot.config import Settings
from bot.handlers.voice import handle_voice
from bot.services.whisper import WhisperService


def _whisper_with_create(create: AsyncMock) -> WhisperService:
    whisper = WhisperService(Settings())
    whisper._client = SimpleNamespace(  # type: ignore[assignment]
        audio=SimpleNamespace(transcriptions=SimpleNamespace(create=create))
    )
    return whisper


async def test_transcribe_passes_params_and_strips() -> None:
    create = AsyncMock(return_value=SimpleNamespace(text="  привет мир  "))
    whisper = _whisper_with_create(create)

    out = await whisper.transcribe(b"audio-bytes", "v.ogg", "ru")

    assert out == "привет мир"  # stripped
    kwargs = create.await_args.kwargs
    assert kwargs["model"] == "whisper-large-v3-turbo"
    assert kwargs["response_format"] == "text"
    assert kwargs["language"] == "ru"
    assert kwargs["file"] == ("v.ogg", b"audio-bytes", "audio/ogg")


async def test_transcribe_handles_plain_string_return() -> None:
    whisper = _whisper_with_create(AsyncMock(return_value="  hi  "))
    out = await whisper.transcribe(b"x", "v.ogg")  # no language -> omitted kwarg
    assert out == "hi"


def _voice_message(file_size: int | None) -> AsyncMock:
    message = AsyncMock()
    message.voice = SimpleNamespace(file_size=file_size, file_unique_id="abc")
    return message


async def test_oversize_voice_is_rejected_before_transcribing() -> None:
    message = _voice_message(2 * 1024 * 1024)  # 2 MB > 1 MB cap
    whisper = AsyncMock()

    await handle_voice(
        message,
        bot=AsyncMock(),
        db=AsyncMock(),
        embeddings=AsyncMock(),
        claude=AsyncMock(),
        whisper=whisper,
    )

    message.answer.assert_awaited_once()
    assert "текстом" in message.answer.await_args.args[0]
    whisper.transcribe.assert_not_awaited()  # never downloaded/transcribed


async def test_transcription_error_falls_back_without_raising() -> None:
    message = _voice_message(1000)
    whisper = AsyncMock()
    whisper.transcribe.side_effect = RuntimeError("groq unavailable")

    await handle_voice(
        message,
        bot=AsyncMock(),
        db=AsyncMock(),
        embeddings=AsyncMock(),
        claude=AsyncMock(),
        whisper=whisper,
    )

    message.answer.assert_awaited_once()
    assert "распознать" in message.answer.await_args.args[0]


async def test_empty_transcript_falls_back() -> None:
    message = _voice_message(1000)
    whisper = AsyncMock()
    whisper.transcribe.return_value = ""

    await handle_voice(
        message,
        bot=AsyncMock(),
        db=AsyncMock(),
        embeddings=AsyncMock(),
        claude=AsyncMock(),
        whisper=whisper,
    )

    message.answer.assert_awaited_once()
    assert "распознать" in message.answer.await_args.args[0]


async def test_successful_voice_feeds_the_rag_pipeline(monkeypatch: pytest.MonkeyPatch) -> None:
    message = _voice_message(1000)
    whisper = AsyncMock()
    whisper.transcribe.return_value = "сколько стоит доставка"

    captured: dict[str, str] = {}

    async def fake_answer_question(msg: object, *, question: str, **_: object) -> None:
        captured["question"] = question

    monkeypatch.setattr("bot.handlers.voice.answer_question", fake_answer_question)

    await handle_voice(
        message,
        bot=AsyncMock(),
        db=AsyncMock(),
        embeddings=AsyncMock(),
        claude=AsyncMock(),
        whisper=whisper,
    )

    assert captured["question"] == "сколько стоит доставка"  # transcript -> pipeline
    message.answer.assert_not_awaited()  # no fallback; the pipeline owns the reply
