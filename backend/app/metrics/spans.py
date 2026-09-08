"""JSONL span logger + waterfall helpers."""

from __future__ import annotations

import json
import statistics
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass
class TurnSpans:
    turn_id: str
    session_id: str
    query: str
    transcript: str
    answer: str
    profile: str
    provider: dict[str, str]
    timings_ms: dict[str, float]
    cache: dict[str, Any] = field(default_factory=dict)
    chunk_ids: list[str] = field(default_factory=list)
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    phase: int = 3

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class MetricsWriter:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def write_turn(self, spans: TurnSpans) -> Path:
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(spans.to_dict(), ensure_ascii=False) + "\n")
        return self.path


def new_turn_id() -> str:
    return uuid.uuid4().hex[:12]


def summarize_jsonl(path: Path) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                rows.append(json.loads(line))

    def collect(key: str) -> list[float]:
        values: list[float] = []
        for row in rows:
            timings = row.get("timings_ms") or {}
            if key in timings and timings[key] is not None:
                values.append(float(timings[key]))
        return values

    def stats(values: list[float]) -> dict[str, float]:
        if not values:
            return {}
        ordered = sorted(values)
        p95_idx = min(len(ordered) - 1, max(0, int(round(0.95 * (len(ordered) - 1)))))
        return {
            "count": len(ordered),
            "p50": round(statistics.median(ordered), 3),
            "p95": round(ordered[p95_idx], 3),
            "mean": round(statistics.mean(ordered), 3),
        }

    keys = [
        "stt_finalize_ms",
        "rag_ms",
        "llm_ttft_ms",
        "llm_total_ms",
        "tts_ttfb_ms",
        "tts_total_ms",
        "e2e_turn_ms",
    ]
    return {
        "file": str(path),
        "turns": len(rows),
        "waterfall": {key: stats(collect(key)) for key in keys},
        "latest": rows[-1] if rows else None,
    }
