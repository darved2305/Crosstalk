# PRODUCT.md — Crosstalk

**Crosstalk: measuring privacy leakage from encrypted LLM streaming traffic and evaluating deployable traffic-shaping defenses in the real serving path.**

*The product and research authority.* Evidence and citations live in `RESEARCH.md` (referenced as `[R#]` and `RESEARCH §n`). Engineering execution lives in `IMPLEMENTATION_PLAN.md`.

Version 2.0 · 19 September 2026 · supersedes v1.0 (13 September 2026).

> **Name.** In telephony, *crosstalk* is a signal coupling into a channel that was meant to be isolated. Here the encrypted content channel couples into the size-and-timing channel that TLS does not hide.

---

## 1. Executive summary

TLS hides what an LLM says but not the shape of how it says it. A streamed response is a sequence of small, content-dependent TLS records emitted at the pace of the decoder. Published work shows that a passive network observer can infer topics, response language, private attributes and partial text from record sizes and timing (RESEARCH §2).

Vendors deployed mitigations: OpenAI/Azure `obfuscation`, Cloudflare and Mistral `p`, and incidental multi-token chunking (RESEARCH §5). Researchers evaluated several defenses. Some were implemented in reference services with measured overheads (Wiretapping LLMs), some were provider-deployed (Whisper Leak's padding measurement), and some were simulated on captured traces (Whisper Leak's batching and injection, NetEcho's dummies). Every LLM study we found either ran at batch size 1 / sequentially or measured uncontrolled commercial load (RESEARCH §4).

**Crosstalk is an experimental system that answers one question well:**

> **How effective are deployable traffic-shaping defenses against encrypted LLM streaming side channels, when they run in the real serving path, are measured on the wire, and are attacked by adversaries retrained on defended traffic? And how does the privacy–performance trade-off change under realistic inference-serving load?**

It consists of:

- A Python streaming **shaper** that terminates the client's TLS in front of a real serving engine (vLLM) and applies one of five defense arms per request.
- A **capture and reconstruction pipeline** that records what an on-path observer sees (dumpcap → PCAPNG → TCP reassembly → TLS records), validated independently against tshark.
- An **evaluation harness** that trains attackers on undefended and on defended traffic, reports them as a pair with grouped statistics, and measures cost (TTFT, inter-token latency, bandwidth, throughput, CPU) on the same runs.
- A **serving-load dimension**: concurrency and vLLM stream interval are experimental factors, not a fixed backdrop.

Crosstalk does not claim a new attack. It does not claim to be the first defense, the first cost measurement, or the first defense-aware attacker. It claims a **systematic, validated, load-aware evaluation** that we did not find in the literature (§9).

---

## 2. Crosstalk in plain words

An AI chatbot sends its answer word by word. Each piece is encrypted, so an eavesdropper cannot read it. But the eavesdropper still sees how big each encrypted piece is and when it arrives. Long words make slightly bigger pieces. Medical answers have a different rhythm from coding answers. With enough examples, a computer learns to guess the topic from the rhythm alone.

You can blur that rhythm: pad every piece to the same size, send pieces on a fixed clock, or bundle several words together. Blurring costs something: slower answers, more bandwidth, more server work. Crosstalk puts each blurring method in front of a real AI server, records the encrypted traffic exactly as an eavesdropper would, and lets a machine-learning "eavesdropper" try to guess topics. It first tries with an eavesdropper who has never seen the blurring, then with one who trained on blurred traffic. It also measures how much each method slows things down, and whether any of this changes when the server is busy.

---

## 3. Is it necessary? Five separate answers

| Lens | Verdict | Reasoning |
|---|---|---|
| **Research platform** | **Strong** | The serving-load dimension and a validated, deployed, static-vs-adaptive comparison are open (RESEARCH §4, §13). The questions are well posed, measurable in about ten days, and publishable whatever the sign of the result. A null result ("batching under load already hides X", or "defense Y collapses against a retrained attacker") is informative. |
| **Open-source engineering artifact** | **Moderate** | A validated pcapng → TCP → TLS-record reconstruction pipeline and an OpenAI-compatible shaping proxy with verified client compatibility would be reusable by researchers. Adoption as a production gateway is uncertain: operators would rather have the policy inside an existing gateway, and the largest vendors already pad upstream. |
| **Internal privacy/performance evaluation tool** | **Moderate–strong** | Teams that self-host LLMs, or re-stream an upstream API, inherit no vendor mitigation (NetEcho scenario E). They need exactly this measurement before enabling padding or batching. The method transfers even if the code is not adopted. |
| **Commercial standalone product** | **Weak** | Mitigations ship free inside provider APIs and Cloudflare AI Gateway. The buyer is unclear, and a shaping policy is a feature of a gateway, not a product. We state this plainly and do not position Crosstalk commercially. |
| **Résumé project** | **Strong, conditional on rigor** | It exercises Linux networking, TLS, packet capture, async I/O and backpressure, inference serving, statistics, cloud deployment and IaC. That value depends on the measurements being correct, which is why validation dominates the plan. |

---

## 4. Research question, sub-questions and hypotheses

### 4.1 Research question (final)

> How effective are deployable traffic-shaping defenses against encrypted LLM streaming side channels when evaluated on the wire against an attacker retrained on defended traffic, and how does the privacy–performance trade-off change under realistic inference-serving load?

### 4.2 Sub-questions

| ID | Sub-question | Experiment |
|---|---|---|
| RQ0 | Does our pipeline reproduce known leakage (topic inference from TLS record sizes and timing) on a self-hosted vLLM stack, and how much of it survives length matching? | E0 |
| RQ1 | For each defense arm deployed in the path: what leakage remains for a static attacker and for an adaptive attacker, relative to the paired undefended control, and at what cost in TTFT, inter-token latency, bandwidth, throughput and CPU? | E1 |
| RQ2 | Do leakage, defense effectiveness and defense cost change with serving load (concurrency, continuous batching) and with server-side chunk aggregation (vLLM stream interval)? | E2 |

### 4.3 Hypotheses (PROPOSED, to be tested; none is a result)

| ID | Hypothesis | Why we expect it | What would falsify it |
|---|---|---|---|
| H1 | Undefended leakage is far above chance in the naturalistic regime and drops, but stays above chance, in the length-matched regime | Size and timing sequences carry content beyond totals ([R1]–[R4]) | Matched-regime attack indistinguishable from chance |
| H2 | Size-only padding (D1) retains substantial leakage against an adaptive attacker | Timing and count channels survive ([R2] Table 3, [R4] Trace A, [R6] TokenPadding) | ρ_adaptive(D1) near 0 |
| H3 | Adaptive attackers recover a non-trivial fraction of the leakage that static attackers lose | WF history (RESEARCH §6.4) | ρ_adaptive ≈ ρ_static for all arms |
| H4 | Serving load changes timing distributions enough to shift both leakage and defense cost. The latency cost of constant-rate shaping depends on generation speed under load | Continuous batching couples streams ([R60], [R61]); constant-rate cost depends on generation vs emission rate ([R5]) | No significant concurrency × arm interaction |
| H5 | Server-side aggregation (stream interval) and gateway window batching reach different leakage/cost points at matched mean aggregation | Different placement and timing semantics | No difference in paired comparison |

**Results section: none yet.** Measured results enter this document only after the Day-10 campaign, each tied to a run manifest.

---

## 5. Threat model

### 5.1 The observer

**Position.** On path between the client and the TLS endpoint the client talks to: client LAN/Wi-Fi, access ISP, transit, or a cloud network position with visibility of that leg.

**Capabilities (in scope):**
- Full packet capture of the victim connection, including TCP headers and encrypted payload bytes. That allows TCP reassembly and TLS record-header parsing (RESEARCH M4).
- Connection metadata: IPs, ports, SNI, ALPN, handshake shape.
- **Profiling:** the observer can query the same service with its own prompts to build labelled training data. This is the standard assumption in [R1]–[R6].
- **Kerckhoffs:** the observer knows which defense is deployed and can collect defended traffic for its own prompts. This is the adaptive attacker.
- In the P0 baseline (HTTP/1.1, one request per connection), the observer can separate requests by connection. This is generous to the attacker and deliberate: it gives a clean upper-bound baseline. The H2-multiplexed arm (P2) removes it.

**Not in scope:** decrypting, downgrading or MITM of TLS; active packet delay, drop or injection; compromise of client or server; co-located microarchitectural channels; identifying individual persons; traffic correlation across networks.

### 5.2 Ethical boundary

Only Crosstalk's own infrastructure, models and synthetic workloads are measured. No third-party traffic is captured. No real personal data appears in any prompt. Commercial APIs are not attacked. The attack code is a measuring instrument for defenses and is documented as such.

---

## 6. Observation model

### 6.1 Three layers, never interchangeable

| Layer | Definition | Source of truth | Who may read it |
|---|---|---|---|
| **ApplicationEmission** | What the shaper decided to send: time of `write()`, plaintext bytes, kind (content / merged / padded / dummy), queueing delay | Shaper emission log (oracle) | Validation and cost analysis only; **never** attack features |
| **TLSRecord** | Records actually produced, reconstructed from the TCP byte stream: direction, start and end timestamps, content type, length | Capture → reassembly → record parser, validated against tshark | Attack features (primary) |
| **NetworkPacket** | Packets seen at the capture point: timestamp, direction, IP length, TCP payload length, flags, retransmission flag | Capture | Attack features (secondary, valid only with offload state recorded) |

The mapping between layers is measured, not assumed (RESEARCH §1.3):
- **Emission → record fidelity:** fraction of emissions that became exactly one record of the predicted length (`plaintext + 17`).
- **Record → packet structure:** records per packet and packets per record, per defense and load.

A defense is judged only on the TLSRecord and NetworkPacket layers, because those are what the observer sees.

### 6.2 Vantages

| Vantage | Where | Status | Purpose |
|---|---|---|---|
| **V-C (client-side)** | Capture in the client's network namespace or host, offloads recorded (GRO/LRO off in the lab) | **Primary baseline** | Equivalent to a LAN/ISP observer of the client connection |
| **V-S (server-side)** | Capture on the shaper host's client-facing interface, TSO/GSO off | Secondary (Day 8 cloud) | Validates that the defense's on-wire effect exists at egress. Measures packetization differences |
| **V-M (cloud mirror)** | GCP NSI/Packet Mirroring of the shaper VM | P3, optional | Cloud-operator vantage; bandwidth and cost overhead |

**What is invariant between V-C and V-S** (no TLS-terminating middlebox in between): the TCP byte stream of each direction, and therefore the TLS record sequence (lengths, order, content types).

**What may differ:** packet boundaries (segmentation, coalescing, GRO at the receiver, MSS clamping), timestamps (propagation, queueing, jitter), visibility of loss and retransmission, and reordering.

With an L4 passthrough NLB in front of the shaper, responses use **direct server return** and never pass through the load balancer ([R86]). "Cloud-side observation" therefore means the backend VM's NIC, not the LB. **V-C and V-S are not treated as equal.** Their agreement is measured on the record layer and their differences are characterized on the packet layer.

---

## 7. Transport model and protocol arms

| Arm | Priority | Definition | Why |
|---|---|---|---|
| **H1-TLS13-1req** | **P0 baseline** | HTTP/1.1, TLS 1.3, ALPN `http/1.1`, one request per connection, `TCP_NODELAY`, no resumption, no 0-RTT, shaper `num_tickets = 0` (recorded) | Clean, interpretable channel with no multiplexing, no session-ticket records confounding sizes, and attribution by connection |
| H1-keepalive | P1 | Several sequential requests per connection | Realistic SDK connection reuse; the observer must segment by timing |
| H1-tickets | P1 | Default TLS 1.3 session tickets | Realism check on the NST confound |
| **H2-controlled** | P2 | HTTP/2 with one active stream per connection | Isolates H2 framing effects (frame headers, WINDOW_UPDATE, PADDING availability) |
| **H2-multiplexed** | P2 | Several concurrent streams per connection | **Session-level** attack only. Internal request IDs are an oracle and must not assign records to requests |
| H3/QUIC | P3 (out of 10-day scope) | QUIC datagrams | No record layer; separate packet-level feature pipeline (RESEARCH §7.5) |

---

## 8. Literature positioning (summary; full evidence in RESEARCH §2–§6)

- **Attacks exist and are strong:** Weiss (USENIX '24), Whisper Leak (preprint), PromptEcho (USENIX '26), NetEcho (preprint), Carlini & Nasr (preprint), Wiretapping LLMs (preprint), Time Will Tell (preprint), Spec Spills (preprint). Crosstalk reproduces one calibration point (E0) and claims nothing about attacks.
- **Defenses have been evaluated, but in isolation:**
  - Wiretapping LLMs measured bandwidth and interactivity overheads for five streaming designs in a reference service that processed prompts sequentially.
  - Whisper Leak measured real provider padding and simulated batching and injection. Its classifiers were trained on mitigated data, but no static/adaptive pair is reported.
  - NetEcho deployed padding in its own vLLM and simulated dummies.
  - Carlini & Nasr derived a constant-rate bandwidth/latency curve analytically.
- **Industry ships upstream, size-focused mitigations** and gateway padding (Cloudflare). Downstream re-streaming apps inherit nothing.
- **The WF literature supplies the methodology:** retrained attackers break lightweight defenses; coarse features defeat fine-grained padding; simulated overheads are unreliable; application-layer defenses do not control on-wire timing (HotNets '25); a DP shaping middlebox with measured overheads exists (NetShaper, USENIX '24).

## 9. The gap Crosstalk occupies (conservative)

> We found no prior work that evaluates several deployable defense families in the real serving path with independently validated wire observation, reports static and defense-aware retrained attackers as a pair under grouped statistics, jointly measures latency, bandwidth, throughput and CPU, and varies serving load and batching as experimental factors. The serving-load dimension is the least covered.

Wording rules for every write-up: never say "first". Say "we found no prior work that…" or "existing work does X; Crosstalk evaluates Y". Re-check the literature before any submission (RESEARCH §13).

## 10. Contributions

| # | Contribution | Classification (RESEARCH §12) |
|---|---|---|
| **A** | **Real deployed defenses with real wire observation.** An open shaper at the operator's last hop with five arms, evaluated only on reconstructed TLS records and packets. The emission → record → packet mapping is validated independently, including a Whisper-Leak-style simulated-vs-deployed comparison (P1) | Engineering + methodological |
| **B** | **Adaptive attacker as the headline.** For each arm, a static attacker (trained on D0) and an adaptive attacker (trained on that arm's defended traffic from training families only), evaluated on identical held-out prompt families, with paired cluster-bootstrap CIs and a retained-leakage ratio | Methodological |
| **C** | **Serving conditions as a variable.** Concurrency / continuous batching and vLLM stream interval as factors for leakage **and** for defense cost and effectiveness | Possible research novelty (strength depends on the measured effect) |
| **D** | **Length-matched controls.** Naturalistic vs prompt-matched vs response-length-forced regimes, reporting how much leakage is "just length" | Methodological |
| **E** | **Reproducible artifact.** Versioned workloads, run manifests with hashes and provenance, raw PCAPNG for validation, and a documented pipeline | Engineering |

---

## 11. Scope

### 11.1 Priorities

| Priority | Contents |
|---|---|
| **P0 (must exist by Day 10)** | H1-TLS13-1req baseline · local lab with real vLLM · shaper with D0–D4 · client-side capture + reconstruction + tshark validation · three-layer validation report · workloads (naturalistic + matched) · LR + LightGBM attackers · grouped nested CV · static/adaptive pairs · paired cluster bootstrap · null control · noise floor · E0, E1, E2 · orchestration via API + Postgres · CI with security scanning · one GCE GPU deployment with a mini-campaign · four UI screens |
| **P1** | D1r random padding (industry reference) · simulated-vs-deployed comparison · H1-keepalive and tickets arms · V-S vantage in the cloud · packet-layer features · MI lower bound from confusion matrices · L4 passthrough NLB in front of the shaper · full E1/E2 campaigns with the 7B model on L4 (P0 includes only a mini-campaign) |
| **P2** | H2 arms · E3 application type (matched regime only) · E4 prefix-cache timing under a passive vantage · E7 WAN impairment (netem) · GKE deployment as an ops demonstration · 1-D CNN attacker · kNN MI estimators |
| **P3** | H3/QUIC · E5 backend/config fingerprinting · E8 L4 vs managed L7 · policy optimisation (NSGA-II) · Maybenot-style machines · packet mirroring vantage |

**Gate rule:** no P2/P3 work starts until every P0 acceptance criterion in IMPLEMENTATION_PLAN §29 passes.

### 11.2 Non-goals

New attack techniques; verbatim reconstruction; attacks on third-party services or users; a "privacy score"; AI-generated insights or a chatbot UI; TEEs, confidential computing or MPC (orthogonal: a TEE does not change how many bytes leave the machine or when); Tor/mixnets; a service mesh in the measured path; Kafka, Redis, Pub/Sub, BigQuery or Temporal without a measured need; multi-cloud for its own sake.

---

## 12. Experiment hierarchy

| ID | Role | Question | Design summary |
|---|---|---|---|
| **E0** | FLAGSHIP (calibration) | Does the pipeline reproduce known leakage, and how much survives matching? | D0 only; naturalistic, MATCH-P and MATCH-PR regimes; T1 (6-topic) and T2 (sensitive vs background, base-rate analysis); null control; determinism probe; learning curve. **Reproduction, not novelty.** |
| **E1** | **FLAGSHIP (main contribution)** | Effectiveness and cost of D1–D4 vs D0 | All arms randomized per request within each session (same prompts, same period); static + adaptive per arm; costs from the same runs; H1-TLS13-1req; concurrency 1 |
| **E2** | **FLAGSHIP** | Does a defense that looks effective at concurrency 1 stay effective under production-like serving? | Concurrency ∈ {1, 8, 32} locally ({1, 16, 64} on L4 if run), in **time-alternating blocks within each session** (so load is not confounded with drift), two replicate sessions; vLLM stream interval ∈ {1, 4} for D0 and D2; all arms interleaved per block; vLLM queue/batch metrics joined |
| E3 | Supporting (P2) | Application-type fingerprinting | Only in the matched regime, since naturalistic app-type differences are dominated by length |
| E4 | Later (P2) | Prefix-cache hit visible to a passive observer under load? | Well-studied from the API-client seat and mitigated by `cache_salt`; load degrades it ([R17]) |
| E5 | Optional (P3) | Backend/config fingerprinting | Interesting, secondary |
| — | **Removed as flagship** | Autoscaling / cold-start fingerprinting | Kept only as a confounder and failure scenario |
| E7 | Later (P2) | WAN impairment | External validity (netem RTT/jitter/loss) |
| E8 | Later (P3) | L4 passthrough vs managed L7 LB | Needs the baseline first; tests whether an L7 edge re-frames streams |
| E9 | P3 | Policy optimisation | Only after substantial measured data |

---

## 13. Workloads and tasks

### 13.1 Tasks

| Task | Definition | Metric focus |
|---|---|---|
| **T1 topic** | 6 balanced classes (e.g. medical, legal, finance, programming, travel, cooking) | Balanced accuracy, macro-F1, macro PR-AUC |
| **T2 sensitive topic** | One sensitive class vs the rest, evaluated at explicit base rates (1:10, 1:100, 1:1000) | PR-AUC; precision at recall 0.05/0.10/0.25 re-weighted to the base rate |
| **T-len (diagnostic)** | Same labels, but the attacker sees only length/duration totals | Quantifies "leakage that is just length" |

### 13.2 Regimes (mandatory)

| Regime | Definition | Question it answers |
|---|---|---|
| **NAT (naturalistic)** | Prompts as authored; natural EOS; cap `max_completion_tokens` = 512 | Total practical metadata leakage |
| **MATCH-P** | Per class, identical distribution over prompt token buckets; prompt bytes padded to 32-byte buckets | Does leakage survive when request size and prefill time carry no class signal? |
| **MATCH-PR** | MATCH-P plus `min_tokens = max_completion_tokens = L`, with L ∈ {96, 160, 224} drawn independently of class | Does leakage survive when response length is identical by construction? |

If the attack collapses under matching, that is reported as a finding, not hidden.

### 13.3 Corpus rules

Synthetic, templated and versioned (`workload@vX.Y.Z` with a content hash). Each prompt has a class, prompt family (paraphrases share it), template ID, token and byte counts under the served tokenizer, and regime tags. No real PII (banned-pattern check). Every request carries a unique vLLM `cache_salt`, which disables cross-request prefix-cache reuse without changing the prompt, except in E4.

---

## 14. Defense hierarchy

### 14.1 Arms (P0)

| Arm | Name | Mechanism (per request) | Channel targeted | Expected cost |
|---|---|---|---|---|
| **D0** | Passthrough | Each upstream SSE event is one emission (one `write()`) | — (control) | ~0 |
| **D1** | Bucket padding | Pad every emission's record plaintext to the smallest bucket of a fixed ladder (default: a first bucket covering ≥ 99.9% of single-token events, then doubling). Padding is carried in SSE comment lines | Size | Bandwidth |
| **D2** | Window batching | Merge the content deltas that arrive within a fixed window W (default 100 ms, grid anchored at the first token) into one valid chunk | Timing + count (+ size granularity) | Inter-token latency ≤ W |
| **D3** | Constant-rate | From request arrival, emit one fixed-size record every T ms (default 50 ms, size S). Content is packed up to capacity, padded to S, or replaced by a comment-only dummy block when idle. After completion, dummies continue until the tick count reaches the next multiple of K (default 32) | Size + timing + count + TTFT + length (quantized) | Bandwidth + latency; load-dependent |
| **D4** | Hybrid | D2 window batching + D1 bucket padding of each merged emission | Size + timing (partial) | Between D1 and D3 |
| D1r | Random padding (P1) | Uniform random comment padding (industry-style) | Size (noisy) | Bandwidth |

Default parameters are fixed **before** E1 from the Day-3/4 pilots and recorded in the run manifest. A small parameter sweep (2–3 points per arm) for the frontier is P1.

**Later extensions (not P0):** bounded jitter, FRONT-style front-loaded cover traffic, distribution-aware "optimal" padding [R40], HTTP/2 DATA padding (transport-adjacent), Maybenot-style state machines.

### 14.2 Placement

The shaper is the **last operator-controlled component before the client-visible encrypted stream**: it terminates the client's TLS. Any TLS-terminating proxy placed in front of it would re-frame emissions, which is itself an experiment (E8), not a baseline. Server-side aggregation (vLLM `stream_interval`) is compared as a second placement in E2.

Gateway-side padding already exists (Cloudflare AI Gateway), and upstream obfuscation exists at OpenAI and Azure. Crosstalk differentiates through multiple families, open implementation, wire validation, adaptive attackers, load, and a measured frontier, not through "the first gateway defense".

### 14.3 Client compatibility

| Class | Definition | Arms |
|---|---|---|
| **Deployment-compatible** | Verified in CI against official openai-python and openai-node streaming clients, plus a WHATWG-conformant SSE parser: decoded content is byte-identical and no spurious chunks are yielded | D0, D1, D2, D3, D4 (padding in SSE comment lines; merged chunks are valid `chat.completion.chunk` objects; dummies are comment-only blocks) |
| **Experimental-only** | Works with Crosstalk's loadgen but would surface bogus chunks or errors in real SDKs | Any `data:` dummy event; any non-schema field change (RESEARCH §5.2) |

Hand-rolled client parsers that split on blank lines and JSON-parse every block are **not** covered by the compatibility claim.

---

## 15. Attacker model

| Setting | Trained on | Evaluated on | Role |
|---|---|---|---|
| **Static** | D0 traffic of training families | Arm-k traffic of held-out families | What a pre-existing attacker achieves |
| **Adaptive** | Arm-k traffic of training families | Arm-k traffic of the **same** held-out families | **Headline number** (Kerckhoffs) |
| Transfer (P1) | Arm-j | Arm-k | Re-profiling cost |

- **Representations:** R1 summary statistics and R2 CUMUL curve on the TLSRecord layer (primary). Packet-layer variants R1p/R2p are secondary. R3 raw sequence for a CNN is P2.
- **Model ladder:** A0 chance/majority → A1 logistic regression → A2 LightGBM. A CNN (A3) enters only if A2 saturates. The same tuning budget applies to static and adaptive.

---

## 16. Metrics

### 16.1 Privacy (primary)

Balanced accuracy and macro-F1 (T1). PR-AUC and precision at fixed recall under explicit base rates (T2). Log-loss.

**Retained leakage** ρ = (m_arm − m_chance) / (m_D0 − m_chance), computed for static and adaptive attackers with paired cluster-bootstrap CIs. The static-to-adaptive recovery ρ_adaptive − ρ_static gets its own CI.

**Exploratory only:** MI lower bound from the confusion matrix (P1); kNN MI/Bayes-error estimates (P2), because MI estimation from our sample sizes is unreliable (RESEARCH §9.5).

### 16.2 Performance and cost (from the same runs)

- TTFT as seen by the client, p50/p95/p99.
- Inter-token (inter-content) latency at the client, p50/p95/p99 (p99.9 where n supports it).
- End-to-end stream duration.
- Throughput (completed requests/s, content tokens/s) at fixed concurrency.
- Bandwidth: wire bytes per request (both directions), overhead vs D0 in %.
- Shaper CPU seconds per request and RSS.
- Timer lateness (emission time − scheduled time) per arm.
- vLLM queue time and running/waiting counts.

### 16.3 Measurement validity (gates, not results)

- Emission → record fidelity.
- Parser vs tshark agreement.
- Capture completeness: zero dropped packets and zero incomplete flows.
- Offload state recorded.
- Null control at chance.
- Noise floor from repeated prompts.

---

## 17. Architecture (overview; details in IMPLEMENTATION_PLAN)

```text
workload manifest ─► session planner ─► loadgen (client netns) ══TLS 1.3 / HTTP/1.1══► shaper (Python, uvloop) ──HTTP/1.1──► vLLM
                                            │                                          │  one policy per request (D0–D4)
                                  dumpcap (same netns, offloads off)                   └─► emission log (oracle: validation/cost only)
                                            │ pcapng (+ drop stats)
                                            ▼
                    reconstruct: pcapng → TCP reassembly → TLS records → packets/records/flows (Parquet)
                                            │            ▲ validated against tshark (+ keylog decryption in validation runs)
                                            ▼
            observer-view features ─► attackers (LR, LightGBM; static/adaptive) ─► paired cluster bootstrap ─► results
                                            ▲
                     labels (no plaintext) ─┘          plaintext prompts/responses: restricted store, never read by attacks

control: FastAPI + PostgreSQL job queue (Day 7) · console: React + TypeScript + Vite · cloud: GCE GPU VM + client VM via Terraform (Day 8)
```

- **Why this shape.** Every box exists for a named reason: the shaper is the treatment, capture is the observer, reconstruction produces the observer's view, and attacks and statistics produce the measurement. Durable orchestration, the API, the UI and the cloud deployment come after the science works locally.
- **Language and runtime.** Python 3.13 + uvloop for the shaper, loadgen, parser, ML and API; React + TypeScript + Vite for the UI; mature system tools (dumpcap, tshark, vLLM) as external components. No Rust, C, C++ or Spring Boot is written for Crosstalk. The shaper runtime is confirmed or overturned by the Day-1 gate (IMPLEMENTATION_PLAN §3). The Day-0 probe on a laptop (RESEARCH §11.3) found:
  - 1 `write()` = 1 TLS record in Python, including back-to-back writes; Node merges same-tick writes.
  - uvloop timer lateness p99 ≈ 1.6 ms at 64 streams.
  - A passthrough relay adds ≈ 0.9 ms p99 at 64 streams in Python and Node alike. At 256 streams it adds ≈ 4.3 ms in Python vs ≈ 8.4 ms in Node.
- **Storage.** Parquet + DuckDB for all experimental data. PostgreSQL only for orchestration state. GCS in the cloud. No BigQuery, Pub/Sub or Redis.
- **Cloud.** Compute Engine for the measurement baseline (fewest hidden layers, NIC offload control). GKE is P2, as an operations demonstration once the science is validated. GKE Standard would be preferred over Autopilot for node-level networking control (RESEARCH §14).

---

## 18. Risks

| # | Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| K1 | Leakage inflated by length or contamination | High | Fatal to credibility | Matched regimes; family-disjoint nested CV; null control; T-len diagnostic |
| K2 | Measurement artifacts (offloads, coalescing, capture drops) mistaken for defense effects | Medium | High | Three-layer validation; tshark agreement; offload assertions; drop counters gate every run |
| K3 | Small local model (6 GB GPU) behaves unlike production models | High | Medium | Relative comparisons are the claim; the Day-8 L4 mini-campaign with a 7B model checks direction of effects; limitation stated |
| K4 | Sample sizes too small to separate arms | Medium | Medium | Paired design; pilot-based power check on Day 3; learning curves; report CIs without over-claiming |
| K5 | Shaper runtime tail latency at high concurrency | Medium | Medium | Day-1 gate; multi-process `SO_REUSEPORT` scale-out; E2 capped at the validated load |
| K6 | Adaptive attacker too weak (under-tuned) and flatters defenses | Medium | High | Same tuning budget for both settings; strongest-of-ladder reported; learning curves |
| K7 | vLLM/driver incompatibility on the local GPU | Medium | Medium | Day-1 lab smoke test; fallback to the cloud L4 earlier; llama.cpp only for plumbing |
| K8 | Schedule slip | High | Medium | Each day lists droppable items; P0 is sized for ten days, P1+ is optional |
| K9 | Cloud cost or quota surprises | Low–medium | Medium | GCE only; stop-on-idle; budget alerts; the Day-8 campaign is small; price marked UNVERIFIED until confirmed |
| K10 | Over-claiming novelty | Medium | High | §9 wording rules; RESEARCH §12–§13 copied into any report |

## 19. Limitations (to be stated in any write-up)

1. **Scale:** a single-GPU serving stack with 1.5B (local) and ~7B (cloud) open models. Frontier-scale serving differs.
2. **Synthetic prompts:** absolute leakage numbers are indicative. Paired relative comparisons are the reliable output.
3. **Idealized observer:** lossless, per-request attribution in the P0 baseline. This favours the attacker, so defense evaluations err conservative.
4. **Closed-world T1:** T2 base-rate analysis mitigates, but it is not a true open world.
5. **Implementations are ours:** D1r resembles vendor random padding, but it is not OpenAI's or Cloudflare's code.
6. **Timing noise:** GPU nondeterminism and sampling are measured via the noise floor, not eliminated.
7. **Laptop-class lab:** WSL2/Docker timing adds noise. Validation gates, not assumptions, decide what is reportable.

## 20. Paper thesis (research question ≠ hypothesis ≠ result)

- **Thesis framing:** *Crosstalk evaluates streaming traffic defenses in the real serving path, retrains attackers on defended traffic, and measures how privacy–performance trade-offs change under serving load.*
- **Title direction:** "Shaping the Stream: A Load-Aware, Wire-Validated Evaluation of Traffic Defenses for Encrypted LLM Streaming".
- **Do not assert** "adaptive attackers recover most leakage", "batching under load is a free defense", or anything similar until E1/E2 show it with CIs.

## 21. Résumé description (claims to be updated with measured numbers only)

**One line:**
> Built **Crosstalk**, a wire-validated system for measuring privacy leakage in encrypted LLM streaming. Streaming defenses deployed in a Python TLS proxy in front of vLLM are attacked by static and defense-retrained classifiers, and costs (TTFT, inter-token latency, bandwidth, CPU) are measured under varying serving load.

**Detailed entry:**
> **Crosstalk — encrypted LLM traffic-analysis and defense evaluation** · Python (asyncio/uvloop), TLS 1.3, dumpcap/tshark, vLLM, LightGBM, DuckDB/Parquet, FastAPI, PostgreSQL, React/TypeScript/Vite, Terraform, GCP
> - Built a Python TLS-terminating SSE shaper with five defense policies (bucket padding, window batching, constant-rate, hybrid). Verified one-emission-one-record behaviour and client compatibility with the official OpenAI SDKs.
> - Built a PCAPNG → TCP-reassembly → TLS-record pipeline validated record-for-record against tshark, with Linux offload control (TSO/GSO/GRO) and capture-completeness gates.
> - Evaluated static vs defense-retrained attackers under prompt-family-disjoint nested CV with paired cluster-bootstrap CIs, naturalistic vs length-matched workloads, and concurrency sweeps on vLLM.

**Interview talking points:**
- Why a socket write is not a TLS record and a record is not a packet, with our own measurements (asyncio `writelines` → 3 records; Node same-tick merge; autocorking; TSO/GRO).
- How TCP reassembly + record parsing gives the observer's view, and why the PCAP is kept for independent validation.
- Why the headline is the adaptive attacker, and why CIs come from a family-level cluster bootstrap rather than overlapping intervals.
- Why Python + uvloop was chosen after measuring timer lateness and relay latency against Node, and when it would be overturned.
- Why the measurement baseline runs on Compute Engine, not Autopilot or GKE (hostNetwork, offloads, fewer layers). The Autopilot capabilities are nuanced (NET_RAW and NET_ADMIN can be enabled).

## 22. Demo story (five minutes)

1. **Trace Explorer:** one encrypted response, with the three layers aligned (emissions, records, packets). "I cannot read it, but I can see its shape."
2. **Attack Results (E0):** topic accuracy vs chance under naturalistic and matched regimes; null control at chance.
3. **Switch the arm to D3:** the waterfall becomes a metronome of equal records.
4. **Frontier:** static vs adaptive toggle. "This is what an attacker who trained on defended traffic gets, and here is what each defense cost in TTFT, inter-token latency and bandwidth."
5. **E2:** the same frontier at concurrency 32. "Does it hold under load?" The answer is whatever we measured, with CIs.

Every number shown is tied to a run manifest. Stored results are labelled as stored.

---

*End of PRODUCT.md. Evidence: `RESEARCH.md`. Engineering authority and the ten-day plan: `IMPLEMENTATION_PLAN.md`.*
