"""The small adapters: a model for servers without one configured, and the hourly allowance."""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable
from typing import Any

from kise.assistant.application.ports import LanguageModel, Part, RateLimiter
from kise.assistant.domain.errors import AssistantRateLimited, AssistantUnavailable


class DisabledLanguageModel(LanguageModel):
    """Used when no provider is configured: every call is a clear 503, not a crash."""

    def generate_json(
        self, *, system: str, parts: list[Part], schema: dict[str, Any]
    ) -> dict[str, Any]:
        raise AssistantUnavailable

    def generate_text(self, *, system: str, parts: list[Part]) -> str:
        raise AssistantUnavailable


class InMemoryRateLimiter(RateLimiter):
    """At most ``limit`` requests per key in any rolling ``window_seconds``.

    Per process: with several web dynos each keeps its own count, so the real ceiling is ``limit``
    times the number of dynos. Good enough to stop a runaway client; a shared store (Redis) is the
    upgrade when that matters.
    """

    def __init__(
        self,
        limit: int,
        *,
        window_seconds: float = 3600.0,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self._limit = limit
        self._window = window_seconds
        self._now = now
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def take(self, key: str) -> None:
        if self._limit <= 0:
            return
        now = self._now()
        with self._lock:
            hits = self._hits.setdefault(key, deque())
            while hits and now - hits[0] >= self._window:
                hits.popleft()
            if len(hits) >= self._limit:
                raise AssistantRateLimited(self._limit)
            hits.append(now)
