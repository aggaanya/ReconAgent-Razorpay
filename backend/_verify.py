"""Real verification tests against running backend."""
import time
import httpx
import json

client = httpx.Client(base_url="http://127.0.0.1:8001", timeout=150)

# Test 1: explain=false
print("--- Test 1: Reconciliation explain=false ---")
t0 = time.perf_counter()
r = client.post("/api/v1/ai/reconcile", json={"source": "synthetic", "seed": 42, "size": 100, "explain": False})
d1 = time.perf_counter() - t0
data = r.json()
print(f"Status: {r.status_code}  Time: {d1:.2f}s")
print(f"Keys: {list(data.keys())}")
print(json.dumps(data, indent=2)[:500])
print()

# Test 2: explain=true
print("--- Test 2: Reconciliation explain=true ---")
t0 = time.perf_counter()
r = client.post("/api/v1/ai/reconcile", json={"source": "synthetic", "seed": 42, "size": 100, "explain": True})
d2 = time.perf_counter() - t0
data = r.json()
print(f"Status: {r.status_code}  Time: {d2:.2f}s")
print(f"Keys: {list(data.keys())}")
answer = data.get("answer", "") or ""
print(f"answer_length={len(answer)}")
print(f"answer_preview={answer[:300]}")
print()

# Summary
print("=== COMPARISON ===")
print(f"explain=false: {d1:.2f}s")
print(f"explain=true:  {d2:.2f}s")

client.close()
