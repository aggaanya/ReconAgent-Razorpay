"""Focused test: capture exact tool arguments for Q1 and Q2."""
import json
import time
import requests

BASE = "http://127.0.0.1:8001"
TIMEOUT = 300

for q in [
    "What was my revenue this month?",
    "Show me the biggest financial issues.",
]:
    print(f"\n{'='*60}")
    print(f"Q: {q}")
    start = time.time()
    resp = requests.post(f"{BASE}/api/v1/ai/chat", json={"question": q}, timeout=TIMEOUT)
    elapsed = time.time() - start
    data = resp.json()
    print(f"Status: {data.get('status')} ({elapsed:.1f}s)")
    print(f"Tools: {data.get('selected_tools')}")
    print(f"Tool errors: {json.dumps(data.get('tool_errors', {}), indent=2)}")
    # Show full tool_results with args
    for tname, tenvelope in data.get('tool_results', {}).items():
        print(f"\nRESULT [{tname}]:")
        print(json.dumps(tenvelope, indent=2)[:600])
    print(f"\nAnswer: {(data.get('answer') or '(none)')[:200]}")
