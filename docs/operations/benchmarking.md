# Benchmarking

Run deterministic checks with `python -m unittest discover -s tests -v`, `python -m compileall -q backend legacy eval tools`, and `cd frontend; npm ci; npm run build`. Live benchmarking is opt-in because AssemblyAI, Ollama, and Kokoro require credentials or local model weights. New multilingual cases live in `eval/datasets/restaurant/multilingual_orders.v1.json`; historical V1 reports remain immutable under `reports/`.
