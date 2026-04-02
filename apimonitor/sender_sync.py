"""
SyncSender — Non-blocking metric sender for the apimonitor SDK.

Strategy:
  1. add_metric() writes immediately to a local SQLite DB (never blocks the caller).
  2. A background thread wakes up every `sync_interval_seconds` and POSTs any
     unsynced rows to the backend, then marks them synced.

This means metrics survive a backend outage: they stay in the local DB and are
retried on the next sync cycle.
"""

import threading
import time
import json
import urllib3
from typing import Optional, Dict, List


class SyncSender:
    """
    Non-blocking metric sender — writes to local SQLite instantly,
    syncs to backend in the background.
    """

    def __init__(self, config, local_db=None):
        self.config = config
        self.local_db = local_db          # LocalDB instance (required for this mode)
        self.stop_event = threading.Event()
        self.thread: Optional[threading.Thread] = None
        self.last_sync = time.time()

    # ── lifecycle ──────────────────────────────────────────────────────────────

    def start(self):
        """Start the background sync thread."""
        if self.local_db is None:
            if self.config.debug:
                print("[APIMonitor] SyncSender: no local_db provided, sync disabled")
            return
        self.thread = threading.Thread(target=self._run, daemon=True, name="apimonitor-sync")
        self.thread.start()
        if self.config.debug:
            print(f"[APIMonitor] SyncSender started (interval={self.config.sync_interval_seconds}s)")

    def stop(self):
        """Stop the background thread and do a final sync."""
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=10)
        # Final flush so nothing is left unsynced on clean shutdown
        self._sync_local_db()
        if self.config.debug:
            print("[APIMonitor] SyncSender stopped")

    # ── public API ─────────────────────────────────────────────────────────────

    def add_metric(self, metric: Dict):
        """
        Store a metric instantly in the local SQLite DB.
        Never blocks — the background thread will push it to the backend later.
        """
        if self.local_db is None:
            return
        try:
            self.local_db.insert_metric(metric)
        except Exception as e:
            if self.config.debug:
                print(f"[APIMonitor] Failed to write metric to local DB: {e}")

    def flush_now(self):
        """Force an immediate sync to the backend (useful in tests)."""
        self._sync_local_db()

    # ── background loop ────────────────────────────────────────────────────────

    def _run(self):
        """Background thread: sync every sync_interval_seconds."""
        while not self.stop_event.is_set():
            elapsed = time.time() - self.last_sync
            if elapsed >= self.config.sync_interval_seconds:
                self._sync_local_db()
                self.last_sync = time.time()
            # Sleep in small increments so stop_event is checked promptly
            self.stop_event.wait(timeout=min(5, self.config.sync_interval_seconds))

    # ── sync logic ─────────────────────────────────────────────────────────────

    def _sync_local_db(self):
        """Sync unsynced metrics from local database to backend"""
        if not self.local_db:
            return

        try:
            # Get unsynced metrics
            unsynced_metrics = self.local_db.get_unsynced_metrics(limit=100)

            if not unsynced_metrics:
                return

            if self.config.debug:
                print(f"[APIMonitor] Syncing {len(unsynced_metrics)} metrics from local DB...")

            # Prepare batch (remove 'id' field before sending)
            batch_to_send = []
            metric_ids = []

            for metric in unsynced_metrics:
                metric_ids.append(metric['id'])
                # Remove id before sending
                metric_copy = {k: v for k, v in metric.items() if k != 'id'}
                batch_to_send.append(metric_copy)

            # Send to backend
            http = urllib3.PoolManager()

            payload = {
                'api_key': self.config.api_key,
                'metrics': batch_to_send
            }

            response = http.request(
                'POST',
                self.config.endpoint,
                body=json.dumps(payload),
                headers={'Content-Type': 'application/json'},
                timeout=10.0
            )

            if response.status == 200:
                # Mark as synced in local database
                self.local_db.mark_as_synced(metric_ids)

                if self.config.debug:
                    print(f"[APIMonitor] Successfully synced {len(metric_ids)} metrics")
            else:
                if self.config.debug:
                    print(f"[APIMonitor] Sync failed: HTTP {response.status}")

        except Exception as e:
            if self.config.debug:
                print(f"[APIMonitor] Sync error: {e}")

