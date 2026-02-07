"""Background thread for sending metrics in batches"""

import threading
import time
import requests
from queue import Queue
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
                    
            except:
                # Timeout - check if we should flush anyway
                if self.batch and (time.time() - self.last_send) >= self.config.batch_timeout:
                    self._flush()
    
    def _flush(self):
        """Send batch to API"""
        if not self.batch:
            return
        
        try:
            response = requests.post(
                self.config.endpoint,
                json={'metrics': self.batch},
                headers={'X-API-Key': self.config.api_key},
                timeout=10
            )
            response.raise_for_status()
            
            if self.config.debug:
                print(f"[APIMonitor] Sent {len(self.batch)} metrics")
            
            # Clear batch
            self.batch = []
            self.last_send = time.time()
            
            # Try to flush offline queue
            if self.offline_queue:
                self._flush_offline()
                
        except Exception as e:
            # Save to offline queue
            if self.offline_queue:
                self.offline_queue.save_batch(self.batch)
            
            if self.config.debug:
                print(f"[APIMonitor] Failed to send: {e}")
            
            self.batch = []
    
    def _flush_offline(self):
        """Send metrics from offline queue"""
        offline_batch = self.offline_queue.get_batch(100)
        if offline_batch:
            try:
                response = requests.post(
                    self.config.endpoint,
                    json={'metrics': offline_batch},
                    headers={'X-API-Key': self.config.api_key},
                    timeout=10
                )
                response.raise_for_status()
                self.offline_queue.clear_batch(len(offline_batch))
            except:
                pass  # Will retry later