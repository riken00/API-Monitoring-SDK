from apimonitor import init, stop
import requests
import time

print("=" * 60)
print("API MONITOR SDK TEST")
print("=" * 60)

# Initialize with debug mode
init(
    api_key="my-test-key-123",
    endpoint="http://localhost:8000/ingest",
    batch_size=10,
    batch_timeout=5,
    sampling_rate=1.0,  # Monitor 100% of requests
    debug=True
)

print("\n📡 Making test API calls...\n")

# Test 1: Successful request
try:
    response = requests.get("https://jsonplaceholder.typicode.com/posts/1")
    print(f"✅ Test 1: {response.status_code} - {response.url}")
except Exception as e:
    print(f"❌ Test 1 failed: {e}")

# # Test 2: 404 error
# try:
#     response = requests.get("https://api.github.com/users/nonexistentuser12345")
#     print(f"⚠️  Test 2: {response.status_code} - {response.url}")
# except Exception as e:
#     print(f"❌ Test 2 failed: {e}")

# # Test 3: Slow endpoint
# try:
#     response = requests.get("https://httpbin.org/delay/1")
#     print(f"✅ Test 3: {response.status_code} - Slow endpoint (should be >1000ms)")
# except Exception as e:
#     print(f"❌ Test 3 failed: {e}")

# # Test 4: POST request
# try:
#     response = requests.post(
#         "https://jsonplaceholder.typicode.com/posts",
#         json={"title": "Test", "body": "Test body", "userId": 1}
#     )
#     print(f"✅ Test 4: {response.status_code} - POST request")
# except Exception as e:
#     print(f"❌ Test 4 failed: {e}")

# print("\n⏳ Waiting 6 seconds for batch timeout to trigger flush...\n")
# time.sleep(6)

# Stop and flush remaining metrics
print("\n🛑 Stopping monitor...\n")
stop()

print("\n" + "=" * 60)
print("✅ TEST COMPLETE")
print("=" * 60)
print("\nCheck your metrics:")