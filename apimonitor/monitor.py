import threading
import atexit
from typing import Optional
from .config import Config
from .interceptor import install_interceptors, uninstall_interceptors
from .sender import MetricSender
from .queue import OfflineQueue

class Monitor:
    """
    Main SDK client. Auto-monitors all HTTP requests.
    
    Usage:
        from apimonitor import Monitor
        
        monitor = Monitor(api_key="your-api-key")
        monitor.start()
        
        # All HTTP requests now tracked automatically
        import requests
        requests.get("https://api.example.com")
    """
    
    _instance: Optional['Monitor'] = None
    _lock = threading.Lock()
    
    def __new__(cls, *args, **kwargs):
        """Singleton - only one monitor per process"""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(
        self,
        api_key: str,
        endpoint: str = "https://api.yourmonitor.com/ingest",
        batch_size: int = 100,
        batch_timeout: int = 10,
        sampling_rate: float = 1.0,
        offline_mode: bool = True,
        debug: bool = False
    ):
        if hasattr(self, '_initialized'):
            return
        
        self.config = Config(
            api_key=api_key,
            endpoint=endpoint,
            batch_size=batch_size,
            batch_timeout=batch_timeout,
            sampling_rate=sampling_rate,
            debug=debug
        )
        
        self.offline_queue = OfflineQueue() if offline_mode else None
        self.sender = MetricSender(self.config, self.offline_queue)
        self._started = False
        self._initialized = True
        
        atexit.register(self.stop)
    
    def start(self):
        """Start monitoring"""
        if self._started:
            return
        
        install_interceptors(self.config, self.sender)
        self.sender.start()
        self._started = True
        
        if self.config.debug:
            print(f"[APIMonitor] Started - {self.config.sampling_rate*100}% sampling")
    
    def stop(self):
        """Stop and flush metrics"""
        if not self._started:
            return
        
        uninstall_interceptors()
        self.sender.stop()
        self._started = False
        
        if self.config.debug:
            print("[APIMonitor] Stopped")
    
    def track_custom(self, event_name: str, **metadata):
        """Track custom business events"""
        metric = {
            'type': 'custom_event',
            'event_name': event_name,
            'metadata': metadata
        }
        self.sender.add_metric(metric)