"""
monitor.py — Singleton orchestrator.

Changes from original:
  - Uses Heartbeat instead of ConfigSync (one thread, two jobs)
  - diagnose() now shows which frameworks were successfully patched
  - Fork detection reinitialises Heartbeat too
"""

from __future__ import annotations

import os
import threading
import time
from typing import Optional

from .config import Config
from .logger import get_logger, reset_logger
from .queue import OfflineQueue
from .sender import MetricSender
from .validator import Validator
from .heartbeat import Heartbeat
from . import interceptor, patcher


class Monitor:
    _instance: Optional["Monitor"] = None
    _lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, api_key: Optional[str] = None, **kwargs):
        if getattr(self, "_initialized", False) and self._pid == os.getpid():
            return

        self.config = Config.from_env(api_key=api_key, **kwargs)
        self._log   = get_logger(self.config.log_level, self.config.debug)

        if not self.config.api_key:
            self._log.warning("No API key provided — SDK will not send data.")

        self._offline   = OfflineQueue(self.config.local_db_path, self.config.max_offline_rows) if self.config.offline_mode else None
        self.sender     = MetricSender(self.config, self._offline)
        self.validator  = Validator(self.config)
        self._heartbeat = Heartbeat(self.config)

        self._started     = False
        self._ref_count   = 0
        self._ref_lock    = threading.Lock()
        self._initialized = True
        self._pid         = os.getpid()
        self._started_at: Optional[float] = None

        # Track which frameworks were successfully patched
        self._patched_frameworks: list = []
        self._patched_libraries:  list = []

        import atexit
        atexit.register(self.stop)

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> "Monitor":
        self._check_fork()
        with self._ref_lock:
            self._ref_count += 1
            if self._started:
                return self

            self._patched_frameworks = patcher.install(self.config, self.sender, self.validator)
            self._patched_libraries  = interceptor.install(self.config, self.sender, self.validator)

            if self.config.enable_ingest:
                self.sender.start()

            self._heartbeat.start()

            self._started    = True
            self._started_at = time.time()

            self._log.info(
                "Started — validation=%s ingest=%s sampling=%.0f%% "
                "frameworks=%s libraries=%s",
                self.config.enable_validation,
                self.config.enable_ingest,
                self.config.sampling_rate * 100,
                self._patched_frameworks,
                self._patched_libraries,
            )
        return self

    def stop(self) -> None:
        if self._pid != os.getpid():
            return
        with self._ref_lock:
            if not self._started or self._ref_count == 0:
                return
            self._ref_count -= 1
            if self._ref_count > 0:
                return

            interceptor.uninstall()
            self._heartbeat.stop()

            if self.config.enable_ingest:
                self.sender.stop()

            self._started = False
            self._log.info("Stopped and flushed.")

    # ── Custom events ─────────────────────────────────────────────────────────

    def track(self, event_name: str, **metadata) -> None:
        if not self.config.enable_ingest:
            return
        self.sender.add_metric({
            "type":       "custom_event",
            "event_name": event_name,
            "timestamp":  time.time(),
            "metadata":   metadata,
        })

    # ── Diagnostics ───────────────────────────────────────────────────────────

    def diagnose(self) -> dict:
        offline_size = self._offline.size() if self._offline else 0
        return {
            "started":               self._started,
            "pid":                   os.getpid(),
            "uptime_seconds":        round(time.time() - self._started_at, 1) if self._started_at else 0,
            "api_key_set":           bool(self.config.api_key),
            "enable_validation":     self.config.enable_validation,
            "enable_ingest":         self.config.enable_ingest,
            "fail_behavior":         self.config.fail_behavior,
            "sampling_rate":         self.config.sampling_rate,
            "batch_size":            self.config.batch_size,
            "batch_timeout":         self.config.batch_timeout,
            "ingest_mode":           self.config.ingest_mode,
            "circuit_breaker_state": self.validator._breaker.state,
            "offline_queue_size":    offline_size,
            "platform_base_url":     self.config.base_url,
            "patched_frameworks":    self._patched_frameworks,   # NEW
            "patched_libraries":     self._patched_libraries,    # NEW
            "heartbeat_interval":    self.config.heartbeat_interval,
        }

    # ── Fork detection ────────────────────────────────────────────────────────

    def _check_fork(self) -> None:
        current_pid = os.getpid()
        if self._pid != current_pid:
            self._log.info("Fork detected (old=%s new=%s) — restarting components",
                           self._pid, current_pid)
            self._pid       = current_pid
            self._started   = False
            self._ref_count = 0
            reset_logger()
            self._log       = get_logger(self.config.log_level, self.config.debug)
            self._offline   = OfflineQueue(self.config.local_db_path, self.config.max_offline_rows) if self.config.offline_mode else None
            self.sender     = MetricSender(self.config, self._offline)
            self.validator  = Validator(self.config)
            self._heartbeat = Heartbeat(self.config)