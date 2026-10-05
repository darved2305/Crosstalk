# Crosstalk

**Measuring privacy leakage from encrypted LLM streaming traffic, and evaluating deployable traffic-shaping defenses in the real serving path.**

> In telephony, *crosstalk* is a signal coupling into a channel that was meant to be isolated. Here the encrypted content of an LLM response couples into the size-and-timing channel that TLS does not hide.

**Status:** research and architecture complete (v2.0 docs, 2026-09-19). The Python measurement instrument and the E0 attack pipeline (corpus, planner, features, attackers, statistics, report) are implemented and tested end to end against the mock upstream; the Linux capture laboratory remains unvalidated, so no E0 data has been collected. See [`docs/experiments/e0-calibration.md`](docs/experiments/e0-calibration.md).

## The question

> How effective are deployable traffic-shaping defenses against encrypted LLM streaming side channels when evaluated on the wire against an attacker retrained on defended traffic, and how does the privacy–performance trade-off change under realistic inference-serving load?

Published work shows that a passive network observer can infer the topic of an encrypted LLM conversation from TLS record sizes and timing, and sometimes reconstruct text. Vendors ship mitigations (OpenAI/Azure `obfuscation`, Cloudflare/Mistral `p`, multi-token chunking), and researchers have evaluated several defenses:

- some implemented in reference services with measured overheads;
- some deployed by providers;
- some simulated on captured traces.

What we did not find is an evaluation that deploys several defense families in front of a real serving engine, validates what reaches the wire, reports static and defense-retrained attackers as a pair, and varies serving load. `docs/RESEARCH.md` has the evidence and the conservative gap statement.

## What Crosstalk builds

| Piece | What it is |
|---|---|
| **Shaper** | Python 3.13 + uvloop TLS-terminating SSE proxy in front of vLLM, with five defense arms: passthrough, bucket padding, window batching, constant-rate, hybrid. Padding and cover traffic use SSE comment lines, which the official OpenAI SDKs ignore |
| **Observer** | dumpcap capture → own pcapng/TCP-reassembly/TLS-record parser, validated record-for-record against tshark, with Linux offloads (TSO/GSO/GRO) controlled and recorded |
| **Evaluation** | Logistic regression + LightGBM attackers under prompt-family-disjoint nested CV, naturalistic vs length-matched workloads, static vs adaptive attackers on identical held-out families, paired cluster-bootstrap CIs, null-label controls |
| **Serving load** | Concurrency and vLLM stream interval as experimental factors, with vLLM queue metrics joined to traces |
| **Platform** | FastAPI + PostgreSQL job queue, Next.js console (four research screens), Terraform on GCP Compute Engine (L4 GPU), GitHub Actions with security scanning |

A key design rule: **a socket write is not a TLS record, and a record is not a packet.** Crosstalk stores three observation layers (application emission, TLS record, network packet) and measures the mapping between them rather than assuming it. See `docs/RESEARCH.md` §11.3 for the first measurements.

## Documents

| Document | Role |
|---|---|
| [`docs/PRODUCT.md`](docs/PRODUCT.md) | Research and product authority: question, threat and observation model, contributions, experiments, defenses, metrics, risks, limitations |
| [`docs/RESEARCH.md`](docs/RESEARCH.md) | Evidence authority: verified literature, industry deployments, transport/runtime facts, Day-0 measurements, novelty matrix, rejected claims, references |
| [`docs/IMPLEMENTATION_PLAN.md`](docs/IMPLEMENTATION_PLAN.md) | Engineering authority: stack, language decision table, ADRs, schemas, algorithms, quality gates, and the ten-day plan |

## Local instrument

Install dependencies with `uv sync --locked`, then run `make check`. Run `uv run crosstalk lab certs` once to create a local TLS certificate and the ignored artifact directories. On Linux with Docker Engine running, `docker compose -f deploy/compose/lab.yml --profile mock up -d --build --wait` starts the bridge laboratory. Then run `uv run crosstalk lab provenance` on the host to record the source commit, dirty state, running image IDs, and tool versions for the load generator. Refresh provenance after rebuilding or changing the source tree. Set `CT_UID` and `CT_GID` to the host account IDs if they differ from 1000 so the non-root shaper and load generator can write to the artifact mount.

The validation plan is [`experiments/validation.yaml`](experiments/validation.yaml). Run `docker compose -f deploy/compose/lab.yml exec loadgen crosstalk session run /app/experiments/validation.yaml --root /artifacts` and note both session IDs. For each ID, run `docker compose -f deploy/compose/lab.yml exec capture crosstalk reconstruct run <session-id> --root /artifacts` followed by `docker compose -f deploy/compose/lab.yml exec capture crosstalk reconstruct validate <session-id> --root /artifacts`. The capture image contains the independent `tshark` oracle. The report command is `uv run crosstalk report waterfall <session-id> <request-id> --root artifacts --output artifacts/waterfall.png`. Measured status and unresolved host requirements are in [`bench/RESULTS.md`](bench/RESULTS.md) and [`docs/experiments/encrypted-traffic-validation.md`](docs/experiments/encrypted-traffic-validation.md).

## Ethics

Only Crosstalk's own infrastructure, models and synthetic prompts are measured. No third-party traffic is captured and no real personal data is used. The attack code exists to evaluate defenses.
