# API Monitor SDK

Automatically monitor all HTTP requests in your Python application with zero code changes.

## Installation

```bash
pip install api-monitor-sdk
```

## Quick Start

```python
from apimonitor import Monitor
import requests

# Initialize monitor
monitor = Monitor(api_key="your-api-key-from-dashboard")
monitor.start()

# All HTTP requests now automatically tracked
response = requests.get("https://api.github.com/users/octocat")
# Metrics sent in background - your app continues normally

# Optional: track custom business events
monitor.track_custom("payment_processed", amount=100, user_id="123")
```

## Features

- ✅ **Zero-code monitoring** - Just call `monitor.start()`
- ✅ **Supports multiple libraries** - requests, httpx, aiohttp
- ✅ **Background sending** - Non-blocking, won't slow your app
- ✅ **Batching** - Sends 100 requests or every 10 seconds
- ✅ **Offline queue** - Buffers metrics when network fails
- ✅ **Sampling** - Monitor 10% of traffic to reduce overhead
- ✅ **Thread-safe** - Works with multi-threaded applications

## Configuration

```python
monitor = Monitor(
    api_key="your-api-key",
    endpoint="https://api.yourmonitor.com/ingest",  # Custom endpoint
    batch_size=100,              # Send after N requests
    batch_timeout=10,            # Or after N seconds
    sampling_rate=1.0,           # 1.0 = 100%, 0.1 = 10%
    offline_mode=True,           # Buffer when network fails
    debug=True                   # Print debug logs
)
monitor.start()
```

## Framework Examples

### Flask

```python
from flask import Flask
from apimonitor import Monitor

app = Flask(__name__)

monitor = Monitor(api_key="your-key")
monitor.start()

@app.route("/")
def home():
    # Any requests made here are auto-tracked
    return "Hello World"
```

### FastAPI

```python
from fastapi import FastAPI
from apimonitor import Monitor

app = FastAPI()

monitor = Monitor(api_key="your-key")
monitor.start()

@app.get("/")
def home():
    return {"message": "Hello World"}
```

### Django

```python
# In your settings.py or apps.py

from apimonitor import Monitor

monitor = Monitor(api_key="your-key")
monitor.start()
```

## Advanced Usage

### Custom Events

Track business-specific events:

```python
# Track payment
monitor.track_custom("payment_processed", 
                     amount=99.99, 
                     currency="USD",
                     user_id="user_123")

# Track signup
monitor.track_custom("user_signup",
                     email="user@example.com",
                     plan="premium")
```

### Stopping the Monitor

```python
# Gracefully stop and flush remaining metrics
monitor.stop()
```

## How It Works

1. **Interception** - Monkey-patches HTTP libraries (requests, httpx, aiohttp)
2. **Collection** - Captures timing, status, URL, errors
3. **Batching** - Queues metrics until batch size or timeout reached
4. **Sending** - Background thread sends to API endpoint
5. **Offline Queue** - If network fails, saves to local SQLite database
6. **Auto-flush** - On program exit, sends remaining metrics

## Supported Libraries

- ✅ `requests` - Most popular HTTP library
- ✅ `httpx` - Modern async HTTP library
- ✅ `aiohttp` - Async HTTP client/server
- 🚧 `urllib3` - Coming soon
- 🚧 `http.client` - Coming soon

## Requirements

- Python 3.8+
- requests (automatically installed)

## License

MIT

## Support

- Documentation: https://docs.yourmonitor.com
- Issues: https://github.com/yourusername/api-monitor-sdk/issues
- Dashboard: https://app.yourmonitor.com
```