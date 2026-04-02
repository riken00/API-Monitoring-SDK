"""
collector.py — Builds the metric dict from a request/response pair.

Applies:
  - Sampling check
  - Body capture and truncation
  - bytes-prefix cleanup (b'...')
  - Timestamp normalisation to Unix epoch float
  - Identity resolution
  - PII sanitisation
  - SDKInfo attachment

Returns None if the request is sampled out. Never raises.
"""

import random
import time
from typing import Any, Dict, Optional

from . import identity as _identity
from . import sanitiser as _sanitiser
from .logger import get_logger

SDK_NAME = "apimonitor-python"
SDK_VERSION = "2.0.0"


def build_metric(
    library: str,
    method: str,
    url: str,
    start_time: float,
    response: Optional[Any],
    error: Optional[Exception],
    config,
    request_headers: Optional[Dict] = None,
    request_body: Optional[Any] = None,
    remote_addr: str = "",
) -> Optional[Dict]:
    """
    Build a metric dict ready to be queued for ingest.
    Returns None if sampling rejects this request.
    Never raises.
    """
    try:
        # Sampling — applied only to ingest, NOT to validation
        if random.random() > config.sampling_rate:
            return None

        duration_ms = round((time.time() - start_time) * 1000, 2)

        # Identity resolution
        id_result = _identity.resolve(
            headers=request_headers,
            body=request_body if config.capture_request_body else None,
            remote_addr=remote_addr,
            extra_headers=config.extra_identity_headers,
            extra_fields=config.extra_identity_fields,
        )

        # Sanitise headers
        clean_req_headers = _sanitiser.scrub_headers(
            request_headers, config.sanitize_fields
        ) if request_headers else None

        # Capture and sanitise request body
        req_body = None
        if config.capture_request_body and request_body is not None:
            req_body = _sanitiser.scrub_body(
                _truncate(request_body, config.max_body_bytes),
                config.sanitize_fields,
            )

        # Capture response fields
        status_code = None
        resp_headers = None
        resp_body = None

        if response is not None:
            status_code = getattr(response, "status_code", None)

            try:
                raw_resp_headers = getattr(response, "headers", {})
                resp_headers = _sanitiser.scrub_headers(
                    dict(raw_resp_headers), config.sanitize_fields
                )
            except Exception:
                pass

            if config.capture_response_body:
                try:
                    if hasattr(response, "text"):
                        resp_body = _sanitiser.scrub_body(
                            response.text[:config.max_body_bytes],
                            config.sanitize_fields,
                        )
                    elif hasattr(response, "content"):
                        resp_body = _sanitiser.scrub_body(
                            response.content.decode("utf-8", errors="replace")[:config.max_body_bytes],
                            config.sanitize_fields,
                        )
                except Exception:
                    pass

        metric = {
            "timestamp": round(start_time, 6),       # Unix epoch float, normalised
            "method": method.upper(),
            "url": url,
            "duration_ms": duration_ms,
            "status_code": status_code,
            "library": library,
            "request_headers": clean_req_headers,
            "request_body": req_body,
            "response_headers": resp_headers,
            "response_body": resp_body,
            "error": str(error) if error else None,
            **id_result.to_dict(),
            "sdk": {
                "name": SDK_NAME,
                "version": SDK_VERSION,
                "language": "python",
                "library": library,
            },
        }

        return metric

    except Exception:
        return None


def _truncate(body: Any, max_bytes: int) -> Any:
    """Truncate body to max_bytes. Handles str, bytes, and other types."""
    if isinstance(body, str):
        return body[:max_bytes]
    if isinstance(body, bytes):
        decoded = body.decode("utf-8", errors="replace")
        # Strip Python bytes prefix b'...'
        if decoded.startswith("b'") or decoded.startswith('b"'):
            decoded = decoded[2:-1]
        return decoded[:max_bytes]
    return body