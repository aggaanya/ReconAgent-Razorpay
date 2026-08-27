import json, requests, time

BASE = "http://127.0.0.1:8001"
TIMEOUT = 300
questions = [
    "What was my revenue this month?",
    "Show me the biggest financial issues.",
    "How much have I paid in fees and taxes?",
    "Are there any refunds that look suspicious?",
]
for q in questions:
    print(f"\n{'='*70}")
    print(f"Q: {q}")
    print("="*70)
    t0 = time.time()
    r = requests.post(f"{BASE}/api/v1/ai/chat", json={"question": q}, timeout=TIMEOUT)
    dt = time.time() - t0
    d = r.json()
    status = d.get("status")
    tools = d.get("selected_tools")
    source = d.get("selection_source")
    errors = d.get("tool_errors", {})
    answer = d.get("answer") or "(none)"
    print(f"Status: {status} | Tools: {tools} | Source: {source} | Time: {dt:.1f}s")
    if errors:
        print(f"Tool errors: {json.dumps(errors)}")
    for tn, te in d.get("tool_results", {}).items():
        print(f"Result [{tn}]: {json.dumps(te)[:500]}")
    print(f"Answer: {answer[:400]}")
