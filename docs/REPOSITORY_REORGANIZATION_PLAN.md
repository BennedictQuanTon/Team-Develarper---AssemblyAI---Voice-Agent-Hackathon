# Repository Reorganization Plan

Status: **Implemented on `chore/repository-reorganization`; retained as the migration record.**
Prepared: **2026-09-16**
Repository baseline: `6482fbfc8916d616dab78fb2b3b9276e97abe4e6`
Primary product assumption: **The Lantern voice waiter**

## 1. Purpose

This document defines a safe, staged plan for reorganizing the repository without changing its observable behavior. It also specifies the proposed directory structure, file naming rules, benchmark layout, report format, migration order, verification gates, and rollback expectations.

The repository currently contains two product generations:

1. **The Lantern restaurant voice waiter**, which is the active frontend and the default real-time runtime mode.
2. **The Da Nang tourism voice assistant**, which remains in the root documentation, RAG implementation, data, API identity, and several evaluation suites.

The recommended direction is to make The Lantern the canonical product and preserve the Da Nang implementation as explicitly labeled legacy material until a separate deletion decision is approved. If both products must remain actively supported, they should be separated into independent applications instead of sharing one implicit `agent_mode` switch.

## 2. Goals

The reorganization should achieve the following outcomes:

- Make the repository identity consistent with the product that currently runs by default.
- Separate active restaurant code from legacy travel code.
- Give smoke tests, benchmarks, datasets, generated results, and authored audits distinct locations.
- Replace ambiguous or time-sensitive names such as `today`, `detail`, `eval`, and `phase3` with durable names.
- Make every benchmark result traceable to its code revision, configuration, dataset, provider, and execution time.
- Remove duplicate application assets while retaining any genuinely required source artwork.
- Reduce oversized modules through behavior-preserving extraction after regression coverage exists.
- Add standard Python and frontend validation entry points.
- Preserve existing routes, WebSocket message contracts, data semantics, and demo behavior throughout the migration.

## 3. Non-goals

This plan does not authorize any of the following:

- Deleting the Da Nang travel implementation or its historical reports.
- Changing menu content, prices, table state, ordering rules, or recommendation behavior.
- Changing AssemblyAI, Gemini, Ollama, or Cartesia providers.
- Re-running paid or rate-limited live benchmarks without explicit approval.
- Rewriting the real-time voice pipeline.
- Changing public HTTP routes or WebSocket message schemas.
- Changing benchmark thresholds or rewriting historical results.
- Squashing Git history, force-pushing, or deleting branches.

## 4. Confirmed Current State

### 4.1 Active product path

The current frontend presents **The Lantern Fine Dining Voice Concierge**. The default backend setting is `agent_mode = "waiter"`. In that mode, the real-time path is:

```text
Browser microphone
  -> WebSocket /ws/realtime
  -> RealtimeSessionController
  -> AssemblyAI streaming transcription
  -> WaiterAgent
  -> WaiterSession + LanternStore
  -> deterministic menu/floor/order tools
  -> Gemini or Ollama language response
  -> Cartesia streaming speech
  -> browser audio, captions, basket, and telemetry events
```

The operational frontend also consumes `/ws/ops` for restaurant floor, menu availability, and metrics snapshots.

### 4.2 Legacy travel path

The repository still supports or references a Da Nang travel flow through:

- `rag/` for Chroma and BM25 retrieval.
- `data/danang_en/documents.json` as the travel knowledge base.
- `/rag/ask` and `/rag/retrieve` endpoints.
- The non-streaming `Orchestrator` path.
- `agent_mode = "rag"` in the real-time session controller.
- Travel-specific smoke tests and benchmarks.
- The root README, FastAPI title, health service name, voice-profile naming, and strategy documents.

### 4.3 Evaluation and report state

The repository currently has:

- 14 Python files directly under `eval/`.
- 11 tracked JSON result files under `reports/`.
- 9 tracked Markdown reports under `reports/`.
- A mixture of restaurant and travel results in one flat directory.
- Fixed output filenames that are overwritten by later runs.
- Inconsistent timestamps and metadata between report schemas.
- Several Markdown reports containing machine-specific `file:///Users/davark/...` links.
- A promised `benchmark_today_eval.json` and `benchmark_today_report.md` pair that is not currently tracked.

### 4.4 Asset state

The repository contains two top-level graphic-source directories. Together they account for most of the tracked repository size. The active background JPG exists three times with identical content, and two large EPS files appear unrelated to application runtime.

### 4.5 Tooling state

The current repository does not contain:

- `pyproject.toml`
- pytest configuration
- Python lint configuration
- a CI workflow
- a frontend lint script
- a frontend test script

The Python code imports `websockets`, but it is not directly declared in `requirements.txt`.

## 5. Selected Organization Strategy

### 5.1 Recommended strategy

Use a **canonical product plus explicit legacy area**:

- The Lantern remains in the normal application paths.
- Da Nang travel material moves under `legacy/travel/` or is clearly labeled as legacy within data, evaluation, reports, and documentation.
- Public compatibility is maintained while files move.
- Deletion is considered only after the legacy path is proven unnecessary and separately approved.

This is safer and smaller than immediately creating a multi-application monorepo.

### 5.2 Alternative if both products remain active

If both products are intentionally active, use:

```text
apps/
├── lantern/
└── danang-guide/
```

Each application would need its own settings, API identity, datasets, tests, report namespace, launch command, and documentation. Shared provider clients could then live in `packages/voice-core/`. This alternative is more expensive and should only be selected if both products have real users or submission requirements.

## 6. Proposed Repository Structure

The target below assumes The Lantern is canonical and Da Nang travel support is retained temporarily as legacy.

```text
assemblyai-lantern-voice-waiter/
├── README.md
├── pyproject.toml
├── requirements.txt
├── .env.example
├── .gitignore
│
├── backend/
│   ├── __init__.py
│   └── app/
│       ├── __init__.py
│       ├── main.py
│       ├── config.py
│       │
│       ├── api/
│       │   ├── __init__.py
│       │   ├── dependencies.py
│       │   ├── schemas.py
│       │   └── routes/
│       │       ├── __init__.py
│       │       ├── health.py
│       │       ├── menu.py
│       │       ├── sessions.py
│       │       ├── metrics.py
│       │       ├── realtime.py
│       │       ├── ops.py
│       │       └── legacy_rag.py
│       │
│       ├── domain/
│       │   └── restaurant/
│       │       ├── __init__.py
│       │       ├── models.py
│       │       ├── store.py
│       │       └── order_session.py
│       │
│       ├── voice/
│       │   ├── __init__.py
│       │   ├── contracts.py
│       │   ├── profiles.py
│       │   ├── fillers.py
│       │   ├── session_store.py
│       │   ├── providers/
│       │   │   ├── __init__.py
│       │   │   ├── assemblyai_batch.py
│       │   │   ├── assemblyai_streaming.py
│       │   │   ├── gemini.py
│       │   │   ├── ollama.py
│       │   │   ├── cartesia_batch.py
│       │   │   └── cartesia_streaming.py
│       │   └── runtime/
│       │       ├── __init__.py
│       │       ├── realtime_controller.py
│       │       ├── waiter_runtime.py
│       │       └── legacy_travel_runtime.py
│       │
│       └── metrics/
│           ├── __init__.py
│           └── turns.py
│
├── frontend/
│   ├── index.html
│   ├── package.json
│   ├── package-lock.json
│   ├── tsconfig.json
│   ├── vite.config.ts
│   ├── public/
│   │   ├── background.jpg
│   │   └── audio/
│   │       └── backchannels/
│   │           ├── backchannels.manifest.json
│   │           └── *.wav
│   └── src/
│       ├── main.ts
│       ├── audio/
│       ├── components/
│       ├── services/
│       ├── styles/
│       │   ├── tokens.css
│       │   ├── base.css
│       │   ├── guest.css
│       │   ├── operations.css
│       │   └── telemetry.css
│       └── types/
│
├── config/
│   └── voice/
│       └── profiles.yaml
│
├── data/
│   └── restaurant/
│       ├── menu.json
│       ├── floor_tables.json
│       └── service_schedule.json
│
├── legacy/
│   └── travel/
│       ├── README.md
│       ├── data/
│       │   └── knowledge_base.v1.json
│       ├── rag/
│       │   ├── __init__.py
│       │   ├── ask.py
│       │   ├── bm25_store.py
│       │   ├── cache.py
│       │   ├── chunking.py
│       │   ├── ingest.py
│       │   ├── retrieve.py
│       │   └── store.py
│       └── docs/
│           ├── hackathon-winning-plan.md
│           └── voice-rag-evaluation-plan.md
│
├── eval/
│   ├── __init__.py
│   ├── datasets/
│   │   ├── restaurant/
│   │   │   ├── context_fillers.v1.json
│   │   │   └── waiter_flows.v1.json
│   │   └── travel/
│   │       └── rag_qrels.v1.json
│   ├── smoke/
│   │   ├── providers.py
│   │   ├── restaurant/
│   │   │   └── order_flows.py
│   │   └── travel/
│   │       ├── three_turn_playbook.py
│   │       └── human_conversation.py
│   └── benchmarks/
│       ├── restaurant/
│       │   ├── context_fillers.py
│       │   ├── simplified_two_turn.py
│       │   ├── llm_provider_comparison.py
│       │   ├── regression_vs_baseline.py
│       │   └── realtime_latency.py
│       └── travel/
│           ├── multi_case_voice.py
│           ├── realtime_voice.py
│           ├── live_e2e.py
│           └── rag_cache.py
│
├── reports/
│   ├── README.md
│   ├── restaurant/
│   │   └── <suite>/<run-id>/
│   │       ├── manifest.json
│   │       ├── results.json
│   │       └── summary.md
│   ├── legacy/
│   │   └── travel/
│   │       └── <suite>/<run-id>/
│   │           ├── manifest.json
│   │           ├── results.json
│   │           └── summary.md
│   └── audits/
│       ├── restaurant/
│       └── legacy-travel/
│
├── tests/
│   ├── unit/
│   │   ├── domain/
│   │   ├── voice/
│   │   └── metrics/
│   ├── contract/
│   │   ├── http/
│   │   └── websocket/
│   └── fixtures/
│
├── tools/
│   ├── generate_backchannels.py
│   ├── create_report_manifest.py
│   └── summarize_latency.py
│
├── scripts/
│   ├── start.sh
│   ├── stop.sh
│   ├── status.sh
│   └── demo_restaurant.sh
│
└── docs/
    ├── REPOSITORY_REORGANIZATION_PLAN.md
    ├── architecture/
    │   ├── system-overview.md
    │   ├── realtime-protocol.md
    │   └── report-artifact-contract.md
    ├── design/
    │   └── ui-design-system.md
    └── operations/
        ├── local-development.md
        └── benchmarking.md
```

## 7. Directory Responsibilities

### `backend/app/api/`

Owns FastAPI routes, request/response schemas, dependency accessors, and WebSocket entry points. It must not own menu rules, basket calculations, provider-specific networking, or benchmark logic.

`main.py` should only create the application, register routers, mount static assets, and define lifespan behavior.

### `backend/app/domain/restaurant/`

Owns deterministic restaurant state and operations:

- menu items
- prices
- inventory availability
- table state
- basket lines
- modifier validation
- totals
- order placement
- mention resolution

This layer must stay independent of FastAPI, AssemblyAI, Gemini, Ollama, Cartesia, and frontend message formats.

### `backend/app/voice/`

Owns provider contracts, provider adapters, session coordination, filler selection, and real-time execution.

- `providers/` contains external API integration details.
- `runtime/` contains the use-case orchestration for restaurant and legacy travel modes.
- `contracts.py` contains stable internal interfaces and result types.

### `data/restaurant/`

Contains version-controlled seed data used by the restaurant domain. Runtime mutations remain in memory unless an explicit persistence system is added later.

### `legacy/travel/`

Contains the older Da Nang knowledge base, retrieval implementation, and historical planning documents. The directory name communicates that it is not the primary product. It should remain executable only if compatibility is still required.

### `eval/smoke/`

Contains fast pass/fail checks that answer whether a provider or complete scenario is operational. Smoke checks should minimize cost and should not be treated as statistically meaningful performance measurements.

### `eval/benchmarks/`

Contains measured suites with explicit datasets, run metadata, thresholds, and immutable output directories. Product-specific subdirectories prevent restaurant and travel results from being compared accidentally.

### `tests/`

Contains repeatable automated tests that do not depend on live paid APIs by default.

- `unit/` verifies deterministic functions and state transitions.
- `contract/` locks HTTP and WebSocket payload compatibility.
- `fixtures/` contains reusable test data that is not production seed data.

### `reports/`

Contains benchmark outputs and authored audits, not application runtime logs.

- Generated results live under product, suite, and run ID.
- Human-authored investigations live under `reports/audits/`.
- Runtime JSONL logs should eventually move to an ignored `var/metrics/` directory so they cannot be confused with curated benchmark results.

### `tools/`

Contains development utilities that generate assets or transform data. These tools are not application launchers and are not evaluation suites.

### `scripts/`

Contains only operational entry points used by developers or demos: start, stop, status, and the supported demo playbook.

### `docs/`

Contains current architecture, development, operation, design, and migration documentation. Obsolete plans should be clearly archived rather than presented as current instructions.

## 8. Naming Standards

### 8.1 General rules

- Use lowercase `snake_case` for Python modules.
- Use lowercase `kebab-case` for report suite directories and Markdown filenames.
- Use explicit product namespaces: `restaurant` or `travel`.
- Use nouns for data files and verbs only for executable tools.
- Avoid `today`, `yesterday`, `new`, `final`, `latest`, and phase numbers in permanent filenames.
- Avoid generic suffixes such as `_eval`, `_detail`, and `_report` when the containing directory already communicates the type.
- Encode versions in datasets, for example `waiter_flows.v1.json`.
- Encode run time in the run directory, not in every file inside it.

### 8.2 Run ID format

Use UTC in a sortable, filesystem-safe form:

```text
YYYY-MM-DDTHHMMSSZ
```

Example:

```text
reports/restaurant/context-fillers/2026-09-12T045537Z/
```

If multiple runs can start in the same second, append the short Git commit or a short random suffix:

```text
2026-09-12T045537Z-6482fbf
```

### 8.3 Standard files per benchmark run

```text
manifest.json
results.json
summary.md
```

Optional large artifacts may live below:

```text
audio/
logs/
plots/
```

Those optional directories should be ignored by default unless a specific artifact is intentionally committed.

## 9. Evaluation Runner Rename Map

| Current path | Proposed path | Action |
| --- | --- | --- |
| `eval/benchmark_local_qwen.py` | `eval/benchmarks/restaurant/llm_provider_comparison.py` | Rename and make provider/model selection metadata-driven. |
| `eval/benchmark_simplified_flow.py` | `eval/benchmarks/restaurant/simplified_two_turn.py` | Rename; keep its two-turn scenario behavior unchanged. |
| `eval/benchmark_today.py` | `eval/benchmarks/restaurant/regression_vs_baseline.py` | Rename and require an explicit baseline path or baseline run ID. |
| `eval/eval_context_fillers.py` | `eval/benchmarks/restaurant/context_fillers.py` | Rename and move its inline 40-query dataset to a versioned dataset file. |
| `eval/waiter_smoke.py` | `eval/smoke/restaurant/order_flows.py` and `eval/benchmarks/restaurant/realtime_latency.py` | Split deterministic scenario assertions from live latency measurement and report generation. |
| `eval/eval_3cases_benchmark.py` | `eval/benchmarks/travel/multi_case_voice.py` | Move under the legacy travel namespace. |
| `eval/run_realtime_bench.py` | `eval/benchmarks/travel/realtime_voice.py` | Move under the legacy travel namespace. |
| `eval/run_live_e2e.py` | `eval/benchmarks/travel/live_e2e.py` | Move under the legacy travel namespace. |
| `eval/run_cache_bench.py` | `eval/benchmarks/travel/rag_cache.py` | Name the subsystem being measured. |
| `eval/smoke_human_conversation.py` | `eval/smoke/travel/human_conversation.py` | Classify it as a travel smoke scenario. |
| `eval/smoke_3turn_playbook.py` | `eval/smoke/travel/three_turn_playbook.py` | Move under the travel smoke namespace. |
| `eval/smoke_apis.py` | `eval/smoke/providers.py` | Keep as the minimal external-provider health check. |
| `eval/run_waterfall.py` | `tools/summarize_latency.py` | Move because it transforms metrics rather than evaluating the product. |
| `eval/__init__.py` | `eval/__init__.py` | Keep and update its package description. |

## 10. JSON and Data Rename Map

| Current path | Proposed path | Reason |
| --- | --- | --- |
| `data/lantern/menu.json` | `data/restaurant/menu.json` | The containing directory states the domain. |
| `data/lantern/tables.json` | `data/restaurant/floor_tables.json` | Clarifies that these are floor-state seed records. |
| `data/lantern/schedule.json` | `data/restaurant/service_schedule.json` | Clarifies the type of schedule. |
| `data/danang_en/documents.json` | `legacy/travel/data/knowledge_base.v1.json` | Marks the product and dataset version. |
| `eval/datasets/rag_qrels.json` | `eval/datasets/travel/rag_qrels.v1.json` | Marks the product and dataset version. |
| inline context-filler cases | `eval/datasets/restaurant/context_fillers.v1.json` | Makes the dataset reusable and versioned. |
| inline waiter scenarios | `eval/datasets/restaurant/waiter_flows.v1.json` | Separates scenario data from runner logic. |
| `frontend/audio/backchannels/manifest.json` | `frontend/public/audio/backchannels/backchannels.manifest.json` | Names the manifest and places runtime assets under `public/`. |

The key names inside existing JSON files should not be changed in the same commit as moving the files. Schema migrations should be separate, reviewed changes with compatibility tests.

## 11. Report Artifact Migration Map

Historical outputs should be moved, not regenerated, so their original numbers remain intact.

| Current artifact(s) | Proposed destination |
| --- | --- |
| `multi_case_benchmark.json`, `multi_case_evaluation_report.md` | `reports/legacy/travel/multi-case/2026-09-09T144349Z/{results.json,summary.md}` |
| `realtime_eval.json` | `reports/legacy/travel/realtime-voice/<historical-run-id>/results.json` |
| `human_conversation_eval.json` | `reports/legacy/travel/human-conversation/<historical-run-id>/results.json` |
| `e2e_live.json` | `reports/legacy/travel/live-e2e/<historical-run-id>/results.json` |
| `cache_bench_phase2.json` | `reports/legacy/travel/rag-cache/<historical-run-id>/results.json` |
| `waterfall_phase3.json` | `reports/legacy/travel/latency-waterfall/<historical-run-id>/summary.json` |
| `benchmark_simplified_flow_eval.json`, `benchmark_simplified_flow_report.md` | `reports/restaurant/simplified-two-turn/2026-09-12T051840Z/{results.json,summary.md}` |
| `benchmark_local_qwen_eval.json`, `benchmark_local_qwen_report.md` | `reports/restaurant/llm-provider-comparison/<historical-run-id>/{results.json,summary.md}` |
| `eval_context_fillers.json`, `eval_context_fillers_report.md` | `reports/restaurant/context-fillers/2026-09-12T045537Z/{results.json,summary.md}` |
| `waiter_e2e_detail.json`, `waiter_e2e_report.md` | `reports/restaurant/realtime-waiter/<historical-run-id>/{turns.json,summary.md}` |
| `waiter_latency_detail.json`, `waiter_latency_report.md` | `reports/restaurant/waiter-latency/2026-09-11T000000Z/{turns.json,summary.md}` with the time marked unknown in the manifest |
| `waiter_smoke_report.md` | `reports/restaurant/order-flows/<historical-run-id>/summary.md` |
| `customer_flow_latency_audit.md` | `reports/audits/restaurant/customer-flow-latency.md` |
| `phase_latency_breakdown_audit.md` | `reports/audits/restaurant/single-pass-latency.md` |

For historical files without a reliable timestamp, use a clearly labeled migration ID rather than inventing a time:

```text
historical-pre-2026-09-13
```

Every migrated historical run should receive a `manifest.json` containing:

```json
{
  "schema_version": 1,
  "product": "restaurant",
  "suite": "realtime-waiter",
  "run_id": "historical-pre-2026-09-13",
  "source": "migrated",
  "source_files": [
    "reports/waiter_e2e_detail.json",
    "reports/waiter_e2e_report.md"
  ],
  "git_commit": null,
  "git_dirty": null,
  "started_at_utc": null,
  "completed_at_utc": null,
  "dataset_version": null,
  "providers": {},
  "notes": [
    "Unknown fields were preserved as null rather than inferred."
  ]
}
```

## 12. Documentation Migration Map

| Current path | Proposed path or treatment |
| --- | --- |
| `README.md` | Rewrite in place as the canonical Lantern overview and local-development entry point. |
| `DESIGN.md` | Move to `docs/design/ui-design-system.md` and update the framing from a personal-blog design system to the actual Lantern UI. |
| `HACKATHON_WINNING_PLAN.md` | Move to `legacy/travel/docs/hackathon-winning-plan.md`. Preserve it as historical planning, not current truth. |
| `.cursor/plans/voice_rag_eval_architecture_ad5514f2.plan.md` | Move its useful content to `legacy/travel/docs/voice-rag-evaluation-plan.md`, then ignore editor-specific plan state. |
| `Apple_Design_Skill.md` | Remove from the application repository after confirmation, or convert only the relevant project decisions into `docs/design/apple-hig-notes.md`. The current file references supporting material that is absent. |
| this document | Keep at `docs/REPOSITORY_REORGANIZATION_PLAN.md` until the migration is complete; then mark it completed or archive it. |

## 13. Detailed Implementation Plan

### Phase 0 — Confirm scope and record the baseline

Objective: prevent the reorganization from encoding the wrong product decision.

Actions:

1. Confirm that The Lantern is the canonical submission/product.
2. Confirm whether the Da Nang travel mode must remain executable or only preserved historically.
3. Record the starting commit and clean/dirty state.
4. Record supported launch commands and externally visible routes.
5. Capture representative HTTP and WebSocket payloads for compatibility tests.
6. Inventory live-provider requirements without invoking the providers.

Acceptance criteria:

- Product ownership is explicit.
- Legacy retention requirements are written down.
- No file movement begins while the scope is ambiguous.
- The starting Git state is reproducible.

Rollback: none required because this phase is documentation-only.

### Phase 1 — Add regression and contract protection

Objective: protect working behavior before moving implementation files.

Actions:

1. Add `pyproject.toml` with pytest and lint configuration.
2. Add deterministic unit tests for `LanternStore` and `WaiterSession`.
3. Add tests for price totals, modifiers, sold-out items, allergens, recommendation caching, pronoun/mention resolution, and order placement.
4. Add HTTP contract tests for `/health`, `/api`, `/menu`, `/menu/available`, `/floor`, and `/session/reset`.
5. Add WebSocket contract fixtures for session-ready, transcript, waiter action, basket update, turn completion, interruption, and error events.
6. Add a test proving metrics are read from the same filename that runtime writers use.
7. Keep live provider tests opt-in and excluded from default test runs.

Likely files created:

- `pyproject.toml`
- `tests/unit/domain/test_lantern_store.py`
- `tests/unit/domain/test_waiter_session.py`
- `tests/contract/http/test_restaurant_routes.py`
- `tests/contract/websocket/test_realtime_messages.py`
- `tests/fixtures/`

Acceptance criteria:

- Default tests run without vendor API keys.
- Existing deterministic restaurant behaviors are covered.
- Public payload shapes are asserted before routes or modules move.

Rollback: remove only newly added test/tooling files if they cannot be made stable; do not change production behavior to satisfy an incorrect test.

### Phase 2 — Correct product identity and documentation

Objective: make the repository explain the product that currently runs.

Actions:

1. Rewrite the root README for The Lantern.
2. Document the current architecture and supported launch flow.
3. Update FastAPI title, description, version labeling, and health service identity.
4. Rename travel-oriented voice profile identifiers to restaurant-oriented names while temporarily accepting old profile aliases.
5. Mark `/rag/*` routes and `agent_mode=rag` as legacy if they remain available.
6. Archive travel plans and remove the stray README text.
7. Replace machine-specific `file://` links with repository-relative links.

Acceptance criteria:

- Root documentation, frontend title, `/api`, `/health`, and default settings identify the same product.
- Existing clients continue to work.
- Legacy functionality is labeled rather than silently deleted.

Rollback: revert identity text and aliases independently; no data migration should be coupled to this phase.

### Phase 3 — Introduce benchmark and report infrastructure

Objective: create stable naming and provenance before moving historical artifacts.

Actions:

1. Create `eval/smoke/`, `eval/benchmarks/`, and product subdirectories.
2. Create `eval/datasets/restaurant/` and `eval/datasets/travel/`.
3. Add a shared benchmark run-context helper.
4. Add `tools/create_report_manifest.py`.
5. Define `manifest.json` schema version 1.
6. Require benchmark runners to accept `--output-root` and optionally `--run-id`.
7. Default new runs to immutable UTC run directories.
8. Prevent silent overwrite unless an explicit `--overwrite` flag is supplied.
9. Add `reports/README.md` explaining generated versus authored material.

Acceptance criteria:

- A dry local benchmark can create a complete run directory.
- Every run records commit, dirty state, product, suite, config, dataset, and timestamps.
- Existing benchmark calculations remain unchanged.

Rollback: retain the old output-path option until all scripts are migrated.

### Phase 4 — Move and rename evaluation runners

Objective: separate suites by responsibility and product.

Actions:

1. Move provider smoke checks first because they have few internal dependencies.
2. Move travel-only suites under travel directories.
3. Move restaurant-only suites under restaurant directories.
4. Split `waiter_smoke.py` into deterministic scenario smoke tests and real-time latency benchmarking.
5. Extract inline context-filler and waiter-flow datasets to versioned JSON.
6. Move `run_waterfall.py` to `tools/summarize_latency.py`.
7. Update imports, README commands, script references, and report links.
8. Add temporary compatibility wrappers at old paths only if external automation still calls them.

Acceptance criteria:

- Every old suite has one documented new command.
- Deterministic smoke tests pass without live keys.
- A search finds no stale imports or old output paths except intentional compatibility wrappers and migration notes.

Rollback: restore old entrypoint wrappers while keeping the new internal modules.

### Phase 5 — Migrate historical reports without changing results

Objective: preserve evidence while making provenance explicit.

Actions:

1. Pair each raw JSON result with its Markdown summary where possible.
2. Classify each artifact as restaurant, legacy travel, or authored audit.
3. Move artifacts into product/suite/run directories.
4. Create migration manifests using only known facts.
5. Use `null` for unknown commit, time, provider, or configuration values.
6. Preserve original numeric values and text exactly except for broken links.
7. Remove unnecessary `.gitkeep` files after directories contain tracked files.
8. Update all repository references to the new paths.

Acceptance criteria:

- Every original artifact has a documented destination.
- File checksums can be used to prove that numeric JSON content was not altered unintentionally.
- All Markdown links resolve relative to the repository.
- No historical benchmark is presented as a newly executed result.

Rollback: move files back using Git history; manifests are additive and can be removed independently.

### Phase 6 — Reorganize data and configuration

Objective: make active and legacy data ownership explicit.

Actions:

1. Move Lantern seed files to `data/restaurant/`.
2. Rename `tables.json` and `schedule.json` to descriptive filenames.
3. Update `LanternStore` path constants.
4. Move the travel knowledge base under `legacy/travel/data/`.
5. Move RAG runtime storage defaults to an ignored legacy-specific directory.
6. Move voice profiles to `config/voice/profiles.yaml`.
7. Update `.env.example` with active settings first and a clearly labeled legacy section.
8. Validate all JSON and YAML after moving.

Acceptance criteria:

- The Lantern starts using the new data paths.
- Menu, floor, schedule, and order behavior are unchanged.
- Legacy travel mode either still runs through documented paths or is explicitly disabled by the approved scope decision.

Rollback: support old and new paths for one transition commit, preferring the new path and warning on the old path.

### Phase 7 — Reorganize backend boundaries

Objective: reduce coupling without rewriting the runtime.

Actions:

1. Extract route groups from `main.py` into routers.
2. Move restaurant models/store/session into `domain/restaurant/`.
3. Move provider implementations into `voice/providers/`.
4. Move `RealtimeSessionController` into `voice/runtime/realtime_controller.py`.
5. Extract restaurant execution from the shared controller into `waiter_runtime.py`.
6. Keep legacy travel orchestration in `legacy_travel_runtime.py` or under `legacy/travel/`.
7. Rename `spans.py` to `turns.py` only after all imports and report tools are covered.
8. Resolve the `turns.jsonl` versus `spans.jsonl` mismatch with one canonical setting.
9. Keep route paths and payloads unchanged.

Acceptance criteria:

- Contract tests pass without changes to expected payloads.
- Default waiter flow remains operational.
- Module boundaries match ownership: API, domain, voice providers, runtime, metrics.
- No circular imports are introduced.

Rollback: perform extractions in small commits so each move can be reverted independently.

### Phase 8 — Clean and reorganize frontend assets/styles

Objective: remove duplication and make frontend ownership clearer.

Actions:

1. Keep one background JPG under `frontend/public/`.
2. Confirm whether the EPS source files are needed before removing or relocating them.
3. Move backchannel WAV files under `frontend/public/audio/backchannels/`.
4. Update static URLs and the generator output path.
5. Split `components.css` by guest, operations, and telemetry surfaces while preserving import order.
6. Keep current TypeScript component/service/type boundaries unless a concrete coupling problem requires a move.
7. Add frontend lint and test scripts.

Acceptance criteria:

- Production build resolves every asset.
- UI screenshots or manual checks show no visual changes.
- Microphone capture, streamed playback, backchannels, basket updates, ops view, and telemetry view still function.
- Only one runtime background image remains tracked.

Rollback: retain the old asset path as a temporary alias if build or runtime consumers were missed.

### Phase 9 — Operational scripts, dependency declarations, and CI

Objective: make the reorganized repository reproducible for contributors.

Actions:

1. Rename the demo script to `demo_restaurant.sh`.
2. Update lifecycle scripts to the canonical product name.
3. Move the audio generator to `tools/generate_backchannels.py`.
4. Declare direct Python dependencies, including `websockets`.
5. Add Python formatting/lint checks.
6. Add frontend lint and build checks.
7. Add CI jobs that do not require vendor secrets.
8. Document optional live-provider checks separately.

Acceptance criteria:

- A clean environment can install declared dependencies.
- Default CI requires no secrets.
- Python tests, JSON validation, frontend checks, and build all pass.
- Live checks are opt-in and clearly labeled as potentially billable/rate-limited.

Rollback: CI and lint changes remain separate from runtime changes so strictness can be adjusted without reverting the reorganization.

### Phase 10 — Final audit and legacy decision

Objective: close the migration and avoid permanent compatibility clutter.

Actions:

1. Search for old paths, filenames, product labels, and broken links.
2. Compare public routes and WebSocket contracts with the Phase 0 baseline.
3. Confirm all tracked JSON is valid.
4. Confirm ignored runtime output is not accidentally committed.
5. Review compatibility wrappers and remove only those confirmed unused.
6. Decide separately whether legacy travel code should remain, move to its own branch/repository, or be deleted.
7. Mark this plan completed with the final commit references.

Acceptance criteria:

- Repository identity is consistent.
- Tests and builds pass.
- Report artifacts are traceable.
- No unapproved legacy deletion occurred.
- Git status is clean after the final commit.

## 14. Verification Strategy

Verification should be proportional to each phase and should avoid live API usage unless explicitly authorized.

### 14.1 Static checks

- Parse every tracked JSON file with a standards-compliant JSON parser.
- Parse the voice-profile YAML.
- Search for old paths after every move.
- Search for machine-specific `file://` links.
- Check for duplicated binary hashes.
- Check that ignored runtime directories remain untracked.

### 14.2 Python checks

After Phase 1 introduces test configuration:

```bash
python -m pytest tests/unit tests/contract
```

The exact lint commands should be documented only after the selected formatter/linter is added to `pyproject.toml`.

### 14.3 Frontend checks

```bash
cd frontend
npm ci
npm run build
```

Add `npm run lint` and `npm test` only after those scripts and their dependencies exist.

### 14.4 Local application checks

Without invoking live voice providers:

- Start the application in a documented stub or deterministic test mode.
- Verify `/health`.
- Verify `/api`.
- Verify menu and floor routes.
- Verify the frontend loads assets.
- Verify WebSocket connection and schema-level events using test doubles.

### 14.5 Optional live checks

Run only with explicit approval and configured keys:

- AssemblyAI streaming transcription smoke test.
- Gemini or Ollama response test.
- Cartesia synthesis test.
- One restaurant real-time order flow.
- Barge-in behavior.
- A selected benchmark suite writing to a new immutable run directory.

Historical benchmark values must not be overwritten during validation.

## 15. Risk Register

### Public contract drift

Risk: moving route and WebSocket code may alter payloads accidentally.
Control: capture contract tests before extraction and keep external paths unchanged.

### Legacy travel behavior loss

Risk: moving RAG code or data may break `agent_mode=rag`.
Control: decide whether it remains supported, then either add a legacy smoke test or disable it explicitly instead of allowing silent failure.

### Benchmark comparability loss

Risk: changing runner logic while renaming files could make old and new results incomparable.
Control: separate file moves from metric/schema changes; record schema and dataset versions.

### Historical evidence corruption

Risk: adding metadata may accidentally rewrite old measurements.
Control: preserve original JSON as `results.json`, create a separate manifest, and compare checksums before and after migration.

### Asset path regressions

Risk: moving backgrounds or WAV files can break Vite development, FastAPI static mounting, or runtime audio URLs.
Control: inventory every URL and filesystem reference, then build and manually verify all frontend surfaces.

### Provider cost or rate-limit impact

Risk: validation could consume API quota or trigger provider limits.
Control: default to deterministic tests; require explicit authorization for live checks.

### Oversized migration commits

Risk: combining moves, renames, refactors, and behavior changes makes review and rollback difficult.
Control: use small commits organized by phase and keep pure moves separate from edits where possible.

## 16. Recommended Commit Sequence

The implementation should use reviewable commits similar to:

1. `test: lock restaurant domain and API contracts`
2. `docs: make Lantern the canonical repository identity`
3. `chore: add benchmark run manifest infrastructure`
4. `refactor: classify evaluation runners by product and purpose`
5. `chore: migrate historical reports into immutable run folders`
6. `refactor: separate restaurant and legacy travel data`
7. `refactor: extract FastAPI route modules`
8. `refactor: separate voice providers and runtime controllers`
9. `chore: deduplicate frontend assets and split styles`
10. `ci: add keyless validation workflow`
11. `docs: finalize architecture and migration record`

Do not combine all phases into one commit.

## 17. Approval Gates

Explicit approval should be requested before:

- choosing between archival and continued execution of travel mode
- deleting any travel code, dataset, report, EPS source, or duplicate asset
- changing public API or WebSocket contracts
- running live provider benchmarks
- changing benchmark thresholds
- pushing, merging, or deploying the reorganization

## 18. Definition of Done

The reorganization is complete only when all of the following are true:

- The root README, backend identity, frontend identity, and default settings agree on The Lantern.
- Active restaurant and legacy travel responsibilities are visibly separated.
- Evaluation runners are separated by purpose and product.
- Each new benchmark run is immutable and contains a manifest, results, and summary.
- Historical results remain intact and are clearly marked as migrated evidence.
- Machine-specific report links are gone.
- `turns.jsonl` and `spans.jsonl` ambiguity is resolved.
- Duplicate runtime images are removed only after reference verification.
- Deterministic backend tests and frontend builds pass.
- Live checks, if authorized, pass without overwriting historical results.
- Documentation matches the implemented structure.
- Git is clean and every migration phase is reviewable independently.

## 19. Immediate Next Step

Implementation decision recorded:

> **The Lantern is the canonical product. The Da Nang travel implementation will be preserved under an explicit legacy namespace until a separate deletion or extraction decision is approved.**

The Lantern is the canonical product. The Da Nang travel implementation is retained under the explicit `legacy/travel/` namespace until a separate deletion or extraction decision is approved. The implementation began with deterministic domain and route contract tests, then moved paths with compatibility boundaries intact.
