# E0 calibration

Status: **pipeline built and tested end to end against the mock upstream. No E0 data has been collected.** The plan forbids E0 collection until the encrypted-traffic validation sessions are `VALID` on the Linux lab (IMPLEMENTATION_PLAN §30).

## What exists

| Piece | Module | Notes |
|---|---|---|
| Corpus v1 | `workloads/corpus/v1/corpus.yaml`, `crosstalk.workloads.corpus` | 6 classes × 40 families × 5 shared paraphrase templates = 1,200 prompts, plus 12 determinism prompts. Templates are shared by every class, so template wording carries no class signal. Banned-pattern check (email, phone, long numbers, URLs, non-ASCII or JSON-escaped characters). Content hash covers rows, tokenizer and revision |
| Regimes | `crosstalk.workloads.planner` | NAT; MATCH-P draws every class from the same sequence of (32-byte padded size, 8-token bucket) cells, so request-body sizes are identical per class (test-enforced); MATCH-PR adds `min_tokens = max_completion_tokens = L` with one L sequence shared by all classes |
| Session plan | same | Block randomization: every block holds each (class × arm) once, shuffled; all arms of a class share the block's prompt. Determinism probe every 25th slot, warmup slots first. Draws cover every family once before any repeats. `plan_sha256` excludes request IDs |
| Collect | `crosstalk.loadgen.session` | Writes `plan.parquet`, `labels/<id>/labels.parquet` (no text), per-slot `x-ct-arm`, `cache_salt = request_id`. MATCH-PR length forcing is checked against `usage.completion_tokens` and recorded as `validation.length_forcing`; a miss makes the session `INVALID` |
| Features | `crosstalk.features` | R1 summary (87), R2 CUMUL (104), T-len (5). Loaded only through `crosstalk.traces.observer`; incomplete flows are excluded and counted |
| Attacks | `crosstalk.attacks` | Chance, majority, logistic regression (C tuned on inner family folds), LightGBM (leaves and rounds by inner-fold early stopping). Outer folds assigned once per family, stratified by class; the static/adaptive protocol is in place for E1 |
| Statistics | `crosstalk.stats` | Weighted metrics (match scikit-learn, property-tested), paired family-cluster bootstrap, null control by family-label permutation, ICC and drift for the noise floor, power check |
| Report | `crosstalk.evaluation.e0` | `artifacts/experiments/<name>/{report.md, summary.json, evaluations/, predictions/, features/, figures/}`. Refuses non-`VALID` or dirty-tree sessions unless `--allow-unvalidated`, which stamps the report NOT REPORTABLE |

Import-linter enforces that `features`, `attacks`, `stats` and `evaluation` never import the oracle loader, loadgen, shaper, workloads, capture or validation code. The end-to-end test also records every Parquet file the analysis opens and fails if any is under `oracle/`, `restricted/` or a plan.

## Laboratory defects found by the end-to-end test

Both were latent in the capture and shaper code. The only earlier capture never reached them because its emission log was empty.

1. **Response phase lost when Finished and the request share a timestamp.** `classify_records` found the first request record by requiring `end_ns` strictly later than the client Finished record's. When both complete in the same segment, no record was labelled `response`. It now uses stream order. Regression tests are in `tests/test_reassembly.py`.
2. **Header record missing from the emission log.** The shaper writes the HTTP response headers as their own `write()`, so the observer sees one extra 141-byte record per response that had no emission. Emission fidelity would have failed on every session. The shaper now logs it as an emission of kind `headers` (sequence 0).

## Deviations from the plan

- Features use NumPy rather than Polars expressions: they operate on per-request arrays of a few hundred records, and NumPy avoids another dependency. They are still pure functions with golden-vector tests.
- `labels` gains `role` (main/determinism/warmup) and `arm`; `predictions` and `evaluations` gain `regime`, `task` and `representation`.
- The learning curve skips a fraction when too few training families remain for the inner folds, and says so in the report (only small pilots hit this).
- Null control and learning curve use one model seed; the main results use `analysis.seeds` (default 5).

## How to run it on the lab

```bash
uv sync --locked --all-extras
uv run crosstalk workloads build --tokenizer Qwen/Qwen2.5-1.5B-Instruct --revision <commit-sha> \
  --output artifacts/workloads/v1
docker compose -f deploy/compose/lab.yml --profile gpu up -d --build --wait
uv run crosstalk lab provenance
docker compose -f deploy/compose/lab.yml exec loadgen \
  crosstalk session run /app/experiments/e0-pilot.yaml --root /artifacts
# per session: reconstruct run, reconstruct validate (as for the validation sessions)
uv run crosstalk e0 run experiments/e0-pilot.yaml --root artifacts
```

Then the same with `experiments/e0.yaml` (NAT, MATCH-P, MATCH-PR at 200 per class, about 3.5 GPU-hours).

## Pending

| Item | Needs |
|---|---|
| E0 pilot (60/class) and the three development regimes | Validation sessions `VALID` on Linux; local vLLM smoke test |
| Power-check decision for E1/E2 | The pilot |
| Real tokenizer counts and the MATCH-P pool size | Building the corpus with the pinned Qwen tokenizer. With whitespace counting, 138 of 200 prompts per class fall into cells shared by all classes |
