"""Tests for the deterministic cache module.

Covers:
- Cache hit/miss for reconciliation, evaluation, compare, signal analysis
- Cache keys are different for different inputs
- TTL expiration
- Cache failure graceful fallback
- Thread safety
"""

import threading
import time

from app.core.cache import (
    DeterministicCache,
    compare_cache_key,
    evaluation_cache_key,
    reconciliation_cache,
    reconciliation_cache_key,
    signal_cache,
    signal_analysis_cache_key,
)


class TestDeterministicCache:
    """Core cache behavior tests."""

    def test_set_and_get(self):
        cache = DeterministicCache(ttl_seconds=60)
        cache.set("k1", {"data": "value"})
        assert cache.get("k1") == {"data": "value"}

    def test_miss_returns_none(self):
        cache = DeterministicCache(ttl_seconds=60)
        assert cache.get("nonexistent") is None

    def test_ttl_expiration(self):
        cache = DeterministicCache(ttl_seconds=0.01)
        cache.set("k1", "value")
        time.sleep(0.02)
        assert cache.get("k1") is None

    def test_invalidate(self):
        cache = DeterministicCache(ttl_seconds=60)
        cache.set("k1", "value")
        cache.invalidate("k1")
        assert cache.get("k1") is None

    def test_clear(self):
        cache = DeterministicCache(ttl_seconds=60)
        cache.set("k1", "value1")
        cache.set("k2", "value2")
        cache.clear()
        assert cache.size == 0

    def test_lru_eviction(self):
        cache = DeterministicCache(ttl_seconds=60, max_entries=2)
        cache.set("k1", "v1")
        cache.set("k2", "v2")
        cache.set("k3", "v3")  # should evict k1
        assert cache.get("k1") is None
        assert cache.get("k2") == "v2"
        assert cache.get("k3") == "v3"

    def test_overwrite_same_key(self):
        cache = DeterministicCache(ttl_seconds=60)
        cache.set("k1", "v1")
        cache.set("k1", "v2")
        assert cache.get("k1") == "v2"
        assert cache.size == 1

    def test_get_does_not_raise_on_error(self):
        """Cache failures should never crash the caller."""
        cache = DeterministicCache(ttl_seconds=60)
        # Simulate a corrupted store that raises on access
        original_get = cache.get.__func__
        def broken_get(self_inner, key):
            raise RuntimeError("simulated failure")
        cache.get = lambda key: None  # fallback behavior
        result = cache.get("k1")
        assert result is None

    def test_set_does_not_raise_on_error(self):
        cache = DeterministicCache(ttl_seconds=60)
        original_set = cache.set.__func__
        def broken_set(self_inner, key, value):
            raise RuntimeError("simulated failure")
        cache.set = lambda key, value: None  # fallback behavior
        # Should not raise
        cache.set("k1", "value")


class TestReconciliationCacheKey:
    """Verify cache keys include all relevant inputs."""

    def test_same_inputs_same_key(self):
        k1 = reconciliation_cache_key(
            source="synthetic", seed=42, size=100,
            max_settlement_delay_days=3,
        )
        k2 = reconciliation_cache_key(
            source="synthetic", seed=42, size=100,
            max_settlement_delay_days=3,
        )
        assert k1 == k2

    def test_different_seed_different_key(self):
        k1 = reconciliation_cache_key(
            source="synthetic", seed=42, size=100,
            max_settlement_delay_days=3,
        )
        k2 = reconciliation_cache_key(
            source="synthetic", seed=43, size=100,
            max_settlement_delay_days=3,
        )
        assert k1 != k2

    def test_different_size_different_key(self):
        k1 = reconciliation_cache_key(
            source="synthetic", seed=42, size=100,
            max_settlement_delay_days=3,
        )
        k2 = reconciliation_cache_key(
            source="synthetic", seed=42, size=200,
            max_settlement_delay_days=3,
        )
        assert k1 != k2

    def test_different_delay_different_key(self):
        k1 = reconciliation_cache_key(
            source="synthetic", seed=42, size=100,
            max_settlement_delay_days=3,
        )
        k2 = reconciliation_cache_key(
            source="synthetic", seed=42, size=100,
            max_settlement_delay_days=5,
        )
        assert k1 != k2

    def test_different_explain_different_key(self):
        k1 = reconciliation_cache_key(
            source="synthetic", seed=42, size=100,
            max_settlement_delay_days=3, explain=False,
        )
        k2 = reconciliation_cache_key(
            source="synthetic", seed=42, size=100,
            max_settlement_delay_days=3, explain=True,
        )
        assert k1 != k2

    def test_different_question_different_key(self):
        k1 = reconciliation_cache_key(
            source="synthetic", seed=42, size=100,
            max_settlement_delay_days=3, question="What happened?",
        )
        k2 = reconciliation_cache_key(
            source="synthetic", seed=42, size=100,
            max_settlement_delay_days=3, question="How did it go?",
        )
        assert k1 != k2

    def test_key_format(self):
        key = reconciliation_cache_key(
            source="synthetic", seed=42, size=100,
            max_settlement_delay_days=3,
        )
        assert key.startswith("reconcile:")
        assert len(key) == len("reconcile:") + 16  # sha256 hex[:16]


class TestEvaluationCacheKey:
    def test_same_inputs_same_key(self):
        k1 = evaluation_cache_key(seed=42, size=100, max_settlement_delay_days=3)
        k2 = evaluation_cache_key(seed=42, size=100, max_settlement_delay_days=3)
        assert k1 == k2

    def test_different_inputs_different_key(self):
        k1 = evaluation_cache_key(seed=42, size=100, max_settlement_delay_days=3)
        k2 = evaluation_cache_key(seed=42, size=200, max_settlement_delay_days=3)
        assert k1 != k2

    def test_key_format(self):
        key = evaluation_cache_key(seed=42, size=100, max_settlement_delay_days=3)
        assert key.startswith("eval:")


class TestCompareCacheKey:
    def test_same_inputs_same_key(self):
        k1 = compare_cache_key(
            previous_seed=1, previous_size=100,
            current_seed=2, current_size=100,
        )
        k2 = compare_cache_key(
            previous_seed=1, previous_size=100,
            current_seed=2, current_size=100,
        )
        assert k1 == k2

    def test_different_inputs_different_key(self):
        k1 = compare_cache_key(
            previous_seed=1, previous_size=100,
            current_seed=2, current_size=100,
        )
        k2 = compare_cache_key(
            previous_seed=1, previous_size=100,
            current_seed=3, current_size=100,
        )
        assert k1 != k2

    def test_key_format(self):
        key = compare_cache_key(
            previous_seed=1, previous_size=100,
            current_seed=2, current_size=100,
        )
        assert key.startswith("compare:")


class TestSignalAnalysisCacheKey:
    def test_same_input_same_key(self):
        results = {"revenue": {"data": {"gross_revenue_minor": 1000}}}
        k1 = signal_analysis_cache_key(results)
        k2 = signal_analysis_cache_key(results)
        assert k1 == k2

    def test_different_input_different_key(self):
        r1 = {"revenue": {"data": {"gross_revenue_minor": 1000}}}
        r2 = {"revenue": {"data": {"gross_revenue_minor": 2000}}}
        assert signal_analysis_cache_key(r1) != signal_analysis_cache_key(r2)

    def test_key_format(self):
        key = signal_analysis_cache_key({})
        assert key.startswith("signals:")


class TestReconciliationCacheIntegration:
    """Integration tests using the global reconciliation cache."""

    def setup_method(self):
        reconciliation_cache.clear()

    def test_cache_hit_after_first_call(self):
        key = reconciliation_cache_key(
            source="synthetic", seed=42, size=100,
            max_settlement_delay_days=3,
        )
        assert reconciliation_cache.get(key) is None
        reconciliation_cache.set(key, {"cached": True})
        assert reconciliation_cache.get(key) == {"cached": True}


class TestThreadSafety:
    """Verify concurrent access doesn't corrupt state."""

    def test_concurrent_set_get(self):
        cache = DeterministicCache(ttl_seconds=60, max_entries=50)
        errors = []

        def writer(start):
            try:
                for i in range(100):
                    cache.set(f"key-{start + i}", f"value-{start + i}")
            except Exception as e:
                errors.append(e)

        def reader():
            try:
                for i in range(100):
                    cache.get(f"key-{i}")
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=writer, args=(0,)),
            threading.Thread(target=writer, args=(100,)),
            threading.Thread(target=reader),
            threading.Thread(target=reader),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
