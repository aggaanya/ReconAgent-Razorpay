"""Temporary instrumentation — measures each phase of reconcile explain=true."""
import json
import sys
import time

sys.path.insert(0, ".")

import httpx

from app.ai.llm import LLMService
from app.ai.tools import RECONCILE_TRANSACTION_TOOL
from app.api.ai import _reconcile_signals
from app.core.config import get_settings
from app.ai.graph.prompts import RECONCILIATION_EXPLAIN_SYSTEM_PROMPT


def bench():
    settings = get_settings()

    # ── A. Deterministic reconciliation ──────────────────────────────
    t0 = time.perf_counter()
    envelope = RECONCILE_TRANSACTION_TOOL.run(
        None,
        {"source": "synthetic", "seed": 42, "size": 100, "max_settlement_delay_days": 3},
    )
    t_reconcile = time.perf_counter() - t0

    data = envelope["data"]
    exceptions = envelope["exceptions"]

    # ── C. Payload construction ──────────────────────────────────────
    t1 = time.perf_counter()
    signals = _reconcile_signals(data, exceptions, 42, 100, envelope.get("exception_summary"))
    t_payload = time.perf_counter() - t1

    # ── D/E. Prompt size ─────────────────────────────────────────────
    signals_json = json.dumps(dict(signals), sort_keys=True, separators=(",", ": "))
    question = (
        "Explain this reconciliation outcome for a business owner. "
        "Cover: the match rate (and clarify that it is not the same as accuracy), "
        "the exception breakdown by category, financial exposure with human-readable "
        "amounts, severity distribution, the most critical exceptions requiring human "
        "review, unresolved items, and recommended actions. Never state that the match "
        "rate equals accuracy."
    )
    user_message = f"Interpret these pre-computed financial signals:\n{signals_json}\n\nQuestion: {question}"
    system_message = RECONCILIATION_EXPLAIN_SYSTEM_PROMPT

    prompt_chars = len(system_message) + len(user_message)
    prompt_bytes = len(system_message.encode("utf-8")) + len(user_message.encode("utf-8"))

    # ── F/G/H. LLM call ─────────────────────────────────────────────
    llm = LLMService(
        api_key=settings.llm_api_key.get_secret_value(),
        model=settings.llm_model,
        base_url=settings.llm_base_url,
        timeout_seconds=settings.llm_timeout_seconds,
        max_retries=0,          # no SDK retries during benchmark
        max_tokens=512,
    )

    t_llm = time.perf_counter()
    reply = llm.explain_signals(
        signals, question=question, system_prompt=RECONCILIATION_EXPLAIN_SYSTEM_PROMPT
    )
    t_llm_total = time.perf_counter() - t_llm
    llm.close()

    # ── Report ───────────────────────────────────────────────────────
    print("=== RECONCILIATION explain=true BENCHMARK ===")
    print(f"  (A) Reconciliation:     {t_reconcile*1000:8.1f} ms")
    print(f"  (B) Signal analysis:    {'N/A (not used in reconcile path)':>12}")
    print(f"  (C) Payload build:      {t_payload*1000:8.3f} ms")
    print(f"  (D) Prompt chars:       {prompt_chars:>8}")
    print(f"  (E) Prompt bytes:       {prompt_bytes:>8}")
    print(f"  (F) LLM duration:       {t_llm_total:>8.2f} s")
    print(f"  (G) Output chars:       {len(reply.content):>8}")
    print(f"      Output words:       {len(reply.content.split()):>8}")
    print(f"      Finish reason:      {reply.finish_reason}")
    print(f"      Prompt tokens:      {reply.usage.prompt_tokens if reply.usage else '?'}")
    print(f"      Completion tokens:  {reply.usage.completion_tokens if reply.usage else '?'}")
    total = t_reconcile + t_payload + t_llm_total
    print(f"  (H) Total duration:     {total:>8.2f} s")
    print(f"  (I) LLM request count:  {1}")
    print(f"  (J) SDK retries:        {0} (disabled)")
    print(f"  (K) Configured timeout: {settings.llm_timeout_seconds}s")
    print(f"  (L) Configured max_tok: {llm._max_tokens}")
    print("============================================")
    print(f"\nDeterministic results:")
    print(f"  total={data['total_records']}  matched={data['matched_count']}  "
          f"exceptions={data['exception_count']}  unresolved={data['unresolved_count']}  "
          f"match_rate={data['match_rate']}")
    print(f"\nErrors: []")
    print(f"Answer length: {len(reply.content)} chars")


if __name__ == "__main__":
    bench()
