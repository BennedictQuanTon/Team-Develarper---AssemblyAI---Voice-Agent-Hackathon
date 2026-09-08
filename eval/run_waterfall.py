"""Summarize turn latency JSONL into a waterfall report."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.app.config import get_settings
from backend.app.metrics.spans import summarize_jsonl


def main() -> None:
    settings = get_settings()
    parser = argparse.ArgumentParser(description="Summarize turns.jsonl waterfall")
    parser.add_argument(
        "--file",
        type=Path,
        default=settings.metrics_dir / "turns.jsonl",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=settings.metrics_dir / "waterfall_phase3.json",
    )
    args = parser.parse_args()

    summary = summarize_jsonl(args.file)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
