"""
logger.py — Internal structured logger.

All SDK output goes through here. Reads config.log_level so it never
bleeds into the client's log config or format.
"""

import logging
import threading
from typing import Optional

_lock = threading.Lock()
_logger: Optional[logging.Logger] = None


def get_logger(log_level: str = "ERROR", debug: bool = False) -> logging.Logger:
    global _logger
    with _lock:
        if _logger is not None:
            return _logger

        logger = logging.getLogger("apimonitor")
        logger.propagate = False  # never leak into root logger

        if not logger.handlers:
            handler = logging.StreamHandler()
            handler.setFormatter(logging.Formatter(
                "[APIMonitor] %(levelname)s %(message)s"
            ))
            logger.addHandler(handler)

        level = logging.DEBUG if debug else getattr(logging, log_level, logging.ERROR)
        logger.setLevel(level)
        _logger = logger
        return _logger


def reset_logger() -> None:
    """Force re-initialisation (used after fork or config reload)."""
    global _logger
    with _lock:
        _logger = None