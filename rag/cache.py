"""In-memory caches for embed keys, retrieval, and extractive answers."""

from __future__ import annotations

import hashlib
import re
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

T = TypeVar("T")

_NORMALIZE = re.compile(r"\s+")


def normalize_query(text: str) -> str:
    return _NORMALIZE.sub(" ", text.strip().lower())


def cache_key(*parts: str) -> str:
    raw = "||".join(parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass
class CacheStats:
    hits: int = 0
    misses: int = 0

    @property
    def hit_rate(self) -> float:
        total = self.hits + self.misses
        return (self.hits / total) if total else 0.0


class LRUCache(Generic[T]):
    def __init__(self, maxsize: int = 256) -> None:
        self.maxsize = maxsize
        self._data: OrderedDict[str, T] = OrderedDict()
        self._lock = threading.Lock()
        self.stats = CacheStats()

    def get(self, key: str) -> T | None:
        with self._lock:
            if key not in self._data:
                self.stats.misses += 1
                return None
            self.stats.hits += 1
            self._data.move_to_end(key)
            return self._data[key]

    def set(self, key: str, value: T) -> None:
        with self._lock:
            if key in self._data:
                self._data.move_to_end(key)
            self._data[key] = value
            while len(self._data) > self.maxsize:
                self._data.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()
            self.stats = CacheStats()


@dataclass
class TimedResult:
    value: Any
    elapsed_ms: float
    cache_hit: bool
    layer: str


class RagCache:
    """Layers: retrieval results, extractive answers, spoken turn payloads."""

    def __init__(self, maxsize: int = 256) -> None:
        self.retrieval = LRUCache[dict[str, Any]](maxsize=maxsize)
        self.answer = LRUCache[dict[str, Any]](maxsize=maxsize)
        self.spoken = LRUCache[dict[str, Any]](maxsize=maxsize)

    def clear(self) -> None:
        self.retrieval.clear()
        self.answer.clear()
        self.spoken.clear()

    def snapshot(self) -> dict[str, Any]:
        return {
            "retrieval": {
                "hits": self.retrieval.stats.hits,
                "misses": self.retrieval.stats.misses,
                "hit_rate": round(self.retrieval.stats.hit_rate, 4),
            },
            "answer": {
                "hits": self.answer.stats.hits,
                "misses": self.answer.stats.misses,
                "hit_rate": round(self.answer.stats.hit_rate, 4),
            },
            "spoken": {
                "hits": self.spoken.stats.hits,
                "misses": self.spoken.stats.misses,
                "hit_rate": round(self.spoken.stats.hit_rate, 4),
            },
        }


def timed_ms(start: float) -> float:
    return round((time.perf_counter() - start) * 1000, 3)
