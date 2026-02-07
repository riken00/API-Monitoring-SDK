"""Extract metrics from HTTP responses"""

import time
import random
from typing import Optional, Dict, Any

def collect_metric(
    library: str,
    method: str,
    url: str,
    start_time: float,
    response: Optional[Any],
    error: Optional[Exception],
    config
) -> Optional[Dict]:
    """
    Extract metrics from HTTP request/response.
    Returns None if skipped by sampling.
    """
    
    # Sampling
    if random.random() > config.sampling_rate:
        return None
    
    response_time = (time.time() - start_time) * 1000  # ms
    
    metric = {
        'timestamp': start_time,  # ← FIXED: Use float timestamp, not ISO string
        'method': method.upper(),
        'url': url,
        'duration_ms': round(response_time, 2)  # ← FIXED: Renamed from response_time_ms
    }
    
    if response:
        metric['status_code'] = getattr(response, 'status_code', None)
    
    if error:
        metric['error'] = str(error)
        metric['status_code'] = None
    
    return metric