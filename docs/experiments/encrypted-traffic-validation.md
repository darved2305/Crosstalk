# Encrypted traffic validation

The validation plan is `experiments/validation.yaml`: 200 passthrough requests at concurrency 1 and another 200 at concurrency 16. The local container execution has not occurred because the Docker daemon was unavailable on 2026-09-30. No session is marked valid from this document.

## Evidence available

The local black-box test `tests/test_integration.py` passed over a real TLS 1.3 connection to the shaper and mock upstream. It checked the exact client-visible content, usage event count, socket port capture, and observable TLS record sizes. The deterministic pcap and pcapng parser tests, TCP reassembly tests, and TLS parser tests passed. The exploratory Windows runtime probe observed an exact 52-emission ciphertext record-length sequence in each of nine repetitions spanning spaced, back-to-back, and delayed-reader cases. This is a loopback runtime check, not a captured edge-network session.

## Pending measurements

| Question | Status and required execution |
|---|---|
| Application emission to captured TLS record | Pending a Docker bridge capture and the 200-request validation sessions. The Windows MemoryBIO observation does not replace pcap reconstruction. |
| TLS record to packet coalescing and segmentation | Pending dumpcap PCAPNG from the non-loopback edge interface. |
| Parser versus tshark agreement | Pending a completed validation capture and installed tshark in the capture container. |
| Offload configuration and MTU | Pending ethtool control and capture metadata from the Linux container namespace. |
| Capture completeness | Pending dumpcap and pcapng drop counters from a completed session. |
| Impaired network recovery | Pending Linux `tc netem` with 1% loss and 5% reordering and a complete captured flow corpus. |
| Local vLLM smoke | Pending Docker Engine, NVIDIA GPU access, and a resolvable pinned image and model revision. |

The validation commands are `docker compose -f deploy/compose/lab.yml --profile mock up -d --wait`, `docker compose -f deploy/compose/lab.yml exec loadgen crosstalk session run /app/experiments/validation.yaml --root /artifacts`, and, per session, `crosstalk reconstruct run <session-id>` followed by `crosstalk reconstruct validate <session-id>`. These commands have not been executed in the container laboratory.
