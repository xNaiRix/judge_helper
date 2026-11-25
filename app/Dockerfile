# Production-образ приложения «Помощник судьи» (FastAPI + Uvicorn).
# Собирается с встроенным BuildKit (--mount=type=cache поддерживается по умолчанию).

# ---------- Этап 1: сборка виртуального окружения ----------
FROM python:3.11-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_ROOT_USER_ACTION=ignore

WORKDIR /build

COPY requirements.txt ./
RUN --mount=type=cache,target=/root/.cache/pip \
    python -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip \
    && /opt/venv/bin/pip install -r requirements.txt

# ---------- Этап 2: минимальный runtime ----------
FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    PATH="/opt/venv/bin:$PATH" \
    DATABASE_URL=sqlite:////data/judge_helper.db

# DejaVu нужен для кириллицы в PNG/PDF-отчётах, curl — для HEALTHCHECK.
RUN apt-get update \
    && apt-get install -y --no-install-recommends fonts-dejavu-core curl \
    && rm -rf /var/lib/apt/lists/*

# Непривилегированный пользователь (принцип наименьших прав).
RUN groupadd --gid 1000 app \
    && useradd --uid 1000 --gid app --create-home --home-dir /app \
        --shell /usr/sbin/nologin app

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv
COPY --chown=app:app src ./src

# Каталог для SQLite-базы: в docker-compose сюда монтируется volume.
RUN mkdir -p /data && chown app:app /data

USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/health || exit 1

STOPSIGNAL SIGTERM

CMD ["uvicorn", "judge_helper.asgi:app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--proxy-headers", \
     "--forwarded-allow-ips", "*"]
