"""
validator.py — Flow 1: synchronous pre-request validation gate.

KEY CHANGE from original:
  _call_validate() now sends the pending_metric inside the validate payload.
  The backend saves the metric inside /validate — no separate /ingest needed.
  This is the core of the "one request, both jobs" design.

Performance layers (fastest to slowest):
  1. In-process LRU cache  — sub-millisecond, no network
  2. Circuit breaker open  — sub-millisecond, no network, uses fail_behavior
  3. Live /validate call   — single network round-trip, carries metric payload
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from collections import OrderedDict
from typing import Any, Dict, Optional

from .logger import get_logger


class ValidationResult:
    __slots__ = ("allowed", "status_code", "reason", "retry_after")

    def __init__(
        self,
        allowed: bool,
        status_code: int = 200,
        reason: str = "",
        retry_after: Optional[int] = None,
    ):
        self.allowed     = allowed
        self.status_code = status_code
        self.reason      = reason
        self.retry_after = retry_after

    def deny_response_body(self) -> bytes:
        payload = {
            "error":      self.reason or "Request blocked by API Monitor",
            "blocked_by": "apimonitor",
        }
        if self.retry_after is not None:
            payload["retry_after"] = self.retry_after
        return json.dumps(payload).encode("utf-8")


class _LRUCache:
    """Thread-safe fixed-size LRU cache with per-entry TTL (milliseconds)."""

    def __init__(self, maxsize: int = 4096):
        self._maxsize = maxsize
        self._data: OrderedDict = OrderedDict()
        self._lock  = threading.Lock()

    def get(self, key: str, ttl_ms: int) -> Optional[bool]:
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                return None
            value, ts = entry
            if (time.monotonic() * 1000 - ts) > ttl_ms:
                del self._data[key]
                return None
            self._data.move_to_end(key)
            return value

    def set(self, key: str, value: bool) -> None:
        with self._lock:
            if key in self._data:
                self._data.move_to_end(key)
            self._data[key] = (value, time.monotonic() * 1000)
            while len(self._data) > self._maxsize:
                self._data.popitem(last=False)


class CircuitBreaker:
    """
    Tracks consecutive failures within a rolling window.
    States: CLOSED (normal) → OPEN (fail-fast) → HALF-OPEN (probing).

    Thresholds (_threshold, _window, _recovery) are read at check time
    so they can be updated live by the heartbeat without recreating the object.
    """

    CLOSED    = "closed"
    OPEN      = "open"
    HALF_OPEN = "half_open"

    def __init__(self, failure_threshold: int, window_seconds: int, recovery_seconds: int):
        self._threshold = failure_threshold
        self._window    = window_seconds
        self._recovery  = recovery_seconds
        self._failures: list = []
        self._state     = self.CLOSED
        self._opened_at: float = 0.0
        self._lock      = threading.Lock()

    @property
    def state(self) -> str:
        with self._lock:
            return self._get_state()

    def _get_state(self) -> str:
        now = time.monotonic()
        if self._state == self.OPEN:
            if now - self._opened_at >= self._recovery:
                self._state = self.HALF_OPEN
        return self._state

    def is_open(self) -> bool:
        with self._lock:
            return self._get_state() == self.OPEN

    def record_success(self) -> None:
        with self._lock:
            self._failures.clear()
            self._state = self.CLOSED

    def record_failure(self) -> None:
        with self._lock:
            now = time.monotonic()
            self._failures = [t for t in self._failures if now - t < self._window]
            self._failures.append(now)
            if len(self._failures) >= self._threshold:
                self._state     = self.OPEN
                self._opened_at = now


class Validator:
    """
    Pre-request validation gate.

    Usage:
        result = validator.check(method="GET", url="...", headers={},
                                 pending_metric=metric_dict)
        if not result.allowed:
            # return blocked response immediately
    """

    def __init__(self, config):
        self._config  = config
        self._cache   = _LRUCache(maxsize=4096)
        self._breaker = CircuitBreaker(
            failure_threshold=config.cb_failure_threshold,
            window_seconds=config.cb_window_seconds,
            recovery_seconds=config.cb_recovery_seconds,
        )
        self._log = get_logger(config.log_level, config.debug)

    def check(
        self,
        method: str,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        identity_id: str = "",
        pending_metric: Optional[Any] = None,
    ) -> ValidationResult:
        """
        Returns a ValidationResult synchronously.
        Always returns in < validate_timeout_ms — never hangs.

        pending_metric: pre-built metric dict to piggyback on the validate call.
          If provided and the request is allowed, the backend saves it to
          api_metrics inside the /validate handler — no /ingest call needed.
        """
        cache_key = f"{method.upper()}::{url}::{identity_id}"

        # 1. In-process cache hit — only cache allow/deny without pending_metric
        #    because piggybacked metrics must always be sent
        if pending_metric is None:
            cached = self._cache.get(cache_key, self._config.check_cache_ttl_ms)
            if cached is not None:
                return ValidationResult(allowed=cached)

        # 2. Circuit breaker open → fail_behavior (no network)
        if self._breaker.is_open():
            self._log.warning(
                "Circuit breaker OPEN — applying fail_behavior=%s",
                self._config.fail_behavior,
            )
            return self._fail_response()

        # 3. Live call to /validate (carries pending_metric)
        result = self._call_validate(method, url, headers or {}, identity_id, pending_metric)

        # 4. Cache the allow/deny decision (only when no metric — metric payloads
        #    are unique per request so caching would lose them)
        if pending_metric is None:
            self._cache.set(cache_key, result.allowed)

        # 5. Update circuit breaker
        if result.allowed or result.status_code in (403, 429):
            # Platform responded normally (even a deny is a healthy response)
            self._breaker.record_success()
        else:
            self._breaker.record_failure()

        return result

    def _call_validate(
        self,
        method: str,
        url: str,
        headers: Dict[str, str],
        identity_id: str,
        pending_metric: Optional[Any],
    ) -> ValidationResult:
        """
        POST to /validate.
        If pending_metric is provided it is embedded in the payload so the
        backend handles both the rate-limit decision AND the metric insert
        in a single round-trip.
        """
        payload: Dict[str, Any] = {
            "api_key":     self._config.api_key,
            "method":      method.upper(),
            "url":         url,
            "identity_id": identity_id,
        }

        # Attach the metric — backend will insert it if the request is allowed
        if pending_metric is not None:
            payload["metric"] = pending_metric

        encoded     = json.dumps(payload, default=str).encode("utf-8")
        timeout_s   = self._config.validate_timeout_ms / 1000

        try:
            req = urllib.request.Request(
                self._config.validate_url,
                data=encoded,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                body = json.loads(resp.read().decode("utf-8"))
                self._log.debug("validate allow: %s %s", method, url)
                return ValidationResult(
                    allowed=True,
                    status_code=resp.status,
                    reason=body.get("reason", ""),
                )

        except urllib.error.HTTPError as e:
            try:
                err_body = json.loads(e.read().decode("utf-8"))
            except Exception:
                err_body = {}

            detail      = err_body.get("detail", {})
            if isinstance(detail, dict):
                reason      = detail.get("reason", "Request denied")
                retry_after = detail.get("retry_after")
            else:
                reason      = str(detail) or "Request denied"
                retry_after = None

            self._log.info(
                "validate deny: %s %s status=%s reason=%s",
                method, url, e.code, reason,
            )
            return ValidationResult(
                allowed=False,
                status_code=e.code,
                reason=reason,
                retry_after=retry_after,
            )

        except Exception as exc:
            self._log.warning(
                "validate error: %s — applying fail_behavior=%s",
                exc, self._config.fail_behavior,
            )
            self._breaker.record_failure()
            return self._fail_response()

    def _fail_response(self) -> ValidationResult:
        if self._config.fail_behavior == "open":
            return ValidationResult(allowed=True, reason="fail_open")
        return ValidationResult(
            allowed=False,
            status_code=503,
            reason="API Monitor platform unreachable",
        )