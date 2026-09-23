"""Immutable benchmark-run artifact helpers.

Generated reports are namespaced by product, suite, and UTC run ID so a new
run cannot silently overwrite historical evidence.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[1]


def _git_value(*args: str) -> str | None:
    try:
        value = subprocess.check_output(
            ["git", *args],
            cwd=ROOT_DIR,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return None
    return value or None


@dataclass(frozen=True)
class BenchmarkRun:
    """Paths and identity for one generated benchmark run."""

    product: str
    suite: str
    run_id: str
    directory: Path

    @property
    def manifest_path(self) -> Path:
        return self.directory / "manifest.json"

    @property
    def results_path(self) -> Path:
        return self.directory / "results.json"

    @property
    def summary_path(self) -> Path:
        return self.directory / "summary.md"


def create_run(
    *,
    product: str,
    suite: str,
    output_root: Path | None = None,
    run_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> BenchmarkRun:
    """Create an empty immutable result directory and its provenance manifest."""

    if not product or not suite:
        raise ValueError("product and suite are required")

    resolved_run_id = run_id or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")
    root = output_root or (ROOT_DIR / "reports")
    directory = root / product / suite / resolved_run_id
    if directory.exists():
        raise FileExistsError(f"Benchmark run already exists: {directory}")
    directory.mkdir(parents=True, exist_ok=False)

    dirty = _git_value("status", "--porcelain=v1")
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "source": "generated",
        "product": product,
        "suite": suite,
        "run_id": resolved_run_id,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "git_commit": _git_value("rev-parse", "HEAD"),
        "git_dirty": bool(dirty),
        "metadata": metadata or {},
    }
    (directory / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return BenchmarkRun(product=product, suite=suite, run_id=resolved_run_id, directory=directory)


def write_results(run: BenchmarkRun, payload: dict[str, Any]) -> Path:
    run.results_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return run.results_path


def write_summary(run: BenchmarkRun, content: str) -> Path:
    run.summary_path.write_text(content.rstrip() + "\n", encoding="utf-8")
    return run.summary_path
