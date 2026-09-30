# Encrypted traffic validation

The validation plan is `experiments/validation.yaml`: 200 passthrough requests at concurrency 1 and another 200 at concurrency 16. Two diagnostic two-request mock sessions ran in Docker Desktop on 2026-09-30. Both were collected from a dirty source tree and are not reportable. The full 200-request sessions have not run. Docker Desktop later returned 502 and 500 daemon errors while recreating the updated containers, so no session is marked valid from this document.

## Evidence available

The local black-box test `tests/test_integration.py` passed over a real TLS 1.3 connection to the shaper and mock upstream. It checked the exact client-visible content, usage event count, socket port capture, and observable TLS record sizes. The deterministic pcap and pcapng parser tests, TCP reassembly tests, and TLS parser tests passed. The exploratory Windows runtime probe observed an exact 52-emission ciphertext record-length sequence in each of nine repetitions spanning spaced, back-to-back, and delayed-reader cases. This is a loopback runtime check, not a captured edge-network session.

The second diagnostic capture, session `01M3SC1CJ3BM1QET5YTV11831E`, used the non-loopback `eth0` edge interface at MTU 1500 with GRO, LRO, TSO and GSO reported off. Dumpcap reported 101 captured packets and zero pcap, dumpcap, flush and interface drops. The custom parser reconstructed 77 TLS records and the independent tshark comparison agreed on all 77 lengths, types and completion-frame times. One of two TCP flows was incomplete in the capture. The emission log was empty because a restarted shaper tried to create a previously used fixed filename; emission fidelity was consequently 0 of 56 compared records. These failures led to unique emission-log filenames, explicit writer startup checks, and an interface-quiescence wait before capture stop. Those repairs still need a fresh container run.

## Pending measurements

| Question | Status and required execution |
|---|---|
| Application emission to captured TLS record | The diagnostic comparison failed because its emission log was empty; a fresh session is required after the writer repair. The Windows MemoryBIO observation does not replace pcap reconstruction. |
| TLS record to packet coalescing and segmentation | A diagnostic PCAPNG exists, but its incomplete second flow prevents a reportable estimate. |
| Parser versus tshark agreement | Exact for 77 of 77 records in one diagnostic capture; the 200-request validation corpus remains pending. |
| Offload configuration and MTU | Diagnostic capture reported MTU 1500 and GRO/LRO/TSO/GSO off on `eth0`; repeat in the final validation session. |
| Capture completeness | Dumpcap reported zero drops in the diagnostic capture, but one of two TCP flows was incomplete. |
| Impaired network recovery | Pending Linux `tc netem` with 1% loss and 5% reordering and a complete captured flow corpus. |
| Local vLLM smoke | Pending Docker Engine, NVIDIA GPU access, and a resolvable pinned image and model revision. |

The full validation commands are `docker compose -f deploy/compose/lab.yml --profile mock up -d --build --wait`, `uv run crosstalk lab provenance` on the host, and `docker compose -f deploy/compose/lab.yml exec loadgen crosstalk session run /app/experiments/validation.yaml --root /artifacts`. For each session ID, run `docker compose -f deploy/compose/lab.yml exec capture crosstalk reconstruct run <session-id> --root /artifacts` and `docker compose -f deploy/compose/lab.yml exec capture crosstalk reconstruct validate <session-id> --root /artifacts`. The capture image includes `tshark` and mounts the same artifacts directory. The complete 200-request command has not yet been run.
