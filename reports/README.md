# Benchmark and Audit Artifacts

This directory stores curated benchmark evidence and authored performance audits.

## Generated benchmark runs

New results must be written below:

```text
reports/<product>/<suite>/<UTC-run-id>/
├── manifest.json
├── results.json
└── summary.md
```

`manifest.json` records the schema version, product, suite, run ID, Git commit, dirty state, and run metadata. Results must not overwrite an existing run directory.

## Historical artifacts

Migrated results are kept under `reports/restaurant/` or `reports/legacy/travel/`. A historical run ID intentionally uses `historical-*` when the original execution time or configuration was not available. Historical values were moved without being regenerated.

## Runtime logs

Runtime JSONL telemetry belongs in the ignored `var/metrics/` directory. It is not a curated benchmark result.
