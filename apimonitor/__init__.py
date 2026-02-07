# apimonitor/__init__.py

from .monitor import Monitor
from .config import Config

_monitor_instance = None

def init(api_key: str, **kwargs):
    """
    Initialize and start the API monitoring SDK.
    
    Usage:
        from apimonitor import init
        init(api_key="your-key")
        
        # Now all HTTP calls are monitored
        import requests
        requests.get("https://api.example.com")
    """
    global _monitor_instance
    _monitor_instance = Monitor(api_key=api_key, **kwargs)
    _monitor_instance.start()
    return _monitor_instance

def stop():
    """Stop the API monitoring SDK."""
    global _monitor_instance
    if _monitor_instance:
        _monitor_instance.stop()
        _monitor_instance = None

__version__ = "0.1.0"
__all__ = ["Monitor", "Config", "init", "stop"]