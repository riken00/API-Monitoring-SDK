"""
heartbeat.py — SDK heartbeat + config sync combined.

Replaces the separate config_sync.py polling approach.

On startup:
  1. Calls POST /ping → gets {config_changed, server_time}
  2. If config_changed=True → immediately fetches GET /config
  3. Applies hot-reloadable config fields

Every heartbeat_interval seconds (default 30s):
  1. Same as above

This means:
  - The backend always knows which SDK instances are alive (sdk_agents table)
  - Config changes propagate within heartbeat_interval seconds
  - No separate polling thread needed
  - One thread does both jobs

The old config_sync.py is replaced by this file entirely.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from typing import Optional

from .logger import get_logger


class Heartbeat:

    def __init__(self, config):
        self._config = config
        self._log    = get_logger(config.log_level, config.debug)
        self._stop   = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        # Ping once immediately on startup
        self._ping()

        self._thread = threading.Thread(
            target=self._run,
            name="apimonitor-heartbeat",
            daemon=True,
        )
        self._thread.start()
        self._log.debug(
            "Heartbeat started (interval=%ds)", self._config.heartbeat_interval
        )

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)

    def _run(self) -> None:
        while not self._stop.wait(timeout=self._config.heartbeat_interval):
            self._ping()

    def _ping(self) -> None:
        """
        POST /ping → check if config has changed → fetch if needed.
        Silently does nothing if the platform is unreachable.
        """
        import hashlib

        config_hash = self._current_config_hash()

        payload = json.dumps({
            "api_key":     self._config.api_key,
            "sdk_version": "2.0.0",
            "runtime":     self._get_runtime(),
            "framework":   self._config.framework,
            "hostname":    self._get_hostname(),
            "config_hash": config_hash,
            "environment": self._config.environment,
        }).encode("utf-8")

        try:
            print(f"{self._config.base_url.rstrip('/')}/api/v1/sdk/ping",'-------------------------')
            req = urllib.request.Request(
                f"{self._config.base_url.rstrip('/')}/api/v1/sdk/ping",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=3) as resp:
                body = json.loads(resp.read().decode("utf-8"))
                if body.get("config_changed"):
                    self._log.debug("Config changed signal received — fetching new config")
                    self._fetch_config()
                else:
                    self._log.debug("Heartbeat ok — config unchanged")

        except Exception as exc:
            self._log.debug("Heartbeat skipped (platform unreachable): %s", exc)

    def _fetch_config(self) -> None:
        """Fetch GET /config and apply hot-reloadable fields."""
        try:
            url = f"{self._config.base_url.rstrip('/')}/api/v1/sdk/config?api_key={self._config.api_key}"
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=5) as resp:
                remote = json.loads(resp.read().decode("utf-8"))
                self._apply(remote)
                self._log.info("Remote config applied: %s", list(remote.keys()))
        except Exception as exc:
            self._log.debug("Config fetch failed: %s", exc)

    def _apply(self, remote: dict) -> None:
        """Apply hot-reloadable fields to the live config."""
        self._config._remote = remote

        reloadable = {
            "sampling_rate":      float,
            "batch_size":         int,
            "batch_timeout":      int,
            "fail_behavior":      str,
            "enable_validation":  bool,
            "enable_ingest":      bool,
            "ingest_mode":        str,     # ← was missing from old config_sync
            "max_body_bytes":     int,
            "capture_request_body":  bool,
            "capture_response_body": bool,
        }
        for field, cast in reloadable.items():
            if field in remote:
                try:
                    setattr(self._config, field, cast(remote[field]))
                except Exception:
                    pass

        if "exclude" in remote:
            self._config.exclude = list(remote["exclude"])

        if "sanitize_fields" in remote:
            self._config.sanitize_fields = list(remote["sanitize_fields"])

        # Update circuit breaker params on the live Validator instance
        # (fix for: CB config changes from control panel had no effect)
        self._update_circuit_breaker(remote)

    def _update_circuit_breaker(self, remote: dict) -> None:
        """
        Push new CB thresholds to the live CircuitBreaker instance.
        This was broken in the original — the CB was constructed once and
        never updated even if remote config changed.
        """
        cb_fields = {
            "cb_failure_threshold": "cb_failure_threshold",
            "cb_window_seconds":    "cb_window_seconds",
            "cb_recovery_seconds":  "cb_recovery_seconds",
        }
        updated = False
        for remote_key, config_key in cb_fields.items():
            if remote_key in remote:
                try:
                    setattr(self._config, config_key, int(remote[remote_key]))
                    updated = True
                except Exception:
                    pass

        if updated:
            # Find the Validator's CircuitBreaker and update its thresholds live
            try:
                from .monitor import Monitor
                m = Monitor._instance
                if m and hasattr(m, "validator") and hasattr(m.validator, "_breaker"):
                    b = m.validator._breaker
                    b._threshold = self._config.cb_failure_threshold
                    b._window    = self._config.cb_window_seconds
                    b._recovery  = self._config.cb_recovery_seconds
                    self._log.debug("Circuit breaker thresholds updated live")
            except Exception:
                pass

    def _current_config_hash(self) -> str:
        """SHA-256 of the current config for change detection."""
        import hashlib
        snapshot = {
            "sampling_rate":         self._config.sampling_rate,
            "batch_size":            self._config.batch_size,
            "batch_timeout":         self._config.batch_timeout,
            "enable_validation":     self._config.enable_validation,
            "enable_ingest":         self._config.enable_ingest,
            "fail_behavior":         self._config.fail_behavior,
            "ingest_mode":           self._config.ingest_mode,
            "cb_failure_threshold":  self._config.cb_failure_threshold,
            "cb_window_seconds":     self._config.cb_window_seconds,
            "cb_recovery_seconds":   self._config.cb_recovery_seconds,
        }
        return hashlib.sha256(
            json.dumps(snapshot, sort_keys=True).encode()
        ).hexdigest()

    def _get_runtime(self) -> str:
        import platform
        return f"python{platform.python_version()}"

    def _get_hostname(self) -> str:
        import socket
        try:
            return socket.gethostname()
        except Exception:
            return "unknown"