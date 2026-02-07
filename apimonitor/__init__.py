from .monitor import Monitor
from .config import Config

_monitor_instance = None

def init(api_key: str, **kwargs):
    """
    Initialize and start the API monitoring SDK.
    """
    global _monitor_instance
    _monitor_instance = Monitor(api_key=api_key, **kwargs)
    _monitor_instance.start()
    return _monitor_instance

def stop():
    """
    Stop the API monitoring SDK.
    """
    global _monitor_instance
    if _monitor_instance:
        _monitor_instance.stop()

__version__ = "0.1.2"
__all__ = ["Monitor", "Config", "init", "stop"]
