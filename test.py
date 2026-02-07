from apimonitor import init, stop
import requests

# Start monitoring
init(api_key="my-test-key-123", endpoint="http://localhost:8000/ingest", debug=True)

# Make some API calls
print("Making API calls...")
response = requests.get("https://jsonplaceholder.typicode.com/posts/1")
print(f"Status: {response.status_code}")

response = requests.get("https://api.github.com/riken-khadela/github")
print(f"Status: {response.status_code}")

# Metrics are sent in background every 10 seconds
# Or force flush on exit
stop()

print("Done! Check your backend for metrics.")