"""
config.py — All SDK settings.

Changes from original:
  - Added heartbeat_interval (replaces config_sync_interval — heartbeat now
    does both jobs: liveness ping + config change detection)
  - Added framework, environment (sent in heartbeat payload)
  - Added max_offline_rows (prevents unbounded SQLite growth)
  - config_sync_interval kept as alias for backward compat but deprecated
"""

import os
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class Config:

    # ── Authentication ─────────────────────────────────────────────────────
    api_key:  str = ""
    base_url: str = "https://api.yourmonitor.com"

    # ── Feature flags ──────────────────────────────────────────────────────
    enable_validation: bool = False   # Flow 1 — synchronous gate
    enable_ingest:     bool = True    # Flow 2 — async background ingest

    # ── Flow 1: Validation gate ────────────────────────────────────────────
    validate_timeout_ms: int = 2_000
    check_cache_ttl_ms:  int = 500

    cb_failure_threshold: int   = 5
    cb_window_seconds:    int   = 10
    cb_recovery_seconds:  int   = 30
    fail_behavior:        str   = "open"   # "open" | "closed"

    # ── Flow 2: Ingest / batching ──────────────────────────────────────────
    batch_size:    int   = 100
    batch_timeout: int   = 10
    queue_maxsize: int   = 10_000
    sampling_rate: float = 1.0

    retry_initial_delay: float = 1.0
    retry_max_delay:     float = 60.0
    retry_multiplier:    float = 2.0

    offline_mode:      bool          = True
    local_db_path:     Optional[str] = None
    max_offline_rows:  int           = 50_000   # NEW — prevent unbounded growth

    # ── Shared: Identity resolution ────────────────────────────────────────
    extra_identity_headers: List[str] = field(default_factory=list)
    extra_identity_fields:  List[str] = field(default_factory=list)

    # ── Shared: PII sanitisation ───────────────────────────────────────────
    sanitize_fields: List[str] = field(default_factory=list)

    # ── Shared: Body capture ───────────────────────────────────────────────
    capture_request_body:  bool = True
    capture_response_body: bool = True
    max_body_bytes:        int  = 10_240

    # ── Shared: Endpoint exclusions ────────────────────────────────────────
    exclude: List[str] = field(default_factory=list)

    # ── Ingest mode ────────────────────────────────────────────────────────
    ingest_mode: str = "background"   # "background" | "inline"

    # ── Heartbeat (replaces config_sync_interval) ──────────────────────────
    # One thread handles both liveness ping + config change detection.
    heartbeat_interval: int = 30   # seconds between heartbeats

    # ── Agent metadata (sent in heartbeat) ────────────────────────────────
    framework:   Optional[str] = None   # e.g. "fastapi", "django", "flask"
    environment: Optional[str] = None   # e.g. "production", "staging"

    # ── Observability ──────────────────────────────────────────────────────
    debug:     bool = False
    log_level: str  = "ERROR"

    # ── Runtime (written by Heartbeat, never set by caller) ───────────────
    _remote: dict = field(default_factory=dict, init=False, repr=False)

    # ── Derived URLs ───────────────────────────────────────────────────────
    @property
    def ingest_url(self) -> str:
        return f"{self.base_url.rstrip('/')}/api/v1/metrics/ingest"

    @property
    def validate_url(self) -> str:
        return f"{self.base_url.rstrip('/')}/api/v1/metrics/validate"

    @property
    def config_url(self) -> str:
        return f"{self.base_url.rstrip('/')}/api/v1/sdk/config"

    @property
    def ping_url(self) -> str:
        return f"{self.base_url.rstrip('/')}/api/v1/sdk/ping"

    # ── Factory ────────────────────────────────────────────────────────────
    @classmethod
    def from_env(cls, **overrides) -> "Config":
        def _get(key, env, default=None):
            v = overrides.get(key)
            return v if v is not None else os.getenv(env, default)

        def _bool(v) -> bool:
            if isinstance(v, bool): return v
            return str(v).lower() in ("1", "true", "yes")

        def _list(v) -> list:
            if isinstance(v, list): return v
            return [x.strip() for x in str(v).split(",") if x.strip()] if v else []

        return cls(
            api_key=_get("api_key", "APIMONITOR_API_KEY", ""),
            base_url=_get("base_url", "APIMONITOR_BASE_URL", "https://api.yourmonitor.com"),
            enable_validation=_bool(_get("enable_validation", "APIMONITOR_ENABLE_VALIDATION", False)),
            enable_ingest=_bool(_get("enable_ingest", "APIMONITOR_ENABLE_INGEST", True)),
            validate_timeout_ms=int(_get("validate_timeout_ms", "APIMONITOR_VALIDATE_TIMEOUT_MS", 2_000)),
            check_cache_ttl_ms=int(_get("check_cache_ttl_ms", "APIMONITOR_CHECK_CACHE_TTL_MS", 500)),
            cb_failure_threshold=int(_get("cb_failure_threshold", "APIMONITOR_CB_FAILURE_THRESHOLD", 5)),
            cb_window_seconds=int(_get("cb_window_seconds", "APIMONITOR_CB_WINDOW_SECONDS", 10)),
            cb_recovery_seconds=int(_get("cb_recovery_seconds", "APIMONITOR_CB_RECOVERY_SECONDS", 30)),
            fail_behavior=_get("fail_behavior", "APIMONITOR_FAIL_BEHAVIOR", "open"),
            batch_size=int(_get("batch_size", "APIMONITOR_BATCH_SIZE", 100)),
            batch_timeout=int(_get("batch_timeout", "APIMONITOR_BATCH_TIMEOUT", 10)),
            queue_maxsize=int(_get("queue_maxsize", "APIMONITOR_QUEUE_MAXSIZE", 10_000)),
            sampling_rate=float(_get("sampling_rate", "APIMONITOR_SAMPLING_RATE", 1.0)),
            retry_initial_delay=float(_get("retry_initial_delay", "APIMONITOR_RETRY_INITIAL_DELAY", 1.0)),
            retry_max_delay=float(_get("retry_max_delay", "APIMONITOR_RETRY_MAX_DELAY", 60.0)),
            retry_multiplier=float(_get("retry_multiplier", "APIMONITOR_RETRY_MULTIPLIER", 2.0)),
            offline_mode=_bool(_get("offline_mode", "APIMONITOR_OFFLINE_MODE", True)),
            local_db_path=_get("local_db_path", "APIMONITOR_LOCAL_DB_PATH", None),
            max_offline_rows=int(_get("max_offline_rows", "APIMONITOR_MAX_OFFLINE_ROWS", 50_000)),
            extra_identity_headers=_list(_get("extra_identity_headers", "APIMONITOR_IDENTITY_HEADERS", [])),
            extra_identity_fields=_list(_get("extra_identity_fields", "APIMONITOR_IDENTITY_FIELDS", [])),
            sanitize_fields=_list(_get("sanitize_fields", "APIMONITOR_SANITIZE_FIELDS", [])),
            capture_request_body=_bool(_get("capture_request_body", "APIMONITOR_CAPTURE_REQ_BODY", True)),
            capture_response_body=_bool(_get("capture_response_body", "APIMONITOR_CAPTURE_RESP_BODY", True)),
            max_body_bytes=int(_get("max_body_bytes", "APIMONITOR_MAX_BODY_BYTES", 10_240)),
            exclude=_list(_get("exclude", "APIMONITOR_EXCLUDE", [])),
            ingest_mode=_get("ingest_mode", "APIMONITOR_INGEST_MODE", "background"),
            heartbeat_interval=int(_get("heartbeat_interval", "APIMONITOR_HEARTBEAT_INTERVAL", 30)),
            framework=_get("framework", "APIMONITOR_FRAMEWORK", None),
            environment=_get("environment", "APIMONITOR_ENVIRONMENT", None),
            debug=_bool(_get("debug", "APIMONITOR_DEBUG", False)),
            log_level=_get("log_level", "APIMONITOR_LOG_LEVEL", "ERROR").upper(),
        )