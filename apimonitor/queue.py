"""
queue.py — SQLite offline buffer.

Changes from original:
  - max_rows parameter prevents unbounded file growth
  - When full, oldest rows are dropped to make room for new ones
  - WAL mode + NORMAL sync for concurrent Gunicorn workers
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from typing import Dict, List, Optional


class OfflineQueue:

    def __init__(self, db_path: Optional[str] = None, max_rows: int = 50_000):
        if db_path is None:
            db_path = os.path.expanduser("~/.apimonitor_queue.db")
        self._db_path  = db_path
        self._max_rows = max_rows
        self._lock     = threading.Lock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _init_db(self) -> None:
        with self._lock:
            conn = self._connect()
            conn.execute("""
                CREATE TABLE IF NOT EXISTS metrics_queue (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    data       TEXT    NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_created ON metrics_queue(created_at)")
            conn.commit()
            conn.close()

    def save_batch(self, metrics: List[Dict]) -> None:
        if not metrics:
            return
        with self._lock:
            conn = self._connect()
            try:
                # Check current size and drop oldest if needed to stay under cap
                (current,) = conn.execute("SELECT COUNT(*) FROM metrics_queue").fetchone()
                headroom   = self._max_rows - current
                if headroom <= 0:
                    # Drop oldest 10% to make room
                    drop_n = max(len(metrics), self._max_rows // 10)
                    conn.execute(
                        "DELETE FROM metrics_queue WHERE id IN "
                        "(SELECT id FROM metrics_queue ORDER BY id ASC LIMIT ?)",
                        (drop_n,),
                    )
                elif headroom < len(metrics):
                    # Only save what fits
                    metrics = metrics[:headroom]

                conn.executemany(
                    "INSERT INTO metrics_queue (data) VALUES (?)",
                    [(json.dumps(m),) for m in metrics],
                )
                conn.commit()
            finally:
                conn.close()

    def get_batch(self, limit: int = 100) -> List[Dict]:
        with self._lock:
            conn    = self._connect()
            cursor  = conn.execute(
                "SELECT data FROM metrics_queue ORDER BY id ASC LIMIT ?", (limit,)
            )
            rows    = cursor.fetchall()
            conn.close()
        result = []
        for (raw,) in rows:
            try:
                result.append(json.loads(raw))
            except Exception:
                pass
        return result

    def clear_batch(self, count: int) -> None:
        with self._lock:
            conn = self._connect()
            conn.execute(
                "DELETE FROM metrics_queue WHERE id IN "
                "(SELECT id FROM metrics_queue ORDER BY id ASC LIMIT ?)",
                (count,),
            )
            conn.commit()
            conn.close()

    def size(self) -> int:
        with self._lock:
            conn  = self._connect()
            (n,)  = conn.execute("SELECT COUNT(*) FROM metrics_queue").fetchone()
            conn.close()
        return n