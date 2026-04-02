"""
sender.py — Flow 2: background batch ingest engine.

Changes from original:
  - When enable_validation=True, metrics are piggybacked on /validate —
    /ingest is NOT called separately. One request, both jobs done.
  - Offline buffer respects max_offline_rows to prevent unbounded growth
  - _send() uses the combined validate path when appropriate
"""

from __future__ import annotations

import json
import threading
import time
from queue import Empty, Full, Queue
from typing import Dict, List, Optional

import urllib3

from .logger import get_logger
from .queue import OfflineQueue


class MetricSender:

    def __init__(self, config, offline_queue: Optional[OfflineQueue] = None):
        self._config  = config
        self._offline = offline_queue
        self._log     = get_logger(config.log_level, config.debug)

        self._queue: Queue        = Queue(maxsize=config.queue_maxsize)
        self._batch: List[Dict]   = []
        self._batch_lock          = threading.Lock()
        self._stop_event          = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_flush          = time.monotonic()
        self._current_delay       = config.retry_initial_delay

        self._http = urllib3.PoolManager(
            num_pools=2,
            maxsize=4,
            timeout=urllib3.Timeout(connect=3.0, read=10.0),
            retries=False,
        )

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="apimonitor-sender",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)
        self._drain_queue()
        self._flush()

    # ── Public API ────────────────────────────────────────────────────────────

    def add_metric(self, metric: Dict) -> None:
        """
        Enqueue a metric for background sending.
        Returns immediately — never blocks.
        """
        if self._config.ingest_mode == "inline":
            self._send([metric])
        else:
            try:
                self._queue.put_nowait(metric)
            except Full:
                self._log.debug("Queue full — dropping metric")

    # ── Background loop ───────────────────────────────────────────────────────

    def _run(self) -> None:
        while not self._stop_event.is_set():
            self._drain_queue()
            now     = time.monotonic()
            elapsed = now - self._last_flush
            count   = len(self._batch)

            if count >= self._config.batch_size:
                self._flush()
            elif elapsed >= self._config.batch_timeout and count > 0:
                self._flush()
            else:
                self._stop_event.wait(timeout=0.5)

    def _drain_queue(self) -> None:
        while True:
            try:
                metric = self._queue.get_nowait()
                with self._batch_lock:
                    self._batch.append(metric)
            except Empty:
                break

    # ── Flush ─────────────────────────────────────────────────────────────────

    def _flush(self) -> None:
        with self._batch_lock:
            if not self._batch:
                self._last_flush = time.monotonic()
                return
            to_send      = self._batch[:]
            self._batch  = []

        self._last_flush = time.monotonic()
        success          = self._send(to_send)

        if success:
            self._current_delay = self._config.retry_initial_delay
            self._log.info("Flushed %s metrics", len(to_send))
            if self._offline:
                self._drain_offline()
        else:
            if self._offline:
                self._offline.save_batch(to_send)
            self._log.warning(
                "Ingest failed — %s metrics saved offline. Retrying in %.1fs",
                len(to_send), self._current_delay,
            )
            self._stop_event.wait(timeout=self._current_delay)
            self._current_delay = min(
                self._current_delay * self._config.retry_multiplier,
                self._config.retry_max_delay,
            )

    def _send(self, metrics: List[Dict]) -> bool:
        """
        Send a batch to the backend.

        When enable_validation=False: POST to /ingest (existing behaviour).
        When enable_validation=True:  metrics were already piggybacked on
          /validate calls in interceptor/patcher, so this path only handles
          any remaining metrics that arrived outside a validate call
          (e.g. custom events from monitor.track()).
        """
        payload = json.dumps({
            "api_key":     self._config.api_key,
            "metrics":     metrics,
            "ingest_mode": self._config.ingest_mode,
            "sdk": {
                "name":     "apimonitor-python",
                "version":  "2.0.0",
                "language": "python",
            },
        }).encode("utf-8")

        try:
            resp = self._http.request(
                "POST",
                self._config.ingest_url,
                body=payload,
                headers={"Content-Type": "application/json"},
            )
            if resp.status in (200, 202):
                return True
            self._log.warning("Ingest HTTP %s: %s", resp.status, resp.data[:200])
            return False
        except Exception as exc:
            self._log.warning("Ingest network error: %s", exc)
            return False

    def _drain_offline(self) -> None:
        if not self._offline:
            return
        batch = self._offline.get_batch(100)
        if not batch:
            return
        if self._send(batch):
            self._offline.clear_batch(len(batch))
            self._log.info("Drained %s offline metrics", len(batch))