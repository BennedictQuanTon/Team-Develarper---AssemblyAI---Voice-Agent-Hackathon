"""CLI: text RAG ask + timings (Phase 2, no LLM keys)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.app.config import get_settings
from rag.cache import RagCache
from rag.retrieve import ask


def main() -> None:
    settings = get_settings()
    parser = argparse.ArgumentParser(description="Ask the local Da Nang hybrid RAG (text only)")
    parser.add_argument("query", nargs="?", default="What is Ba Na Hills and the Golden Bridge?")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument("--twice", action="store_true", help="Run twice to show cache hit")
    args = parser.parse_args()

    cache = RagCache()
    first = ask(
        args.query,
        chroma_dir=settings.chroma_persist_dir,
        bm25_path=settings.bm25_index_path,
        cache=cache,
        use_cache=not args.no_cache,
        top_k=args.top_k,
    )
    print(json.dumps(first, indent=2, ensure_ascii=False))

    if args.twice:
        second = ask(
            args.query,
            chroma_dir=settings.chroma_persist_dir,
            bm25_path=settings.bm25_index_path,
            cache=cache,
            use_cache=not args.no_cache,
            top_k=args.top_k,
        )
        print("--- second call (expect cache hit) ---")
        print(json.dumps(second, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
