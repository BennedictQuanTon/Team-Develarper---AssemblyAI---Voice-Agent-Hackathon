# Pull Request Change Log: Lantern Repository Reorganization

**Branch:** `chore/repository-reorganization`  
**Commit:** `b02ad49a2ae11d0fd7a9137661be0bf4da1e4a55`  
**Scope:** Repository structure, naming, documentation, deterministic validation, and compatibility-preserving import/path updates.

This is the implementation record for the pull request. It complements [REPOSITORY_REORGANIZATION_PLAN.md](REPOSITORY_REORGANIZATION_PLAN.md), which describes the rationale and intended structure. This document records what actually changed.

## Executive summary

The repository now treats **The Lantern restaurant voice waiter** as the active product. The previous Da Nang tourism RAG implementation remains in the repository, but it is visibly classified under `legacy/travel/` and remains available only through the existing `AGENT_MODE=rag` compatibility path.

The change does **not** alter public restaurant HTTP routes, WebSocket paths, menu values, ordering rules, provider choices, or historical benchmark values. It reorganizes the files that support them and updates their internal import/configuration paths.

## 1. Product identity and configuration

### Changed

- Rewrote the root [README.md](../README.md) around The Lantern rather than the previous travel-agent identity.
- Changed the FastAPI title, description, version label, and health-service name to identify The Lantern voice waiter.
- Changed the default voice profile from `friendly_guide` to `friendly_waiter`.
- Moved `config/voice_profiles.yaml` to `config/voice/profiles.yaml`.
- Updated `.env.example` to use the active restaurant profile, legacy travel knowledge-base path, and ignored runtime metrics directory.
- Added `friendly_guide -> friendly_waiter` as a profile alias, so older local `.env` files continue to work.

### Compatibility retained

- `AGENT_MODE=waiter` remains the default.
- `AGENT_MODE=rag` remains supported for the preserved travel agent.
- Existing `/rag/*` endpoints remain available and now import from `legacy.travel.rag`.

## 2. Restaurant domain and active data

### Directory and file moves

| Previous location | New location | Reason |
| --- | --- | --- |
| `data/lantern/menu.json` | `data/restaurant/menu.json` | States the active product domain. |
| `data/lantern/tables.json` | `data/restaurant/floor_tables.json` | Clarifies this is restaurant floor seed data. |
| `data/lantern/schedule.json` | `data/restaurant/service_schedule.json` | Clarifies the schedule’s role. |
| `backend/app/domain/lantern.py` implementation | `backend/app/domain/restaurant/store.py` | Groups restaurant state under an explicit domain package. |
| `backend/app/domain/waiter.py` implementation | `backend/app/domain/restaurant/order_session.py` | Names the state object by its responsibility. |

### Code updates

- Added `backend/app/domain/restaurant/` as the active restaurant-domain package.
- Updated restaurant loaders to use `data/restaurant/`, `floor_tables.json`, and `service_schedule.json`.
- Updated runtime and evaluation imports to use the new package.
- Kept `backend.app.domain.lantern` and `backend.app.domain.waiter` as thin re-export modules. Any local script importing the old modules continues to resolve.

## 3. Travel RAG classified as legacy

### Directory and file moves

| Previous location | New location |
| --- | --- |
| `rag/` | `legacy/travel/rag/` |
| `data/danang_en/documents.json` | `legacy/travel/data/knowledge_base.v1.json` |
| `HACKATHON_WINNING_PLAN.md` | `legacy/travel/docs/hackathon-winning-plan.md` |
| `.cursor/plans/voice_rag_eval_architecture_ad5514f2.plan.md` | `legacy/travel/docs/voice-rag-evaluation-plan.md` |
| `scripts/demo_voice_playbook.sh` | `legacy/travel/scripts/demo_voice_playbook.sh` |

### Code updates

- Added `legacy/` and `legacy/travel/` Python packages.
- Rewrote the moved RAG imports as `legacy.travel.rag.*`.
- Updated the RAG JSON default path in `backend/app/config.py`.
- Added [legacy/travel/README.md](../legacy/travel/README.md) explaining its compatibility-only status.

### Intentionally not done

- Travel code, data, reports, and RAG endpoints were **not deleted**.
- Travel mode was **not promoted** or retested against paid providers.

## 4. Evaluation runners and datasets

Evaluation code is now grouped by purpose and product. Imports and repository-root calculations were updated to preserve the runners’ existing behavior.

### Restaurant benchmarks

| Previous name | New location |
| --- | --- |
| `eval/benchmark_local_qwen.py` | `eval/benchmarks/restaurant/llm_provider_comparison.py` |
| `eval/benchmark_simplified_flow.py` | `eval/benchmarks/restaurant/simplified_two_turn.py` |
| `eval/benchmark_today.py` | `eval/benchmarks/restaurant/regression_vs_baseline.py` |
| `eval/eval_context_fillers.py` | `eval/benchmarks/restaurant/context_fillers.py` |
| `eval/waiter_smoke.py` | `eval/benchmarks/restaurant/realtime_waiter.py` |

### Travel benchmarks

| Previous name | New location |
| --- | --- |
| `eval/eval_3cases_benchmark.py` | `eval/benchmarks/travel/multi_case_voice.py` |
| `eval/run_cache_bench.py` | `eval/benchmarks/travel/rag_cache.py` |
| `eval/run_live_e2e.py` | `eval/benchmarks/travel/live_e2e.py` |
| `eval/run_realtime_bench.py` | `eval/benchmarks/travel/realtime_voice.py` |

### Smoke tests and utilities

| Previous name | New location |
| --- | --- |
| `eval/smoke_apis.py` | `eval/smoke/providers.py` |
| `eval/smoke_3turn_playbook.py` | `eval/smoke/travel/three_turn_playbook.py` |
| `eval/smoke_human_conversation.py` | `eval/smoke/travel/human_conversation.py` |
| n/a | `eval/smoke/restaurant/order_flows.py` |
| `eval/run_waterfall.py` | `tools/summarize_latency.py` |

- Moved `eval/datasets/rag_qrels.json` to `eval/datasets/travel/rag_qrels.v1.json` to make its product and version clear.
- Added package markers to the new benchmark and smoke directories for stable imports.

## 5. Benchmark report contract and historical evidence

### New behavior for future runs

- Added `eval/reporting.py`.
- `create_run()` creates a unique, immutable directory at:

  ```text
  reports/<product>/<suite>/<UTC-run-id>/
  ├── manifest.json
  ├── results.json
  └── summary.md
  ```

- The generated manifest records product, suite, run ID, UTC creation time, source commit, working-tree state, and caller metadata.
- A run refuses to overwrite an existing result directory.
- Updated benchmark runners to write to named product/suite runs by default; explicit legacy output arguments remain where they previously existed.

### Historical report migration

- Restaurant evidence moved below `reports/restaurant/`.
- Travel evidence moved below `reports/legacy/travel/`.
- Authored audits moved below `reports/audits/restaurant/`.
- Each migrated historical run gained a separate provenance `manifest.json` without regenerating or rewriting the measured JSON payload.
- Ambiguous original times are explicitly labelled `historical-pre-2026-09-13`; known dated runs keep their available timestamp.
- Replaced machine-specific `file:///Users/davark/...` links in active report summaries with repository-relative artifact links.
- Added [reports/README.md](../reports/README.md) defining the report contract.

## 6. Runtime logs and operations metrics

- Moved the default metrics directory from a report-adjacent location to ignored `var/metrics/`.
- Updated `.gitignore` so `var/` and the legacy travel runtime path are not committed.
- Fixed the operations WebSocket summary to read `turns.jsonl`, matching the runtime writer, instead of the unrelated `spans.jsonl` path.
- Runtime logs remain distinct from curated benchmark reports.

## 7. Frontend assets and design material

### Runtime audio

- Moved all backchannel WAV files from `frontend/audio/backchannels/` to `frontend/public/audio/backchannels/`.
- Renamed `manifest.json` to `backchannels.manifest.json` and updated runtime URLs and server-side filesystem references.

### Background assets

- Retained the browser runtime background through `frontend/public/background.jpg` and verified FastAPI serves `/background.jpg` successfully.
- Removed three duplicate tracked JPG copies:
  - `frontend/src/assets/background.jpg`
  - `white-background-gradient-modern-abstract-design-round-shape/8_123dasa1.jpg`
  - `white-gray-abstract-gradient-background/1dc908eb-37b4-4134-9ae2-60a689f3336c.jpg`
- Moved the original EPS source artwork to `design/source/` instead of deleting it.

### Documentation and UI labels

- Moved `DESIGN.md` to `docs/design/ui-design-system.md` and changed its framing from the unrelated ThoughtStream personal-blog design system to The Lantern ordering UI.
- Moved `Apple_Design_Skill.md` to `docs/archive/apple_design_skill.md`.
- Updated stale frontend code comments that referenced `DESIGN.md` or ThoughtStream.
- Added `scripts/demo_restaurant.sh` as the supported restaurant demo playbook.

## 8. Tests, metadata, and continuous integration

- Added `pyproject.toml` with project metadata plus pytest and Ruff configuration.
- Added `websockets>=12.0` to `requirements.txt`, matching the project’s runtime import.
- Added six deterministic tests covering:
  - Lantern API identity plus menu and floor routes.
  - Realtime WebSocket session initialization.
  - Mentioned-dish addition and order placement.
  - Interrupted-turn snapshot restore.
  - Sold-out item protection.
  - Immutable report-run creation.
- Added `.github/workflows/ci.yml` with backend contract tests and a frontend production build on pushes and pull requests.

## 9. Documentation added or updated

- [README.md](../README.md): active product, setup, configuration, contracts, validation, and report layout.
- [REPOSITORY_REORGANIZATION_PLAN.md](REPOSITORY_REORGANIZATION_PLAN.md): migration rationale and target structure, now marked implemented.
- [architecture/system-overview.md](architecture/system-overview.md): active/legacy boundaries and runtime ownership.
- [operations/benchmarking.md](operations/benchmarking.md): deterministic checks, live-suite guardrails, and report rules.
- [reports/README.md](../reports/README.md): historical and generated report contract.

## 10. Validation completed locally

The following checks passed without calling paid/live speech or LLM providers:

```text
python -m compileall -q backend legacy eval tools
python -m unittest discover -s tests -v   # 6 tests passed
cd frontend && npm ci && npm run build    # passed
```

The FastAPI fallback asset route was also checked directly: `GET /background.jpg` returned `200 image/jpeg`.

## 11. Suggested follow-up issues

The following are useful independent tickets; they are **not hidden changes in this PR**:

1. **Decide the lifecycle of the legacy Da Nang travel agent.** Keep it maintained, extract it to another repository, or retire it with a separately approved deletion plan.
2. **Extract FastAPI routes from `backend/app/main.py`.** The repository classification is complete, but route-module extraction would be a separate behavior-sensitive refactor.
3. **Split the real-time pipeline by provider and session responsibility.** Existing orchestration remains in `backend/app/pipeline/` to preserve current runtime behavior.
4. **Move inline benchmark scenarios into versioned datasets.** This PR names and separates runners, but intentionally does not invent or alter benchmark cases.
5. **Run approved live provider smoke tests.** Local validation was keyless; live AssemblyAI, Gemini/Ollama, and Cartesia checks should be scheduled only with API-budget approval.
6. **Review CI dependency installation time.** The workflow installs the current full requirements file; it can later be optimized with a smaller deterministic test dependency set if CI duration becomes a concern.

## Reviewer checklist

- [ ] Confirm The Lantern is the intended canonical product.
- [ ] Confirm `legacy/travel/` is the desired retention boundary for the Da Nang agent.
- [ ] Check any external automation that invokes old evaluation or demo-script paths.
- [ ] Check any local `.env` that explicitly sets `VOICE_PROFILE=friendly_guide`; it remains supported but can be renamed.
- [ ] Confirm the removal of only duplicate JPG copies is acceptable; EPS source files were retained.
- [ ] Review the GitHub Actions workflow before enabling required status checks.
