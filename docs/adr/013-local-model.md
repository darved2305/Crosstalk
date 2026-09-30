# ADR 013: Local serving model

Status: accepted, GPU smoke test pending.

The local GPU path uses vLLM 0.29.0 with `Qwen/Qwen2.5-1.5B-Instruct`, stream interval 1, at most 64 sequences, seed 0, GPU memory utilization 0.85, and a 4096-token model limit. The image digest and model revision must be pinned when the image and model can be fetched. Docker's daemon is unavailable in the current Windows session; the mock upstream is used for local protocol tests without asserting a vLLM result.
