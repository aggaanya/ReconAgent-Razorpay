"""Thread-safe in-memory cache for deterministic computation results.

Provides an LRU cache with TTL (time-to-live) for expensive but
deterministic operations like reconciliation and signal analysis. The cache
is process-local (no external dependencies like Redis) and falls back
gracefully on any internal failure.

Key design:

- Cache keys include every input that affects the result (seed, size,
  source, policy parameters, etc.).
- TTL prevents stale data from persisting indefinitely.
- Thread safety via ``threading.Lock`` (FastAPI runs endpoint handlers
  in a thread pool by default).
- Cache failures never crash the request — they fall back to normal
  computation silently.
"""

import hashlib
import json
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# Default TTL: 10 minutes. Reconciliation is deterministic for a given
# seed+size+source, so caching is safe as long as the underlying data
# doesn't change (it's synthetic, so it doesn't).
DEFAULT_TTL_SECONDS: float = 600.0

# Maximum cached entries before LRU eviction begins.
DEFAULT_MAX_ENTRIES: int = 128


@dataclass
class _CacheEntry:
    """One cached value with an expiry timestamp."""

    value: Any
    expires_at: float


class DeterministicCache:
    """Thread-safe LRU + TTL cache for deterministic computations.

    Safe to use from FastAPI endpoint handlers (which run in a thread
    pool). Every public method catches and logs internal errors — a cache
    failure never prevents the caller from computing the result normally.
    """

    def __init__(
        self,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        max_entries: int = DEFAULT_MAX_ENTRIES,
    ) -> None:
        self._ttl = ttl_seconds
        self._max_entries = max_entries
        self._store: dict[str, _CacheEntry] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> Any | None:
        """Return the cached value for *key* if present and not expired."""
        try:
            with self._lock:
                entry = self._store.get(key)
                if entry is None:
                    return None
                if time.monotonic() > entry.expires_at:
                    del self._store[key]
                    return None
                return entry.value
        except Exception:
            logger.exception("Cache get failed for key=%s", key)
            return None

    def set(self, key: str, value: Any) -> None:
        """Store *value* under *key* with the configured TTL."""
        try:
            with self._lock:
                if len(self._store) >= self._max_entries and key not in self._store:
                    self._evict_oldest()
                self._store[key] = _CacheEntry(
                    value=value,
                    expires_at=time.monotonic() + self._ttl,
                )
        except Exception:
            logger.exception("Cache set failed for key=%s", key)

    def invalidate(self, key: str) -> None:
        """Remove a specific entry (if present)."""
        try:
            with self._lock:
                self._store.pop(key, None)
        except Exception:
            logger.exception("Cache invalidate failed for key=%s", key)

    def clear(self) -> None:
        """Drop all cached entries."""
        try:
            with self._lock:
                self._store.clear()
        except Exception:
            logger.exception("Cache clear failed")

    @property
    def size(self) -> int:
        """Current number of cached entries (approximate, no lock)."""
        return len(self._store)

    def _evict_oldest(self) -> None:
        """Remove the entry with the earliest expiry (LRU approximation)."""
        if not self._store:
            return
        oldest_key = min(self._store, key=lambda k: self._store[k].expires_at)
        del self._store[oldest_key]


# ---------------------------------------------------------------------------
# Key builders — deterministic, ordered, stable serialization
# ---------------------------------------------------------------------------


def _stable_json(obj: Any) -> str:
    """JSON-serialize with sorted keys for deterministic hashing."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def reconciliation_cache_key(
    *,
    source: str,
    seed: int,
    size: int,
    max_settlement_delay_days: int,
    explain: bool = False,
    question: str | None = None,
) -> str:
    """Build a cache key for reconciliation results.

    Includes every input that affects the deterministic output. The LLM
    explanation (if any) is NOT part of the key because it's non-
    deterministic — the cached value includes the explanation from the
    first call, which is acceptable for identical deterministic inputs.
    """
    payload = {
        "source": source,
        "seed": seed,
        "size": size,
        "max_settlement_delay_days": max_settlement_delay_days,
        "explain": explain,
        "question": question or "",
    }
    raw = _stable_json(payload)
    digest = hashlib.sha256(raw.encode()).hexdigest()[:16]
    return f"reconcile:{digest}"


def evaluation_cache_key(
    *,
    seed: int,
    size: int,
    max_settlement_delay_days: int,
) -> str:
    """Build a cache key for evaluation results."""
    payload = {
        "seed": seed,
        "size": size,
        "max_settlement_delay_days": max_settlement_delay_days,
    }
    raw = _stable_json(payload)
    digest = hashlib.sha256(raw.encode()).hexdigest()[:16]
    return f"eval:{digest}"


def signal_analysis_cache_key(tool_results: dict[str, Any]) -> str:
    """Build a cache key for deterministic signal analysis.

    The key is derived from a stable JSON serialization of the tool
    results envelope. Only the ``data`` fields (deterministic numbers)
    are included; metadata like ``tool`` names is part of the envelope.
    """
    raw = _stable_json(tool_results)
    digest = hashlib.sha256(raw.encode()).hexdigest()[:16]
    return f"signals:{digest}"


def compare_cache_key(
    *,
    previous_seed: int,
    previous_size: int,
    current_seed: int,
    current_size: int,
    explain: bool = False,
) -> str:
    """Build a cache key for reconciliation comparison/drift analysis."""
    payload = {
        "previous_seed": previous_seed,
        "previous_size": previous_size,
        "current_seed": current_seed,
        "current_size": current_size,
        "explain": explain,
    }
    raw = _stable_json(payload)
    digest = hashlib.sha256(raw.encode()).hexdigest()[:16]
    return f"compare:{digest}"


# ---------------------------------------------------------------------------
# Process-wide singleton instances
# ---------------------------------------------------------------------------

# Cache for reconciliation and evaluation results (heavy computation).
reconciliation_cache = DeterministicCache(
    ttl_seconds=600.0,
    max_entries=128,
)

# Cache for signal analysis (deterministic but called every chat query).
signal_cache = DeterministicCache(
    ttl_seconds=600.0,
    max_entries=256,
)


__all__ = [
    "DEFAULT_MAX_ENTRIES",
    "DEFAULT_TTL_SECONDS",
    "DeterministicCache",
    "compare_cache_key",
    "evaluation_cache_key",
    "reconciliation_cache",
    "reconciliation_cache_key",
    "signal_analysis_cache_key",
    "signal_cache",
]
