"""
Fixed-window request rate limiting.

Counters live in Redis so every API process shares them. If Redis is
unreachable the limiter falls back to per-process memory for a short period
instead of failing open or blocking every request. Identities (IP addresses,
emails, tenant ids) are hashed before they become keys.
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from dataclasses import dataclass
from typing import Callable, Dict, Optional, Tuple

from fastapi import Depends, HTTPException, Request, status

from backend.app.core.config import settings
from backend.app.core.dependencies import TenantContext, get_tenant_context

logger = logging.getLogger(__name__)

KEY_PREFIX = "msme:ratelimit"
REDIS_RETRY_SECONDS = 30.0
MEMORY_MAX_KEYS = 50_000
EXEMPT_PATHS = frozenset({"/health"})


@dataclass(frozen=True)
class RateLimitResult:
    allowed: bool
    count: int
    limit: int
    retry_after: int


class RateLimiter:
    def __init__(self, redis_url: Optional[str] = None) -> None:
        self._redis_url = redis_url
        self._redis = None
        self._redis_down_until = 0.0
        self._memory: Dict[str, Tuple[float, int]] = {}
        self._lock = threading.Lock()

    def _client(self):
        if time.monotonic() < self._redis_down_until:
            return None
        if self._redis is None:
            import redis

            self._redis = redis.Redis.from_url(
                self._redis_url or settings.REDIS_URL,
                socket_connect_timeout=0.25,
                socket_timeout=0.25,
            )
        return self._redis

    def _incr_redis(self, key: str, cost: int, ttl: int) -> Optional[int]:
        client = self._client()
        if client is None:
            return None
        try:
            pipe = client.pipeline()
            pipe.incrby(key, cost)
            pipe.expire(key, ttl)
            count, _ = pipe.execute()
            return int(count)
        except Exception as exc:
            self._redis_down_until = time.monotonic() + REDIS_RETRY_SECONDS
            logger.warning("Rate limiter using in-memory counters; Redis unavailable (%s).", type(exc).__name__)
            return None

    def _incr_memory(self, key: str, cost: int, ttl: int) -> int:
        now = time.monotonic()
        with self._lock:
            if len(self._memory) > MEMORY_MAX_KEYS:
                self._memory = {k: v for k, v in self._memory.items() if v[0] > now}
            expires, count = self._memory.get(key, (now + ttl, 0))
            if expires <= now:
                expires, count = now + ttl, 0
            count += cost
            self._memory[key] = (expires, count)
            return count

    def hit(self, bucket: str, identity: str, limit: int, window_seconds: int, cost: int = 1) -> RateLimitResult:
        """Count `cost` requests for identity in the current window (cost=0 only reads)."""
        if not settings.RATE_LIMIT_ENABLED or limit <= 0:
            return RateLimitResult(True, 0, limit, 0)
        now = time.time()
        window = int(now // window_seconds)
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]
        key = f"{KEY_PREFIX}:{bucket}:{window_seconds}:{window}:{digest}"
        ttl = window_seconds + 1
        count = self._incr_redis(key, cost, ttl)
        if count is None:
            count = self._incr_memory(key, cost, ttl)
        retry_after = max(1, int((window + 1) * window_seconds - now))
        allowed = count <= limit if cost else count < limit
        return RateLimitResult(allowed, count, limit, retry_after)

    def reset_memory(self) -> None:
        with self._lock:
            self._memory.clear()


limiter = RateLimiter()


def client_ip(request: Request) -> str:
    """Client address as seen by the ASGI server (proxy headers are resolved by uvicorn)."""
    return request.client.host if request.client else "unknown"


def too_many_requests(result: RateLimitResult) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail=f"Too many requests. Please wait {result.retry_after} seconds and try again.",
        headers={"Retry-After": str(result.retry_after)},
    )


def enforce(result: RateLimitResult) -> None:
    if not result.allowed:
        raise too_many_requests(result)


def ip_limit(bucket: str, limit_setting: str, window_seconds: int) -> Callable[[Request], None]:
    """Route dependency limiting requests per client IP."""

    def dependency(request: Request) -> None:
        enforce(limiter.hit(bucket, client_ip(request), getattr(settings, limit_setting), window_seconds))

    return dependency


def tenant_limit(bucket: str, limit_setting: str, window_seconds: int) -> Callable[..., None]:
    """Route dependency limiting requests per business tenant."""

    def dependency(tenant_ctx: TenantContext = Depends(get_tenant_context)) -> None:
        enforce(limiter.hit(bucket, str(tenant_ctx.business_id), getattr(settings, limit_setting), window_seconds))

    return dependency


login_ip_limit = ip_limit("login", "RATE_LIMIT_LOGIN_PER_MINUTE", 60)
register_ip_limit = ip_limit("register", "RATE_LIMIT_REGISTER_PER_HOUR", 3600)
upload_limit = tenant_limit("upload", "RATE_LIMIT_UPLOADS_PER_MINUTE", 60)
demo_limit = tenant_limit("demo", "RATE_LIMIT_DEMO_PER_MINUTE", 60)


def check_login_account(email: str) -> None:
    """Reject further attempts once an account has too many failed logins this hour."""
    enforce(limiter.hit("login-failed", email.strip().lower(), settings.RATE_LIMIT_LOGIN_PER_ACCOUNT_PER_HOUR, 3600, cost=0))


def record_failed_login(email: str) -> None:
    limiter.hit("login-failed", email.strip().lower(), settings.RATE_LIMIT_LOGIN_PER_ACCOUNT_PER_HOUR, 3600)


class GlobalRateLimitMiddleware:
    """Per-IP ceiling on all API traffic; CORS preflights and health checks are exempt."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http" or scope.get("method") == "OPTIONS" or scope.get("path") in EXEMPT_PATHS:
            await self.app(scope, receive, send)
            return
        client = scope.get("client")
        ip = client[0] if client else "unknown"
        result = limiter.hit("global", ip, settings.RATE_LIMIT_PER_MINUTE, 60)
        if result.allowed:
            await self.app(scope, receive, send)
            return
        from starlette.responses import JSONResponse

        exc = too_many_requests(result)
        response = JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=exc.headers)
        await response(scope, receive, send)
