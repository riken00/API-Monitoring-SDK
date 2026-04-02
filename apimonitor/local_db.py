"""Local database for storing metrics in non-blocking mode"""

import sqlite3
import json
import time
from typing import List, Dict, Optional
from threading import Lock

class LocalDB:
    """SQLite database for local metrics storage"""
    
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.lock = Lock()
        self._init_db()
    
    def _init_db(self):
        """Initialize database schema"""
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Create metrics table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS metrics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL,
                    method TEXT NOT NULL,
                    url TEXT NOT NULL,
                    duration_ms REAL NOT NULL,
                    status_code INTEGER,
                    error TEXT,
                    synced BOOLEAN DEFAULT 0,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Create indexes
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_synced ON metrics(synced)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_timestamp ON metrics(timestamp)')
            
            conn.commit()
            conn.close()
    
    def insert_metric(self, metric: Dict):
        """Insert a single metric"""
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute('''
                INSERT INTO metrics (timestamp, method, url, duration_ms, status_code, error, synced)
                VALUES (?, ?, ?, ?, ?, ?, 0)
            ''', (
                metric['timestamp'],
                metric['method'],
                metric['url'],
                metric['duration_ms'],
                metric.get('status_code'),
                metric.get('error')
            ))
            
            conn.commit()
            conn.close()
    
    def get_unsynced_metrics(self, limit: int = 100) -> List[Dict]:
        """Get unsynced metrics for batch sync"""
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute('''
                SELECT id, timestamp, method, url, duration_ms, status_code, error
                FROM metrics
                WHERE synced = 0
                ORDER BY timestamp ASC
                LIMIT ?
            ''', (limit,))
            
            rows = cursor.fetchall()
            conn.close()
            
            metrics = []
            for row in rows:
                metrics.append({
                    'id': row[0],
                    'timestamp': row[1],
                    'method': row[2],
                    'url': row[3],
                    'duration_ms': row[4],
                    'status_code': row[5],
                    'error': row[6]
                })
            
            return metrics
    
    def mark_as_synced(self, metric_ids: List[int]):
        """Mark metrics as synced"""
        if not metric_ids:
            return
        
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            placeholders = ','.join('?' * len(metric_ids))
            cursor.execute(f'''
                UPDATE metrics
                SET synced = 1
                WHERE id IN ({placeholders})
            ''', metric_ids)
            
            conn.commit()
            conn.close()
    
    def get_stats(self) -> Dict:
        """Get database statistics"""
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute('SELECT COUNT(*) FROM metrics WHERE synced = 0')
            unsynced_count = cursor.fetchone()[0]
            
            cursor.execute('SELECT COUNT(*) FROM metrics WHERE synced = 1')
            synced_count = cursor.fetchone()[0]
            
            conn.close()
            
            return {
                'total_metrics': unsynced_count + synced_count,
                'unsynced_metrics': unsynced_count,
                'synced_metrics': synced_count
            }
    
    def cleanup_old_metrics(self, days: int = 7):
        """Delete synced metrics older than N days"""
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cutoff_time = time.time() - (days * 24 * 60 * 60)
            cursor.execute('''
                DELETE FROM metrics
                WHERE synced = 1 AND timestamp < ?
            ''', (cutoff_time,))
            
            deleted_count = cursor.rowcount
            conn.commit()
            conn.close()
            
            return deleted_count
