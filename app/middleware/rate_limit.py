"""Simple per-IP rate limiting for /api routes (Task 13.1)."""

from __future__ import annotations

import time
from collections import defaultdict
from typing import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.core.config import settings

_window: dict[str, list[float]] = defaultdict(list)
# Generous default so test suites and local dev do not hit 429.
_MAX_PER_MINUTE = 600


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        if settings.DEBUG or not request.url.path.startswith("/api/"):
            return await call_next(request)
        ip = request.client.host if request.client else "unknown"
        now = time.time()
        w = _window[ip]
        w[:] = [t for t in w if now - t < 60.0]
        if len(w) >= _MAX_PER_MINUTE:
            return JSONResponse(
                status_code=429,
                content={
                    "error": {
                        "message": "Too many requests; please slow down.",
                        "type": "RateLimitExceeded",
                        "details": {"retry_after_seconds": 60},
                    }
                },
            )
        w.append(now)
        return await call_next(request)
