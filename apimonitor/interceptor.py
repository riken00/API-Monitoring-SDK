"""
Monkey-patches requests, httpx, aiohttp to auto-collect metrics
"""

import time
from functools import wraps
from .collector import collect_metric

_original_funcs = {}

def install_interceptors(config, sender):
    """Patch all supported HTTP libraries"""
    _patch_requests(config, sender)
    _patch_httpx(config, sender)
    # Add more libraries as needed

def uninstall_interceptors():
    """Restore original functions"""
    for (lib, attr), original in _original_funcs.items():
        setattr(lib, attr, original)
    _original_funcs.clear()

def _patch_requests(config, sender):
    """Patch requests library"""
    try:
        import requests
        original = requests.Session.request
        
        @wraps(original)
        def monitored_request(self, method, url, **kwargs):
            start = time.time()
            error = None
            response = None
            
            try:
                response = original(self, method, url, **kwargs)
                return response
            except Exception as e:
                error = e
                raise
            finally:
                metric = collect_metric(
                    'requests', method, url, start, response, error, config
                )
                if metric:
                    sender.add_metric(metric)
        
        requests.Session.request = monitored_request
        _original_funcs[(requests.Session, 'request')] = original
    except ImportError:
        pass

def _patch_httpx(config, sender):
    """Patch httpx library"""
    try:
        import httpx
        original = httpx.Client.send
        
        @wraps(original)
        def monitored_send(self, request, **kwargs):
            start = time.time()
            error = None
            response = None
            
            try:
                response = original(self, request, **kwargs)
                return response
            except Exception as e:
                error = e
                raise
            finally:
                metric = collect_metric(
                    'httpx', request.method, str(request.url), start, response, error, config
                )
                if metric:
                    sender.add_metric(metric)
        
        httpx.Client.send = monitored_send
        _original_funcs[(httpx.Client, 'send')] = original
    except ImportError:
        pass