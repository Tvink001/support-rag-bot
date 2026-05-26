# syntax=docker/dockerfile:1
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# 1) Dependency layer — cached unless pyproject.toml changes. A stub package lets
#    `pip install -e .` resolve metadata + dependencies without the real source,
#    so editing bot/ later does not reinstall the whole dependency tree.
COPY pyproject.toml ./
RUN mkdir -p bot \
 && printf '"""P4_RAG bot package."""\n' > bot/__init__.py \
 && pip install --upgrade pip \
 && pip install -e .

# 2) Source layer — only this rebuilds on code changes (editable install points
#    at /app, so the real package overwrites the stub).
COPY bot/ ./bot/

EXPOSE 8080

CMD ["python", "-m", "bot.main"]
