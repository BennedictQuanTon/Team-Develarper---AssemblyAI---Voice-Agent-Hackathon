"""Run deterministic restaurant order-flow assertions with the configured waiter agent.

This is the smoke-test entry point extracted from the broader realtime waiter
benchmark. It intentionally does not start the WebSocket latency benchmark.
"""

from __future__ import annotations

import asyncio

from backend.app.config import get_settings
from backend.app.pipeline.waiter_agent import WaiterAgent, build_waiter_agent
from eval.benchmarks.restaurant.realtime_waiter import run_part_a


async def main() -> int:
    get_settings.cache_clear()
    settings = get_settings()
    if not settings.gemini_api_key.strip():
        print("FAIL: GEMINI_API_KEY is required for the live order-flow smoke test")
        return 2

    agent = build_waiter_agent()
    if not isinstance(agent, WaiterAgent) or not agent.available:
        print("FAIL: the configured waiter agent is not available")
        return 2

    result = await run_part_a(agent)
    print(result)
    return 0 if result.get("passed") == result.get("total") else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
