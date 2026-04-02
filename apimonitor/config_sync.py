"""
config_sync.py — Fetches remote config from /v1/config every N seconds
and writes updates into Config._remote so all components pick them up
without a restart.

Runs as a daemon thread — silently does nothing if the platform is unreachable.
"""

import json
import threading
import time
import urllib.request
import urllib.error

from .logger import get_logger

class ConfigSync:

    def __init__(self, config):
        self._config = config
        self._log = get_logger(config.log_level, config.debug)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        # Fetch once immediately on startup so config is fresh before traffic
        self._fetch()
        self._thread = threading.Thread(
            target=self._run,
            name="apimonitor-config-sync",
            daemon=True,
        )
        self._thread.start()
        self._log.debug("ConfigSync started (interval=%ss)", self._config.config_sync_interval)

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)

    def _run(self) -> None:
        while not self._stop.wait(timeout=self._config.config_sync_interval):
            self._fetch()

    def _fetch(self) -> None:
        try:
            url = f"{self._config.config_url}?api_key={self._config.api_key}"
            req = urllib.request.Request(
                url,
                method="GET",
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                remote = json.loads(resp.read().decode("utf-8"))
                self._apply(remote)
                self._log.debug("Remote config refreshed: %s", list(remote.keys()))
        except Exception as exc:
            self._log.debug("Config sync skipped: %s", exc)

    def _apply(self, remote: dict) -> None:
        """
        Write remote values into Config._remote and also apply them
        to the live config fields that are safe to hot-reload.
        """
        self._config._remote = remote

        # Hot-reloadable fields — applied immediately without restart
        if "sampling_rate" in remote:
            self._config.sampling_rate = float(remote["sampling_rate"])
        if "batch_size" in remote:
            self._config.batch_size = int(remote["batch_size"])
        if "batch_timeout" in remote:
            self._config.batch_timeout = int(remote["batch_timeout"])
        if "exclude" in remote:
            self._config.exclude = list(remote["exclude"])
        if "fail_behavior" in remote:
            self._config.fail_behavior = str(remote["fail_behavior"])
        if "enable_validation" in remote:
            self._config.enable_validation = bool(remote["enable_validation"])
        if "enable_ingest" in remote:
            self._config.enable_ingest = bool(remote["enable_ingest"])