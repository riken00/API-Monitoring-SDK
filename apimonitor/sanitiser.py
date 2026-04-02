"""
sanitiser.py — PII scrubber.

Applied to request/response bodies and headers BEFORE any metric
is queued or sent. Always returns a copy — never mutates the original.
"""

import copy
import json
import re
from typing import Any, Dict, List, Optional, Union

# Built-in patterns always applied
_PATTERNS = [
    re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+"),   # email
    re.compile(r"\b(?:\d[ -]?){13,16}\b"),                               # credit card
    re.compile(r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b"),  # phone
]

_REDACTED = "[REDACTED]"


def scrub_body(body: Any, sanitize_fields: Optional[List[str]] = None) -> Any:
    """
    Scrub PII from a request or response body.
    Accepts a dict, JSON string, bytes, or raw string.
    Returns the same type that was passed in.
    """
    if body is None:
        return None

    original_type = type(body)

    # Normalise to string
    if isinstance(body, bytes):
        body_str = body.decode("utf-8", errors="replace")
        # Strip Python bytes prefix
        if body_str.startswith("b'") or body_str.startswith('b"'):
            body_str = body_str[2:-1]
    elif isinstance(body, dict):
        body_str = None  # handled directly below
    else:
        body_str = str(body)
        if body_str.startswith("b'") or body_str.startswith('b"'):
            body_str = body_str[2:-1]

    # Dict path — scrub in-place on a deep copy
    if isinstance(body, dict):
        cleaned = _scrub_dict(copy.deepcopy(body), sanitize_fields or [])
        return cleaned

    # JSON string path
    if body_str and body_str.strip().startswith("{"):
        try:
            parsed = json.loads(body_str)
            if isinstance(parsed, dict):
                cleaned = _scrub_dict(parsed, sanitize_fields or [])
                result_str = json.dumps(cleaned)
                if original_type is bytes:
                    return result_str.encode("utf-8")
                return result_str
        except Exception:
            pass

    # Plain string path — apply regex patterns only
    if body_str:
        result = _scrub_string(body_str)
        if original_type is bytes:
            return result.encode("utf-8")
        return result

    return body


def scrub_headers(
    headers: Optional[Dict[str, str]],
    sanitize_fields: Optional[List[str]] = None,
) -> Optional[Dict[str, str]]:
    """Scrub PII from a headers dict. Returns a new dict."""
    if not headers:
        return headers

    blocked = {f.lower() for f in (sanitize_fields or [])} | {
        "authorization", "x-api-key", "cookie", "set-cookie", "proxy-authorization"
    }
    result = {}
    for k, v in headers.items():
        if str(k).lower() in blocked:
            result[k] = _REDACTED
        else:
            result[k] = _scrub_string(str(v)) if v else v
    return result


# ── internals ─────────────────────────────────────────────────────────────────

def _scrub_dict(d: Dict, sanitize_fields: List[str]) -> Dict:
    blocked = {f.lower() for f in sanitize_fields}
    for k, v in d.items():
        if str(k).lower() in blocked:
            d[k] = _REDACTED
        elif isinstance(v, str):
            d[k] = _scrub_string(v)
        elif isinstance(v, dict):
            d[k] = _scrub_dict(v, sanitize_fields)
        elif isinstance(v, list):
            d[k] = [
                _scrub_dict(i, sanitize_fields) if isinstance(i, dict)
                else (_scrub_string(i) if isinstance(i, str) else i)
                for i in v
            ]
    return d


def _scrub_string(value: str) -> str:
    for pattern in _PATTERNS:
        value = pattern.sub(_REDACTED, value)
    return value