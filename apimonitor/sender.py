"""Background thread for sending metrics in batches"""

import threading
import time
from queue import Queue, Empty
from typing import Optional, Dict, List

class MetricSender:
    def __init__(self, config, offline_queue: Optional['OfflineQueue'] = None):
        self.config = config
        self.offline_queue = offline_queue
        self.queue = Queue()
        self.batch: List[Dict] = []
        self.thread: Optional[threading.Thread] = None
        self.stop_event = threading.Event()
        self.last_send = time.time()
    
    def start(self):
        """Start background thread"""
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
    
    def stop(self):
        """Stop and flush remaining metrics"""
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=5)
        self._flush()
    
    def add_metric(self, metric: Dict):
        """Add metric to queue"""
        self.queue.put(metric)
    
    def _run(self):
        """Background thread main loop"""
        while not self.stop_event.is_set():
            try:
                # Get metric with timeout
                metric = self.queue.get(timeout=1)
                self.batch.append(metric)
                
                # Send if batch full or timeout reached
                if len(self.batch) >= self.config.batch_size or \
                   (time.time() - self.last_send) >= self.config.batch_timeout:
                    self._flush()
                    
            except Empty:
                # Timeout - check if we should flush anyway
                if self.batch and (time.time() - self.last_send) >= self.config.batch_timeout:
                    self._flush()
    
    def _flush(self):
        """Send batch to API"""
        if not self.batch:
            return
        
        try:
            # ← CRITICAL FIX: Use urllib3 instead of requests to avoid infinite loop
            import urllib3
            import json
            
            http = urllib3.PoolManager()
            
            # ← FIXED: Backend expects api_key in JSON body, not header
            payload = {
                'api_key': self.config.api_key,
                'metrics': self.batch
            }
            
            response = http.request(
                'POST',
                self.config.endpoint,
                body=json.dumps(payload),
                headers={'Content-Type': 'application/json'},
                timeout=10.0
            )
            
            if response.status == 200:
                if self.config.debug:
                    print(f"[APIMonitor] Sent {len(self.batch)} metrics")
                
                # Clear batch
                self.batch = []
                self.last_send = time.time()
                
                # Try to flush offline queue
                if self.offline_queue:
                    self._flush_offline()
            else:
                raise Exception(f"HTTP {response.status}: {response.data.decode()[:200]}")
                
        except Exception as e:
            # Save to offline queue
            if self.offline_queue:
                self.offline_queue.save_batch(self.batch)
            
            if self.config.debug:
                print(f"[APIMonitor] Failed to send: {e}")
            
            self.batch = []
    
    def _flush_offline(self):
        """Send metrics from offline queue"""
        if not self.offline_queue:
            return
            
        offline_batch = self.offline_queue.get_batch(100)
        if offline_batch:
            try:
                import urllib3
                import json
                
                http = urllib3.PoolManager()
                
                payload = {
                    'api_key': self.config.api_key,
                    'metrics': offline_batch
                }
                
                response = http.request(
                    'POST',
                    self.config.endpoint,
                    body=json.dumps(payload),
                    headers={'Content-Type': 'application/json'},
                    timeout=10.0
                )
                
                if response.status == 200:
                    self.offline_queue.clear_batch(len(offline_batch))
                    if self.config.debug:
                        print(f"[APIMonitor] Flushed {len(offline_batch)} offline metrics")
            except:
                pass  # Will retry later