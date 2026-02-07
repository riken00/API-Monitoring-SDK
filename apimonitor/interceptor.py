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

def uninstall_interceptors():
    """Restore original functions"""
    for (lib, attr), original in _original_funcs.items():
        setattr(lib, attr, original)
    _original_funcs.clear()

def _should_ignore(url: str, config) -> bool:
    """Check if URL should be ignored (our own monitoring backend)"""
    base_url = config.endpoint.rsplit('/', 1)[0]
    return url.startswith(base_url)

def _patch_requests(config, sender):
    """Patch requests library"""
    try:
        import requests
        import urllib.request
        import json
        
        original = requests.Session.request
        
        @wraps(original)
        def monitored_request(self, method, url, **kwargs):
            # CRITICAL: Skip monitoring our own backend
            if _should_ignore(url, config):
                return original(self, method, url, **kwargs)
            
            # --- START VALIDATION ---
            # Call the monitoring backend to validate BEFORE making the actual request
            try:
                # Determine validation URL
                base_url = config.endpoint.rsplit('/', 1)[0]
                validate_url = f"{base_url}/validate"
                
                # Prepare validation payload
                val_payload = {
                    "api_key": config.api_key,
                    "method": method.upper(),
                    "url": str(url)
                }
                
                if config.debug:
                    print(f"🔐 [SDK] Validating with monitoring backend: {method} {url}")
                
                # Use urllib to avoid circular dependency with requests
                req = urllib.request.Request(
                    validate_url,
                    data=json.dumps(val_payload).encode('utf-8'),
                    headers={'Content-Type': 'application/json'},
                    method='POST'
                )
                
                try:
                    with urllib.request.urlopen(req, timeout=2.0) as response:
                        val_response = json.loads(response.read().decode('utf-8'))
                        if config.debug:
                            print(f"   ✅ [SDK] Validation passed: {val_response.get('message', 'OK')}")
                except urllib.error.HTTPError as e:
                    # Validation failed - read error details
                    error_body = e.read().decode('utf-8')
                    try:
                        error_data = json.loads(error_body)
                        error_msg = error_data.get('detail', 'Request blocked by monitoring policy')
                    except:
                        error_msg = f"Request blocked (HTTP {e.code})"
                    
                    if config.debug:
                        print(f"   ❌ [SDK] Validation FAILED: {error_msg}")
                    
                    # Create a mock response object to return to the caller
                    class BlockedResponse:
                        def __init__(self, status_code, message):
                            self.status_code = status_code
                            self.text = json.dumps({"error": message, "blocked_by": "api_monitor"})
                            self.headers = {'Content-Type': 'application/json'}
                            self._content = self.text.encode('utf-8')
                        
                        def json(self):
                            return json.loads(self.text)
                        
                        @property
                        def content(self):
                            return self._content
                    
                    # Return blocked response WITHOUT calling the actual API
                    blocked_resp = BlockedResponse(e.code, error_msg)
                    
                    # Still collect metrics for blocked requests
                    metric = collect_metric(
                        'requests', method, url, time.time(), blocked_resp, None, config
                    )
                    if metric:
                        sender.add_metric(metric)
                    
                    return blocked_resp
                    
            except Exception as e:
                # If validation service is down, decide: fail open or fail closed
                # Current implementation: FAIL CLOSED (strict validation)
                if config.debug:
                    print(f"   ⚠️  [SDK] Validation service error: {str(e)}")
                    print(f"   ❌ [SDK] Request BLOCKED due to validation service unavailability")
                
                class ValidationErrorResponse:
                    def __init__(self):
                        self.status_code = 503
                        self.text = json.dumps({
                            "error": "API Monitoring service unavailable",
                            "detail": "Cannot validate request"
                        })
                        self.headers = {'Content-Type': 'application/json'}
                        self._content = self.text.encode('utf-8')
                    
                    def json(self):
                        return json.loads(self.text)
                    
                    @property
                    def content(self):
                        return self._content
                
                return ValidationErrorResponse()
            # --- END VALIDATION ---
            
            # Validation passed - proceed with actual request
            if config.debug:
                print(f"🚀 [SDK] Proceeding to client API: {method} {url}")
            
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
            url = str(request.url)
            
            # ← CRITICAL: Ignore our own monitoring backend
            if _should_ignore(url, config):
                return original(self, request, **kwargs)
            
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
                    'httpx', request.method, url, start, response, error, config
                )
                if metric:
                    sender.add_metric(metric)
        
        httpx.Client.send = monitored_send
        _original_funcs[(httpx.Client, 'send')] = original
    except ImportError:
        pass