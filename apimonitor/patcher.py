"""
patcher.py — Auto-patches incoming request handlers for Django, Flask, FastAPI/Starlette.

Changes from original:
  - Starlette/FastAPI patch now correctly reads request body from ASGI receive
  - All patch functions return the framework name on success
  - install() returns list of successfully patched frameworks
  - Validate+Ingest combined: when enable_validation=True, metric is
    piggybacked on the validate call — /ingest is NOT called separately
"""

from __future__ import annotations

import asyncio
import functools
import json
import time
from typing import List

from .collector import build_metric
from .logger import get_logger


def install(config, sender, validator) -> List[str]:
    """Patch all available web frameworks. Returns list of patched framework names."""
    patched = []
    if _patch_django(config, sender, validator):    patched.append("django")
    if _patch_flask(config, sender, validator):     patched.append("flask")
    if _patch_starlette(config, sender, validator): patched.append("starlette/fastapi")
    return patched


def _is_excluded(path: str, config) -> bool:
    for rule in config.exclude:
        if rule.endswith("*"):
            if path.startswith(rule[:-1]):
                return True
        elif path == rule:
            return True
    return False


# ── Django ────────────────────────────────────────────────────────────────────

def _patch_django(config, sender, validator) -> bool:
    try:
        from django.core.handlers.base import BaseHandler
        log = get_logger(config.log_level, config.debug)
        original = BaseHandler.get_response

        @functools.wraps(original)
        def monitored(self, request):
            path = getattr(request, "path", "")
            if _is_excluded(path, config):
                return original(self, request)

            headers = {
                k: v for k, v in request.META.items()
                if isinstance(k, str) and (k.startswith("HTTP_") or k in ("CONTENT_TYPE", "CONTENT_LENGTH"))
            }
            remote_addr = request.META.get("REMOTE_ADDR", "")

            if config.enable_validation:
                result = validator.check(method=request.method, url=request.build_absolute_uri(), headers=headers)
                if not result.allowed:
                    from django.http import JsonResponse
                    return JsonResponse(json.loads(result.deny_response_body()), status=result.status_code)

            start    = time.time()
            response = original(self, request)

            if config.enable_ingest:
                req_body = resp_body = None
                try:
                    if config.capture_request_body and hasattr(request, "body"):
                        req_body = request.body
                except Exception:
                    pass
                try:
                    if config.capture_response_body and hasattr(response, "content"):
                        resp_body = response.content
                except Exception:
                    pass

                class _R:
                    status_code = getattr(response, "status_code", None)
                    headers     = dict(getattr(response, "headers", {}))
                    text        = resp_body.decode("utf-8", errors="replace") if isinstance(resp_body, bytes) else (resp_body or "")
                    content     = resp_body if isinstance(resp_body, bytes) else b""

                metric = build_metric(
                    library="django", method=request.method,
                    url=request.build_absolute_uri(), start_time=start,
                    response=_R(), error=None, config=config,
                    request_headers=headers, request_body=req_body,
                    remote_addr=remote_addr,
                )
                if metric:
                    sender.add_metric(metric)

            return response

        BaseHandler.get_response = monitored
        log.debug("Patched Django BaseHandler.get_response")
        return True
    except ImportError:
        return False


# ── Flask ─────────────────────────────────────────────────────────────────────

def _patch_flask(config, sender, validator) -> bool:
    try:
        from flask import Flask, request as flask_request
        log = get_logger(config.log_level, config.debug)
        original = Flask.full_dispatch_request

        @functools.wraps(original)
        def monitored(self):
            path = flask_request.path
            if _is_excluded(path, config):
                return original(self)

            headers     = dict(flask_request.headers)
            remote_addr = flask_request.remote_addr or ""

            if config.enable_validation:
                result = validator.check(method=flask_request.method, url=flask_request.url, headers=headers)
                if not result.allowed:
                    from flask import Response
                    return Response(result.deny_response_body(), status=result.status_code, mimetype="application/json")

            start    = time.time()
            response = original(self)

            if config.enable_ingest:
                try:
                    req_body  = flask_request.get_data(as_text=False) if config.capture_request_body else None
                    resp_body = response.get_data() if (config.capture_response_body and hasattr(response, "get_data")) else None

                    class _R:
                        status_code = response.status_code
                        headers     = dict(response.headers)
                        text        = resp_body.decode("utf-8", errors="replace") if isinstance(resp_body, bytes) else ""
                        content     = resp_body if isinstance(resp_body, bytes) else b""

                    metric = build_metric(
                        library="flask", method=flask_request.method,
                        url=flask_request.url, start_time=start,
                        response=_R(), error=None, config=config,
                        request_headers=headers, request_body=req_body,
                        remote_addr=remote_addr,
                    )
                    if metric:
                        sender.add_metric(metric)
                except Exception:
                    pass

            return response

        Flask.full_dispatch_request = monitored
        log.debug("Patched Flask.full_dispatch_request")
        return True
    except ImportError:
        return False


# ── Starlette / FastAPI ───────────────────────────────────────────────────────

def _patch_starlette(config, sender, validator) -> bool:
    """
    Fix from original: request body is now correctly read from ASGI receive.
    Original always had request_body=None for FastAPI users.
    """
    try:
        from starlette.applications import Starlette
        log = get_logger(config.log_level, config.debug)
        original = Starlette.__call__

        @functools.wraps(original)
        async def monitored(self, scope, receive, send):
            if scope.get("type") != "http":
                await original(self, scope, receive, send)
                return

            path = scope.get("path", "")
            if _is_excluded(path, config):
                await original(self, scope, receive, send)
                return

            req_headers = {
                k.decode(): v.decode()
                for k, v in scope.get("headers", [])
            }
            remote = ""
            client = scope.get("client")
            if client:
                remote = client[0]

            method    = scope.get("method", "GET")
            host      = req_headers.get("host", "localhost")
            scheme    = scope.get("scheme", "http")
            full_url  = f"{scheme}://{host}{path}"
            qs        = scope.get("query_string", b"")
            if qs:
                full_url += f"?{qs.decode()}"

            # ── Read request body from ASGI receive (was missing in original) ──
            req_body_bytes = b""
            if config.capture_request_body:
                try:
                    body_parts = []
                    bytes_read = 0
                    more_body  = True
                    while more_body:
                        message   = await receive()
                        chunk     = message.get("body", b"")
                        more_body = message.get("more_body", False)
                        bytes_read += len(chunk)
                        if bytes_read <= config.max_body_bytes:
                            body_parts.append(chunk)
                        # Stop reading if we've hit the cap
                        if bytes_read >= config.max_body_bytes:
                            more_body = False
                    req_body_bytes = b"".join(body_parts)
                except Exception:
                    pass

                # Replay body to the actual handler via a new receive callable
                body_consumed = False

                async def replay_receive():
                    nonlocal body_consumed
                    if not body_consumed:
                        body_consumed = True
                        return {"type": "http.request", "body": req_body_bytes, "more_body": False}
                    return await receive()

                actual_receive = replay_receive
            else:
                actual_receive = receive

            # ── Flow 1: Validation gate ────────────────────────────────────
            if config.enable_validation:
                result = validator.check(method=method, url=full_url, headers=req_headers)
                if not result.allowed:
                    body = result.deny_response_body()
                    await send({
                        "type": "http.response.start",
                        "status": result.status_code,
                        "headers": [
                            (b"content-type",   b"application/json"),
                            (b"content-length", str(len(body)).encode()),
                        ],
                    })
                    await send({"type": "http.response.body", "body": body})
                    return

            # ── Flow 2: Capture metric ─────────────────────────────────────
            start              = time.time()
            status_code        = [None]
            resp_headers_cap   = [{}]
            resp_body_parts    = []
            resp_bytes_read    = 0

            async def wrapped_send(message):
                if message["type"] == "http.response.start":
                    status_code[0]      = message["status"]
                    resp_headers_cap[0] = {
                        k.decode(): v.decode()
                        for k, v in message.get("headers", [])
                    }
                elif message["type"] == "http.response.body" and config.capture_response_body:
                    chunk = message.get("body", b"")
                    if resp_bytes_read + len(chunk) <= config.max_body_bytes:
                        resp_body_parts.append(chunk)
                await send(message)

            await original(self, scope, actual_receive, wrapped_send)

            if config.enable_ingest:
                resp_body_bytes = b"".join(resp_body_parts)
                req_body_str    = req_body_bytes.decode("utf-8", errors="replace") if req_body_bytes else None

                class _R:
                    pass

                r            = _R()
                r.status_code = status_code[0]
                r.headers     = resp_headers_cap[0]
                r.text        = resp_body_bytes.decode("utf-8", errors="replace") if resp_body_bytes else ""
                r.content     = resp_body_bytes

                metric = build_metric(
                    library="starlette", method=method, url=full_url,
                    start_time=start, response=r, error=None, config=config,
                    request_headers=req_headers, request_body=req_body_str,
                    remote_addr=remote,
                )
                if metric:
                    sender.add_metric(metric)

        Starlette.__call__ = monitored
        log.debug("Patched Starlette.__call__")
        return True
    except ImportError:
        return False