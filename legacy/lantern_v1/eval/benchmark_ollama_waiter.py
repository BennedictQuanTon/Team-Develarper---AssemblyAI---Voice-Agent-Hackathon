"""Same-machine A/B benchmark of the local-Ollama waiter agent, LLM only.

Times `OllamaWaiterAgent.respond()` in-process on two scenarios: the lead's 2-turn flow from
`eval/benchmark_local_qwen.py`, and the 6-turn order with a sold-out dish from `eval/benchmark_today.py`.
No speech recognition or synthesis and no API keys, so the numbers isolate the model path, which is the
part that differs between branches.

It runs unchanged in a worktree of another branch: features that branch lacks (prefetch, template replies,
agent timings) are detected and skipped, never assumed.

    uv run python -m eval.benchmark_ollama_waiter --label ours_tpl_off --out reports/ab/ours_tpl_off.json
    uv run python -m eval.benchmark_ollama_waiter --template on --reps 5 --out reports/ab/ours_tpl_on.json
    uv run python -m eval.benchmark_ollama_waiter --cold --reps 3 --out reports/ab/ours_cold.json

Before timing anything it loads the model and checks Ollama's /api/ps: if the model is not entirely in
GPU memory the run stops, because a partial CPU offload would make the numbers meaningless.
"""

from __future__ import annotations

import argparse
import asyncio
import inspect
import json
import math
import platform
import statistics
import subprocess
import sys
import time
from importlib import metadata
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.domain.lantern import LanternStore  # noqa: E402
from backend.app.domain.waiter import WaiterSession  # noqa: E402
from backend.app.pipeline.filler import classify_context_filler  # noqa: E402
from backend.app.pipeline.waiter_agent import OllamaWaiterAgent  # noqa: E402

try:  # absent on branches without the prefetch work
    from backend.app.pipeline.waiter_agent import prefetch_for_case
except ImportError:  # pragma: no cover - depends on the checked-out branch
    prefetch_for_case = None

LEAD_TURNS = [
    "What are your house specialties here?",
    "I'll take the first one with no green onions, please place the order.",
]
ORDER_TURNS = [
    ("recommend", "What would you recommend for a mild couple?"),
    ("those-two", "We'll take those two please"),
    ("86-squid", "I'd like the crispy squid too"),
    ("sub-seabass", "Okay, make it a grilled seabass instead"),
    ("side-morning-glory", "And stir-fried morning glory on the side"),
    ("place", "That's all, please place the order"),
]
ORDER_EXPECTED_DISHES = {"Pomelo Salad with Shrimp", "Lemongrass Chicken", "Grilled Seabass", "Stir-fried Morning Glory"}
ORDER_EXPECTED_TOTAL = 36.50


def percentile(values: list[float], pct: float) -> float | None:
    """Nearest-rank percentile; None for no values."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, min(len(ordered), math.ceil(pct / 100 * len(ordered))))
    return ordered[rank - 1]


def order_accuracy(basket: dict[str, Any], replies: list[str]) -> dict[str, Any]:
    """The 6-turn scenario's pass/fail gates. A latency win only counts if all of them pass."""
    lines = basket.get("basket") or []
    names = [line.get("name") for line in lines]
    total = round(sum(float(line.get("line_total", 0)) for line in lines), 2)
    gates = {
        "dishes_exact": set(names) == ORDER_EXPECTED_DISHES and len(names) == len(ORDER_EXPECTED_DISHES),
        "total_exact": abs(total - ORDER_EXPECTED_TOTAL) < 0.005,
        "sold_out_refused": "Crispy Squid" not in names,
        "placed": basket.get("placed") is True,
        "no_json_spoken": all("{" not in reply and "tool_call" not in reply for reply in replies),
    }
    return {"passed": all(gates.values()), "gates": gates, "dishes": names, "total": total}


def _supports(func: Any, name: str) -> bool:
    try:
        return name in inspect.signature(func).parameters
    except (TypeError, ValueError):
        return False


def build_agent(model: str, base_url: str, template: bool) -> OllamaWaiterAgent:
    kwargs: dict[str, Any] = {"model": model, "base_url": base_url}
    if _supports(OllamaWaiterAgent.__init__, "template_replies"):
        kwargs["template_replies"] = template
    elif template:
        raise SystemExit("this branch's OllamaWaiterAgent has no template replies; run with --template off")
    return OllamaWaiterAgent(**kwargs)


async def run_turn(agent: OllamaWaiterAgent, ss: WaiterSession, text: str) -> dict[str, Any]:
    prefetch = None
    if prefetch_for_case is not None and _supports(agent.respond, "prefetch"):
        prefetch = prefetch_for_case(ss, classify_context_filler(text), text) or None
    started = time.perf_counter()
    if prefetch is not None:
        result = await agent.respond(ss, text, prefetch)
    else:
        result = await agent.respond(ss, text)
    wall_ms = round((time.perf_counter() - started) * 1000, 1)
    tools = [call.get("tool") for call in result.get("tool_calls") or []]
    timings = result.get("timings") or {}
    return {
        "text": text,
        "wall_ms": wall_ms,
        "reply": result.get("reply", ""),
        "tools": tools,
        "prefetched": sorted(prefetch) if prefetch else [],
        # Branches without agent timings get an estimate: one round per tool call plus the reply.
        "llm_rounds": timings.get("llm_rounds", len(tools) + 1),
        "llm_rounds_estimated": "llm_rounds" not in timings,
        "agent_timings": timings,
    }


async def run_scenario(agent: OllamaWaiterAgent, name: str) -> dict[str, Any]:
    store = LanternStore()  # never the process-wide singleton: set_available would leak between reps
    ss = WaiterSession(store, session_id=f"bench-{name}")
    turns = []
    if name == "lead":
        for text in LEAD_TURNS:
            turns.append(await run_turn(agent, ss, text))
        return {"scenario": name, "turns": turns, "basket": ss.snapshot()}
    for intent, text in ORDER_TURNS:
        if intent == "86-squid":
            store.set_available("MAIN_SQUID", False)
        turn = await run_turn(agent, ss, text)
        turn["intent"] = intent
        turns.append(turn)
    basket = ss.snapshot()
    return {
        "scenario": name,
        "turns": turns,
        "basket": basket,
        "accuracy": order_accuracy(basket, [turn["reply"] for turn in turns]),
    }


async def ollama_get(client: httpx.AsyncClient, path: str) -> Any:
    response = await client.get(path)
    response.raise_for_status()
    return response.json()


async def unload(client: httpx.AsyncClient, model: str) -> None:
    """Evict the model so the next request includes a cold load (keep_alive=0 unloads immediately)."""
    response = await client.post("/api/generate", json={"model": model, "keep_alive": 0})
    response.raise_for_status()
    for _ in range(50):
        loaded = await ollama_get(client, "/api/ps")
        if not any(entry.get("name") == model or entry.get("model") == model for entry in loaded.get("models") or []):
            return
        await asyncio.sleep(0.1)


async def gpu_gate(client: httpx.AsyncClient, model: str) -> dict[str, Any]:
    loaded = await ollama_get(client, "/api/ps")
    for entry in loaded.get("models") or []:
        if entry.get("name") == model or entry.get("model") == model:
            size, vram = int(entry.get("size") or 0), int(entry.get("size_vram") or 0)
            return {"loaded": True, "size": size, "size_vram": vram, "fully_on_gpu": size > 0 and vram >= size,
                    "context_length": entry.get("context_length")}
    return {"loaded": False, "fully_on_gpu": False}


def git_sha() -> str | None:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True,
                              timeout=10).stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def package_version(name: str) -> str | None:
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return None


async def environment(client: httpx.AsyncClient, model: str) -> dict[str, Any]:
    env: dict[str, Any] = {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "git_sha": git_sha(),
        "repo": str(ROOT),
        "packages": {name: package_version(name) for name in ("langchain-core", "langchain-ollama", "ollama", "httpx")},
    }
    try:
        env["ollama_version"] = (await ollama_get(client, "/api/version")).get("version")
        for entry in (await ollama_get(client, "/api/tags")).get("models") or []:
            if entry.get("name") == model:
                env["model_digest"] = entry.get("digest")
                env["model_details"] = entry.get("details")
    except httpx.HTTPError as exc:
        env["ollama_error"] = str(exc)
    return env


def summarize(runs: list[dict[str, Any]]) -> dict[str, Any]:
    """Per scenario and turn index: median and p90 of wall time, plus accuracy pass rate."""
    summary: dict[str, Any] = {}
    for scenario in sorted({run["scenario"] for run in runs}):
        scenario_runs = [run for run in runs if run["scenario"] == scenario]
        per_turn = []
        for index in range(len(scenario_runs[0]["turns"])):
            walls = [run["turns"][index]["wall_ms"] for run in scenario_runs if index < len(run["turns"])]
            rounds = [run["turns"][index]["llm_rounds"] for run in scenario_runs if index < len(run["turns"])]
            per_turn.append({
                "turn": index + 1,
                "median_ms": round(statistics.median(walls), 1),
                "p90_ms": percentile(walls, 90),
                "mean_rounds": round(statistics.mean(rounds), 2),
            })
        totals = [sum(turn["wall_ms"] for turn in run["turns"]) for run in scenario_runs]
        entry: dict[str, Any] = {
            "reps": len(scenario_runs),
            "per_turn": per_turn,
            "conversation_median_ms": round(statistics.median(totals), 1),
            "conversation_p90_ms": percentile(totals, 90),
        }
        accuracies = [run["accuracy"]["passed"] for run in scenario_runs if "accuracy" in run]
        if accuracies:
            entry["accuracy_pass_rate"] = round(sum(accuracies) / len(accuracies), 3)
        summary[scenario] = entry
    return summary


async def main_async(args: argparse.Namespace) -> int:
    out = Path(args.out)
    if out.exists():
        print(f"refusing to overwrite {out}")
        return 2
    scenarios = ["lead", "order"] if args.scenario == "both" else [args.scenario]
    template = args.template == "on"

    async with httpx.AsyncClient(base_url=args.base_url, timeout=120.0) as client:
        env = await environment(client, args.model)
        agent = build_agent(args.model, args.base_url, template)

        # Load the model through the agent itself, so it loads with the agent's own num_ctx.
        await run_turn(agent, WaiterSession(LanternStore(), session_id="bench-warmup"), "Hello.")
        gate = await gpu_gate(client, args.model)
        print(f"GPU gate: {gate}")
        if not gate["fully_on_gpu"] and not args.allow_partial_gpu:
            print("Model is not fully in GPU memory; refusing to time it (use --allow-partial-gpu to override).")
            return 3

        runs: list[dict[str, Any]] = []
        for rep in range(1, args.reps + 1):
            for scenario in scenarios:
                if args.cold:
                    await unload(client, args.model)
                run = await run_scenario(agent, scenario)
                run.update({"rep": rep, "cold": bool(args.cold)})
                runs.append(run)
                turns = " ".join(f"{turn['wall_ms']:.0f}" for turn in run["turns"])
                verdict = "" if "accuracy" not in run else f"  accuracy={'PASS' if run['accuracy']['passed'] else 'FAIL'}"
                print(f"rep {rep} {scenario:5s} turns(ms): {turns}{verdict}")

    result = {
        "label": args.label,
        "model": args.model,
        "template_replies": template,
        "cold": bool(args.cold),
        "prefetch_available": prefetch_for_case is not None,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "environment": env,
        "gpu_gate": gate,
        "summary": summarize(runs),
        "runs": runs,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    print(json.dumps(result["summary"], indent=2))
    print(f"wrote {out}")
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--model", default="qwen2.5:3b")
    parser.add_argument("--base-url", default="http://localhost:11434")
    parser.add_argument("--template", choices=("on", "off"), default="off")
    parser.add_argument("--scenario", choices=("lead", "order", "both"), default="both")
    parser.add_argument("--reps", type=int, default=5)
    parser.add_argument("--cold", action="store_true", help="unload the model before every scenario run")
    parser.add_argument("--allow-partial-gpu", action="store_true")
    parser.add_argument("--label", default="")
    parser.add_argument("--out", required=True, help="results JSON path; an existing file is never overwritten")
    return parser.parse_args(argv)


def main() -> None:
    sys.exit(asyncio.run(main_async(parse_args())))


if __name__ == "__main__":
    main()
