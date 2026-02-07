"""Extract metrics from HTTP responses"""

import time
import random
from datetime import datetime
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
        'timestamp': datetime.utcnow().isoformat() + 'Z',
        'library': library,
        'method': method.upper(),
        'url': url,
        'response_time_ms': round(response_time, 2)
    }
    
    if response:
        metric['status_code'] = response.status_code
        metric['response_size'] = len(response.content) if hasattr(response, 'content') else None
    
    if error:
        metric['error_message'] = str(error)
        metric['status_code'] = None
    
    return metric