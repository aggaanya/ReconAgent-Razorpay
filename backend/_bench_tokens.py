"""Controlled benchmark: test each max_tokens level with fresh timing."""
import json
import sys
import time
import openai
import httpx

sys.path.insert(0, ".")
from app.ai.tools import RECONCILE_TRANSACTION_TOOL
from app.api.ai import _reconcile_signals
from app.ai.graph.prompts import RECONCILIATION_EXPLAIN_SYSTEM_PROMPT

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

c = openai.OpenAI(api_key="ollama", base_url="http://127.0.0.1:11434/v1", timeout=120, max_retries=0)

for mt in [150, 256, 384, 512]:
    t0 = time.perf_counter()
    r = c.chat.completions.create(model="llama3.2:3b", messages=messages, max_tokens=mt)
    d = time.perf_counter() - t0
    print(f"max_tokens={mt:>3d}: {d:.1f}s  tokens={r.usage.completion_tokens:>3d}  rate={r.usage.completion_tokens/d:.1f} tok/s  finish={r.choices[0].finish_reason}")

c.close()
