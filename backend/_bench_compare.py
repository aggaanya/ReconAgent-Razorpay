"""Compare OpenAI SDK vs direct Ollama HTTP for reconcile explain."""
import json
import sys
import time
import httpx

sys.path.insert(0, ".")
from app.ai.tools import RECONCILE_TRANSACTION_TOOL
from app.api.ai import _reconcile_signals
from app.ai.graph.prompts import RECONCILIATION_EXPLAIN_SYSTEM_PROMPT
from app.core.config import get_settings

settings = get_settings()

envelope = RECONCILE_TRANSACTION_TOOL.run(None, {"source": "synthetic", "seed": 42, "size": 100, "max_settlement_delay_days": 3})
signals = _reconcile_signals(envelope["data"], envelope["exceptions"], 42, 100, envelope.get("exception_summary"))
signals_json = json.dumps(dict(signals), sort_keys=True, separators=(",", ": "))
q = (
    "Explain this reconciliation outcome for a business owner. "
    "Cover: the match rate (and clarify that it is not the same as accuracy), "
    "the exception breakdown by category, financial exposure with human-readable "
    "amounts, severity distribution, the most critical exceptions requiring human "
    "review, unresolved items, and recommended actions. Never state that the match "
    "rate equals accuracy."
)
user_msg = f"Interpret these pre-computed financial signals:\n{signals_json}\n\nQuestion: {q}"
messages = [
    {"role": "system", "content": RECONCILIATION_EXPLAIN_SYSTEM_PROMPT},
    {"role": "user", "content": user_msg},
]

# --- Test 1: Direct Ollama /api/chat ---
payload = {
    "model": "llama3.2:3b",
    "messages": messages,
    "stream": False,
    "options": {"num_predict": 384},
}
client = httpx.Client(timeout=120)
t0 = time.perf_counter()
r = client.post("http://127.0.0.1:11434/api/chat", json=payload)
d1 = time.perf_counter() - t0
resp = r.json()
print(f"Direct /api/chat: {d1:.1f}s  tokens={resp.get('eval_count','?')}  prompt_tokens={resp.get('prompt_eval_count','?')}  rate={resp.get('eval_count',0)/d1:.1f} tok/s")
client.close()

# --- Test 2: OpenAI SDK ---
import openai
c = openai.OpenAI(api_key="ollama", base_url="http://127.0.0.1:11434/v1", timeout=120, max_retries=0)
t0 = time.perf_counter()
r2 = c.chat.completions.create(model="llama3.2:3b", messages=messages, max_tokens=384)
d2 = time.perf_counter() - t0
print(f"OpenAI SDK:       {d2:.1f}s  tokens={r2.usage.completion_tokens}  prompt_tokens={r2.usage.prompt_tokens}  rate={r2.usage.completion_tokens/d2:.1f} tok/s")
c.close()
