# Benchmarking and Smoke-Test Operations

## Deterministic checks

Run the committed offline contract suite before changing domain state, routes, report paths, or import boundaries:

```bash
python -m unittest discover -s tests -v
```

Build the browser application independently:

```bash
cd frontend
npm ci
npm run build
```

These checks do not call AssemblyAI, Gemini, Ollama, or Cartesia.

## Optional live suites

Restaurant benchmarks live under `eval/benchmarks/restaurant/`; preserved travel suites live under `eval/benchmarks/travel/`. Their use can consume provider quota, so run them only with the required credentials and a deliberate approval to incur that cost.

## Report contract

New benchmark code uses `eval.reporting.create_run`. The helper creates a unique directory and refuses to overwrite an existing run:

```text
reports/<product>/<suite>/<UTC-run-id>/
├── manifest.json
├── results.json
└── summary.md
```

Use an explicit run ID only when reproducible job identifiers are needed. Historical runs use `historical-*` only where the original execution timestamp or configuration could not be established from the retained artifact. Do not overwrite historical results to make them conform to new schemas.
