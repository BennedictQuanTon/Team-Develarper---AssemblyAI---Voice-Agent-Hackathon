"""Deterministic harness placeholder for the live multilingual exception demo."""

from pathlib import Path
import json


def load_cases() -> list[dict]:
    path = Path(__file__).parents[2] / "datasets" / "restaurant" / "multilingual_orders.v1.json"
    return json.loads(path.read_text(encoding="utf-8"))["cases"]


if __name__ == "__main__":
    print(f"loaded {len(load_cases())} multilingual cases")
