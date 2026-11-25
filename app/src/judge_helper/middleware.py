"""HTTP-middleware: request_id, RELEASE-логи без ПД, rate limiting по IP.

RELEASE-логи содержат только технические данные: request_id, endpoint (путь),
method, status_code, duration_ms. Без email, ФИО, оценок, токенов и IP (ТЗ).
"""

from __future__ import annotations

import logging
import time
import uuid
from collections import defaultdict, deque
from contextvars import ContextVar

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

request_id_var: ContextVar[str] = ContextVar("request_id", default="")

logger = logging.getLogger("judge_helper.release")


class RateLimiter:
    """Скользящее окно: не более limit запросов в window секунд с одного IP."""

    def __init__(self, limit: int, window_seconds: float = 60.0):
        self.limit = limit
        self.window = window_seconds
        self._hits: dict[str, deque] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        bucket = self._hits[key]
        while bucket and now - bucket[0] > self.window:
            bucket.popleft()
        if len(bucket) >= self.limit:
            return False
        bucket.append(now)
        return True

    def reset(self) -> None:
        self._hits.clear()


class LoginThrottler:
    """Не более max_attempts неудачных входов за window минут (по email+IP)."""

    def __init__(self, max_attempts: int, window_minutes: float):
        self.max_attempts = max_attempts
        self.window = window_minutes * 60
        self._fails: dict[str, deque] = defaultdict(deque)

    def blocked(self, key: str) -> bool:
        now = time.monotonic()
        bucket = self._fails[key]
        while bucket and now - bucket[0] > self.window:
            bucket.popleft()
        return len(bucket) >= self.max_attempts

    def register_failure(self, key: str) -> None:
        self._fails[key].append(time.monotonic())

    def reset(self) -> None:
        self._fails.clear()


def register_middleware(app: FastAPI) -> None:
    @app.middleware("http")
    async def request_pipeline(request: Request, call_next):
        request_id = uuid.uuid4().hex[:12]
        request_id_var.set(request_id)

        # Rate limit: суммарно все типы запросов с одного IP (ТЗ)
        limiter: RateLimiter = request.app.state.rate_limiter
        ip = request.client.host if request.client else "unknown"
        if not limiter.allow(ip):
            logger.info(
                "request_id=%s endpoint=%s method=%s status_code=429 rate_limited=1",
                request_id,
                request.url.path,
                request.method,
            )
            return JSONResponse(
                status_code=429,
                content={"detail": "Превышен лимит запросов (100 в минуту с одного IP)"},
                headers={"X-Request-ID": request_id},
            )

        start = time.perf_counter()
        response = await call_next(request)
        duration_ms = (time.perf_counter() - start) * 1000

        # RELEASE-лог: только технические данные, без ПД
        logger.info(
            "request_id=%s endpoint=%s method=%s status_code=%s duration_ms=%.1f",
            request_id,
            request.url.path,
            request.method,
            response.status_code,
            duration_ms,
        )
        response.headers["X-Request-ID"] = request_id
        return response
