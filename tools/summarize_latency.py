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
from eval.reporting import create_run, write_results


def main() -> None:
    settings = get_settings()
    parser = argparse.ArgumentParser(description="Summarize turns.jsonl waterfall")
    parser.add_argument(
        "--file",
        type=Path,
        default=settings.metrics_dir / "turns.jsonl",
    )
    parser.add_argument("--out", type=Path, default=None, help="Optional explicit summary JSON path")
    parser.add_argument("--run-id", type=str, default=None)
    args = parser.parse_args()

    summary = summarize_jsonl(args.file)
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
        out_path = args.out
    else:
        run = create_run(product="legacy-travel", suite="latency-waterfall", run_id=args.run_id)
        out_path = write_results(run, summary)
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    print(f"wrote {out_path}")


if __name__ == "__main__":
    main()
