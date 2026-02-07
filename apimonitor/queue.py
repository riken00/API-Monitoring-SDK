"""SQLite-based offline queue for when network fails"""

import sqlite3
import json
import os
from typing import List, Dict

class OfflineQueue:
    def __init__(self, db_path: str = None):
        if db_path is None:
            db_path = os.path.expanduser("~/.apimonitor_queue.db")
        
        self.db_path = db_path
        self._init_db()
    
    def _init_db(self):
        """Create table if not exists"""
        conn = sqlite3.connect(self.db_path)
        conn.execute('''
            CREATE TABLE IF NOT EXISTS metrics_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                data TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        conn.commit()
        conn.close()
    
    def save_batch(self, metrics: List[Dict]):
        """Save metrics to offline queue"""
        conn = sqlite3.connect(self.db_path)
        for metric in metrics:
            conn.execute(
                'INSERT INTO metrics_queue (data) VALUES (?)',
                (json.dumps(metric),)
            )
        conn.commit()
        conn.close()
    
    def get_batch(self, limit: int = 100) -> List[Dict]:
        """Get metrics from queue"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.execute(
            'SELECT data FROM metrics_queue ORDER BY id LIMIT ?',
            (limit,)
        )
        rows = cursor.fetchall()
        conn.close()
        return [json.loads(row[0]) for row in rows]
    
    def clear_batch(self, count: int):
        """Remove sent metrics from queue"""
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            'DELETE FROM metrics_queue WHERE id IN (SELECT id FROM metrics_queue ORDER BY id LIMIT ?)',
            (count,)
        )
        conn.commit()
        conn.close()