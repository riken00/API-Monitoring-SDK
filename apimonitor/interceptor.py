"""
interceptor.py — Monkey-patches outgoing HTTP libraries.

Supported: requests, httpx, urllib3 (direct usage).

KEY CHANGE from original:
  When enable_validation=True, the metric is piggybacked onto the /validate
  call as the `metric` field — /ingest is NOT called separately.
  One network round-trip handles both Flow 1 (decision) and Flow 2 (capture).

  When enable_validation=False and enable_ingest=True:
  Metric is queued for background /ingest as before.

  This means the SDK NEVER makes two separate calls to the backend per request.

install() returns a list of successfully patched library names so
monitor.diagnose() can report which ones are active.
"""

from __future__ import annotations

import time
from functools import wraps
from typing import List, Optional

from .collector import build_metric
from .logger import get_logger

_originals = {}


def install(config, sender, validator) -> List[str]:
    """Patch all available HTTP libraries. Returns list of patched library names."""
    patched = []
    if _patch_requests(config, sender, validator):  patched.append("requests")
    if _patch_httpx(config, sender, validator):     patched.append("httpx")
    if _patch_urllib3(config, sender, validator):   patched.append("urllib3")
    return patched


def uninstall() -> None:
    """Restore all original functions."""
    for (obj, attr), original in _originals.items():
        setattr(obj, attr, original)
    _originals.clear()


# ── Shared helpers ────────────────────────────────────────────────────────────

def _is_own_url(url: str, config) -> bool:
    """
    Skip monitoring calls to our own platform — prevents infinite loops.
    Checks exact base_url prefix, case-insensitive scheme.
    """
    own = config.base_url.rstrip("/")
    return url.startswith(own) or url.lower().startswith(own.lower())


def _is_excluded(url: str, config) -> bool:
    """Check path exclusion rules (exact match or wildcard prefix)."""
    try:
        from urllib.parse import urlparse
        path = urlparse(url).path
    except Exception:
        path = url

    for rule in config.exclude:
        if rule.endswith("*"):
            if path.startswith(rule[:-1]):
                return True
        elif path == rule:
            return True
    return False


def _blocked_response(validation_result, log):
    """Build a minimal blocked-response object compatible with requests/httpx."""
    import json

    class _BlockedResponse:
        status_code = validation_result.status_code
        headers     = {"Content-Type": "application/json"}

        def __init__(self, body: bytes):
            self._body = body

        @property
        def text(self) -> str:
            return self._body.decode("utf-8")

        @property
        def content(self) -> bytes:
            return self._body

        def json(self):
            return json.loads(self._body)

    body = validation_result.deny_response_body()
    log.info("Request blocked — status=%s reason=%s",
             validation_result.status_code, validation_result.reason)
    return _BlockedResponse(body)


def _handle_request(
    *,
    config,
    sender,
    validator,
    library: str,
    method: str,
    url: str,
    start_time: float,
    response=None,
    error=None,
    request_headers=None,
    request_body=None,
    identity_id: str = "",
) -> None:
    """
    Shared post-request logic for all three libraries.

    If enable_validation=True:
      The metric was already piggybacked on the /validate call BEFORE the
      request executed (see each patch below). Nothing to do here.

    If enable_validation=False and enable_ingest=True:
      Build metric and queue it for background /ingest.
    """
    if config.enable_validation:
        # Already handled via piggybacked metric in validate call
        return

    if config.enable_ingest:
        metric = build_metric(
            library=library,
            method=method,
            url=url,
            start_time=start_time,
            response=response,
            error=error,
            config=config,
            request_headers=request_headers,
            request_body=request_body,
        )
        if metric:
            sender.add_metric(metric)


def _validate_with_metric(
    *,
    config,
    validator,
    sender,
    library: str,
    method: str,
    url: str,
    start_time: float,
    response=None,
    error=None,
    request_headers=None,
    request_body=None,
):
    """
    Combined Flow 1 + Flow 2:
    Calls validator.check() with the metric pre-attached as pending_metric.
    The backend will save the metric inside the /validate handler — no
    separate /ingest call needed.

    Returns ValidationResult.
    """
    # Build the metric dict to piggyback
    pending = None
    if config.enable_ingest:
        pending = build_metric(
            library=library,
            method=method,
            url=url,
            start_time=start_time,
            response=response,
            error=error,
            config=config,
            request_headers=request_headers,
            request_body=request_body,
        )

    return validator.check(
        method=method,
        url=url,
        headers=request_headers or {},
        pending_metric=pending,
    )


# ── requests ──────────────────────────────────────────────────────────────────

def _patch_requests(config, sender, validator) -> bool:
    try:
        import requests
        log      = get_logger(config.log_level, config.debug)
        original = requests.Session.request

        @wraps(original)
        def monitored(self, method, url, **kwargs):
            if _is_own_url(url, config) or _is_excluded(url, config):
                return original(self, method, url, **kwargs)

            req_headers = kwargs.get("headers") or {}
            req_body    = kwargs.get("data") or kwargs.get("json")
            start       = time.time()

            # ── Flow 1: Validation (with piggybacked metric) ───────────────
            if config.enable_validation:
                # Build metric from pre-request info (no response yet)
                pending = None
                if config.enable_ingest:
                    pending = build_metric(
                        library="requests",
                        method=method, url=url,
                        start_time=start,
                        response=None, error=None,
                        config=config,
                        request_headers=req_headers,
                        request_body=req_body,
                    )

                result = validator.check(
                    method=method, url=url,
                    headers=req_headers,
                    pending_metric=pending,
                )
                if not result.allowed:
                    return _blocked_response(result, log)

            # ── Execute the actual request ─────────────────────────────────
            response = error = None
            try:
                response = original(self, method, url, **kwargs)
                return response
            except Exception as exc:
                error = exc
                raise
            finally:
                # ── Flow 2 only (validation=False) ─────────────────────────
                if not config.enable_validation and config.enable_ingest:
                    metric = build_metric(
                        library="requests",
                        method=method, url=url,
                        start_time=start,
                        response=response, error=error,
                        config=config,
                        request_headers=req_headers,
                        request_body=req_body,
                    )
                    if metric:
                        sender.add_metric(metric)

        requests.Session.request                    = monitored
        _originals[(requests.Session, "request")]   = original
        log.debug("Patched requests.Session.request")
        return True
    except ImportError:
        return False


# ── httpx ─────────────────────────────────────────────────────────────────────

def _patch_httpx(config, sender, validator) -> bool:
    try:
        import httpx
        log      = get_logger(config.log_level, config.debug)
        original = httpx.Client.send

        @wraps(original)
        def monitored(self, request, **kwargs):
            url         = str(request.url)
            if _is_own_url(url, config) or _is_excluded(url, config):
                return original(self, request, **kwargs)

            req_headers = dict(request.headers)
            req_body    = request.content
            start       = time.time()

            # ── Flow 1: Validation (with piggybacked metric) ───────────────
            if config.enable_validation:
                pending = None
                if config.enable_ingest:
                    pending = build_metric(
                        library="httpx",
                        method=request.method, url=url,
                        start_time=start,
                        response=None, error=None,
                        config=config,
                        request_headers=req_headers,
                        request_body=req_body,
                    )

                result = validator.check(
                    method=request.method, url=url,
                    headers=req_headers,
                    pending_metric=pending,
                )
                if not result.allowed:
                    return _blocked_response(result, log)

            # ── Execute the actual request ─────────────────────────────────
            response = error = None
            try:
                response = original(self, request, **kwargs)
                return response
            except Exception as exc:
                error = exc
                raise
            finally:
                if not config.enable_validation and config.enable_ingest:
                    metric = build_metric(
                        library="httpx",
                        method=request.method, url=url,
                        start_time=start,
                        response=response, error=error,
                        config=config,
                        request_headers=req_headers,
                        request_body=req_body,
                    )
                    if metric:
                        sender.add_metric(metric)

        httpx.Client.send                       = monitored
        _originals[(httpx.Client, "send")]      = original
        log.debug("Patched httpx.Client.send")
        return True
    except ImportError:
        return False


# ── urllib3 ───────────────────────────────────────────────────────────────────

def _patch_urllib3(config, sender, validator) -> bool:
    try:
        import urllib3
        log      = get_logger(config.log_level, config.debug)
        original = urllib3.HTTPConnectionPool.urlopen

        @wraps(original)
        def monitored(self, method, url, **kwargs):
            # urllib3 gives us path only — reconstruct full URL
            port     = f":{self.port}" if self.port not in (80, 443, None) else ""
            scheme   = "https" if self.port == 443 else "http"
            full_url = f"{scheme}://{self.host}{port}{url}"

            if _is_own_url(full_url, config) or _is_excluded(full_url, config):
                return original(self, method, url, **kwargs)

            req_headers = kwargs.get("headers") or {}
            req_body    = kwargs.get("body")
            start       = time.time()

            # ── Flow 1: Validation (with piggybacked metric) ───────────────
            if config.enable_validation:
                pending = None
                if config.enable_ingest:
                    pending = build_metric(
                        library="urllib3",
                        method=method, url=full_url,
                        start_time=start,
                        response=None, error=None,
                        config=config,
                        request_headers=req_headers,
                        request_body=req_body,
                    )

                result = validator.check(
                    method=method, url=full_url,
                    headers=req_headers,
                    pending_metric=pending,
                )
                if not result.allowed:
                    return _blocked_response(result, log)

            # ── Execute the actual request ─────────────────────────────────
            response = error = None
            try:
                response = original(self, method, url, **kwargs)
                return response
            except Exception as exc:
                error = exc
                raise
            finally:
                if not config.enable_validation and config.enable_ingest:
                    metric = build_metric(
                        library="urllib3",
                        method=method, url=full_url,
                        start_time=start,
                        response=response, error=error,
                        config=config,
                        request_headers=req_headers,
                        request_body=req_body,
                    )
                    if metric:
                        sender.add_metric(metric)

        urllib3.HTTPConnectionPool.urlopen                       = monitored
        _originals[(urllib3.HTTPConnectionPool, "urlopen")]      = original
        log.debug("Patched urllib3.HTTPConnectionPool.urlopen")
        return True
    except ImportError:
        return False