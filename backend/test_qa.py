"""Quick smoke test for the 4 finance Q&A questions via HTTP."""
import json
import time
import requests

BASE = "http://127.0.0.1:8001"
TIMEOUT = 300  # generous for Ollama

QUESTIONS = [
    "What was my revenue this month?",
    "Show me the biggest financial issues.",
    "How much have I paid in fees and taxes?",
    "Are there any refunds that look suspicious?",
]

for q in QUESTIONS:
    print(f"\n{'='*80}")
    print(f"QUESTION: {q}")
    print(f"{'='*80}")
    start = time.time()
    try:
        resp = requests.post(f"{BASE}/api/v1/ai/chat", json={"question": q}, timeout=TIMEOUT)
        elapsed = time.time() - start
        data = resp.json()
        print(f"HTTP: {resp.status_code} ({elapsed:.1f}s)")
        print(f"Status: {data.get('status')}")
        print(f"Selected tools: {data.get('selected_tools')}")
        print(f"Selection source: {data.get('selection_source')}")
        print(f"Tool results keys: {list(data.get('tool_results', {}).keys())}")
        print(f"Tool errors: {data.get('tool_errors', {})}")
        print(f"Financial signals count: {len(data.get('financial_signals', []))}")
        print(f"Errors: {data.get('errors', [])}")
        answer = data.get('answer')
        if answer:
            print(f"\nANSWER:\n{answer}")
        else:
            print(f"\nANSWER: None")
            # Show raw interpretation field if present
            interp = data.get('interpretation')
            if interp:
                print(f"Raw interpretation field: {interp}")
        # Show first tool result envelope for grounding check
        for tname, tenvelope in data.get('tool_results', {}).items():
            print(f"\nTOOL RESULT [{tname}]: {json.dumps(tenvelope, indent=2)[:500]}")
    except Exception as e:
        elapsed = time.time() - start
        print(f"FAILED after {elapsed:.1f}s: {e}")
