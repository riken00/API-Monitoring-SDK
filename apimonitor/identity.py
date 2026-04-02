"""
identity.py — User identity resolution.

Resolution priority:
  1. JWT  — Authorization: Bearer, Cookie token
  2. Headers — X-User-Id, X-Account-Id, X-Client-Id, X-Tenant-Id, + extras
  3. Body — top-level and one-level-deep JSON field scan
  4. Fallback — SHA-256 hash of IP + User-Agent

Always returns an IdentityResult. Never raises.
"""

import base64
import hashlib
import json
from typing import Any, Dict, List, Optional


_IDENTITY_HEADERS = [
    "x-user-id", "x-account-id", "x-client-id",
    "x-tenant-id", "x-customer-id", "x-actor-id",
]

_IDENTITY_BODY_FIELDS = [
    "user_id", "userId", "user", "account_id", "accountId",
    "client_id", "clientId", "sub", "email", "uid",
]

_IP_HEADERS = [
    "cf-connecting-ip",
    "x-real-ip",
    "x-forwarded-for",
    "x-client-ip",
    # Django META keys
    "http_cf_connecting_ip",
    "http_x_real_ip",
    "http_x_forwarded_for",
]


class IdentityResult:
    __slots__ = ("identity_id", "identity_source", "ip_address")

    def __init__(self, identity_id: str, identity_source: str, ip_address: str = ""):
        self.identity_id = identity_id
        self.identity_source = identity_source  # jwt | header | body | ip_only
        self.ip_address = ip_address

    def to_dict(self) -> Dict[str, str]:
        return {
            "identity_id": self.identity_id,
            "identity_source": self.identity_source,
            "ip_address": self.ip_address,
        }


def resolve(
    headers: Optional[Dict[str, str]],
    body: Optional[Any],
    remote_addr: str = "",
    extra_headers: Optional[List[str]] = None,
    extra_fields: Optional[List[str]] = None,
) -> IdentityResult:
    """Resolve the best available identity for a request. Never raises."""
    try:
        norm = _norm_headers(headers or {})
        ip = _extract_ip(norm, remote_addr)

        # 1. JWT
        jwt_id = _from_jwt(norm)
        if jwt_id:
            return IdentityResult(_hash(jwt_id), "jwt", ip)

        # 2. Identity headers
        all_headers = _IDENTITY_HEADERS + [h.lower() for h in (extra_headers or [])]
        header_id = _from_headers(norm, all_headers)
        if header_id:
            return IdentityResult(_hash(header_id), "header", ip)

        # 3. Body fields
        all_fields = _IDENTITY_BODY_FIELDS + list(extra_fields or [])
        body_id = _from_body(body, all_fields)
        if body_id:
            return IdentityResult(_hash(body_id), "body", ip)

        # 4. IP + UA fallback
        ua = norm.get("user-agent", "")
        return IdentityResult(_hash(f"{ip}::{ua}"), "ip_only", ip)

    except Exception:
        return IdentityResult("unknown", "ip_only", "")


# ── internals ─────────────────────────────────────────────────────────────────

def _norm_headers(headers: Dict) -> Dict[str, str]:
    return {str(k).lower(): str(v) for k, v in headers.items()}


def _extract_ip(norm: Dict[str, str], remote_addr: str) -> str:
    for key in _IP_HEADERS:
        val = norm.get(key, "").strip()
        if val:
            return val.split(",")[0].strip()
    return remote_addr.strip()


def _from_jwt(norm: Dict[str, str]) -> Optional[str]:
    token = None
    auth = norm.get("authorization", "")
    if auth.lower().startswith("bearer "):
        token = auth[7:].strip()

    if not token:
        for part in norm.get("cookie", "").split(";"):
            k, _, v = part.strip().partition("=")
            if k.strip().lower() in ("token", "access_token", "jwt"):
                token = v.strip()
                break

    if not token:
        return None

    try:
        parts = token.split(".")
        if len(parts) < 2:
            return None
        padded = parts[1] + "=" * (-len(parts[1]) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded))
        for claim in ("sub", "user_id", "userId", "email", "uid"):
            val = payload.get(claim)
            if val and isinstance(val, str) and val.strip():
                return val.strip()
    except Exception:
        pass
    return None


def _from_headers(norm: Dict[str, str], keys: List[str]) -> Optional[str]:
    for key in keys:
        val = norm.get(key, "").strip()
        if val:
            return val
    return None


def _from_body(body: Any, fields: List[str]) -> Optional[str]:
    if body is None:
        return None
    parsed = None
    if isinstance(body, dict):
        parsed = body
    elif isinstance(body, (str, bytes)):
        try:
            raw = body if isinstance(body, str) else body.decode("utf-8", errors="replace")
            raw = raw.strip()
            # Strip Python bytes prefix b'...' if present
            if raw.startswith("b'") or raw.startswith('b"'):
                raw = raw[2:-1]
            if raw.startswith("{"):
                parsed = json.loads(raw)
        except Exception:
            pass

    if not isinstance(parsed, dict):
        return None

    for f in fields:
        val = parsed.get(f)
        if val and isinstance(val, str) and val.strip():
            return val.strip()

    for v in parsed.values():
        if isinstance(v, dict):
            for f in fields:
                val = v.get(f)
                if val and isinstance(val, str) and val.strip():
                    return val.strip()
    return None


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]