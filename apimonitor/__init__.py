"""
apimonitor — API Monitor SDK v2.0.0

Quick start:
    from apimonitor import init
    init(api_key="your-key")                            # ingest only (default)
    init(api_key="your-key", enable_validation=True)    # both flows combined
    init(api_key="your-key", enable_ingest=False,
         enable_validation=True)                        # validation only

When enable_validation=True the SDK makes ONE request per incoming request
to your server — the metric is piggybacked on the /validate call so /ingest
is never called separately.

When enable_validation=False the SDK batches metrics and sends them to
/ingest in the background — zero latency impact on your server.
"""

from .monitor import Monitor
from .config import Config

_monitor: Monitor | None = None


def init(api_key: str, **kwargs) -> Monitor:
    """
    Initialise and start the SDK. Safe to call multiple times.

    Key kwargs (see Config for full list):
      enable_validation (bool)  — Flow 1: validate every request. Default False.
      enable_ingest     (bool)  — Flow 2: capture metrics. Default True.
      batch_size        (int)   — count threshold for flush. Default 100.
      batch_timeout     (int)   — seconds before partial batch flushes. Default 10.
      sampling_rate     (float) — 0.0–1.0 fraction captured. Default 1.0.
      fail_behavior     (str)   — "open" or "closed". Default "open".
      exclude           (list)  — paths to skip e.g. ["/health", "/metrics"].
      heartbeat_interval (int)  — seconds between heartbeats. Default 30.
      framework         (str)   — e.g. "fastapi", "django". Shown in agents list.
      environment       (str)   — e.g. "production", "staging".
      debug             (bool)  — verbose logging. Default False.
    """
    global _monitor
    _monitor = Monitor(api_key=api_key, **kwargs)
    _monitor.start()
    return _monitor


def stop() -> None:
    """Gracefully stop the SDK and flush remaining metrics."""
    global _monitor
    if _monitor:
        _monitor.stop()
        _monitor = None


def get_monitor() -> Monitor | None:
    """Return the active Monitor instance, or None if not initialised."""
    return _monitor


__version__ = "0.1.3"
__all__ = ["Monitor", "Config", "init", "stop", "get_monitor"]