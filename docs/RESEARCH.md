# RESEARCH.md — Crosstalk

**The evidence authority.** Every factual claim that drives Crosstalk's architecture, scope or novelty is recorded here with its source, source type, date and a claim label. `PRODUCT.md` (research/product authority) and `IMPLEMENTATION_PLAN.md` (engineering authority) cite this file; neither may state a fact that is not supported here.

Version 2.0 · 19 September 2026 · supersedes v1.0 (13 September 2026).

---

## 0. How to use this document

### 0.1 Claim labels

| Label | Meaning |
|---|---|
| **VERIFIED** | Supported by a primary source read for this revision (paper text, RFC, official documentation, or source code at a named branch), or by our own measurement where the source column says `[OWN]`. |
| **REPRODUCTION** | A published result Crosstalk intends to reproduce on its own stack. Not a Crosstalk finding until our measurement exists. |
| **PROPOSED** | Something Crosstalk will build or test. Not a claim about the world. |
| **INFERENCE** | Our conclusion from verified evidence. Logically supported but not stated by any source. |
| **UNVERIFIED** | Plausible but not confirmed against a primary source (secondary sources only, or not yet measured). Must not be used as a premise for an architecture decision. |
| **REJECTED** | A claim from v1.0 or from common folklore that this review found false, overstated or unsupported. Listed in §15 so it does not creep back. |

### 0.2 Source types

`[PR]` peer-reviewed venue · `[PP]` preprint (arXiv, IACR ePrint) · `[IND]` industry deployment or vendor engineering post · `[STD]` standard (RFC, WHATWG) · `[DOC]` official documentation or source code at a named branch · `[OWN]` Crosstalk's own measurement (Day-0 probe, §11.3).

### 0.3 How this revision was verified (2026-09-19)

- Full texts of the nine most load-bearing LLM papers were downloaded and text-searched rather than summarised by a tool: Weiss et al., Whisper Leak, PromptEcho (USENIX '26 proceedings PDF), NetEcho, Carlini & Nasr, Wiretapping LLMs, Time Will Tell, When Speculation Spills Secrets, NetShaper.
- Official documentation was fetched for vLLM, OpenAI, Cloudflare, Microsoft, Google Cloud, nginx, Envoy, Node.js and Python. Runtime behaviour was checked in source (CPython 3.13 `asyncio/sslproto.py`, `selectors.py`; Node v24 `crypto_tls.cc`; aiohttp and uvicorn writers; openai-python and openai-node SSE decoders).
- A small Day-0 probe measured TLS record boundaries and timer behaviour in Python (asyncio, uvloop) and Node.js on Linux (§11.3). It is **indicative evidence, not the Day-1 gate**: it ran on a laptop inside WSL2.

### 0.4 Material corrections relative to v1.0

1. **"Nobody has measured what these defenses cost" is false.** Wiretapping LLMs measured bandwidth and time-per-token overheads for five streaming designs on a real vLLM-style service. Carlini & Nasr, When Speculation Spills Secrets, Time Will Tell, Whisper Leak and PromptEcho all report cost figures, analytically or measured (§4).
2. **Whisper Leak's mitigation numbers already come from classifiers trained on mitigated traffic.** Defense-aware training is not new for LLM traffic. What is missing is a side-by-side static-vs-adaptive pair, deployed (not simulated) batching and injection, and latency measurements (§2.2, §4).
3. **One write ≠ one TLS record ≠ one packet.** The v1.0 architecture assumed it. Source code and our probe show the mapping depends on the runtime and on the write API (§1.4, §11).
4. **GKE Autopilot does allow `NET_RAW`** (via Pod securityContext) and `NET_ADMIN` (via `--workload-policies=allow-net-admin`). The real restrictions are no privileged pods (partners excepted), no `hostNetwork` and no host namespaces (§14).
5. **PromptEcho is now verified against the proceedings PDF**, and it has a public artifact.
6. **NetEcho's "~80% via Trace A" and "20% dummy → ~2%"** were restated without their conditions. Both are narrower than v1.0 implied (§2.4).
7. **SSE `data:` dummy events are not ignored by OpenAI SDKs.** Only comment lines are ignored (§5.2).
8. **Python 3.14's incremental GC was reverted in 3.14.5.** No GC advantage should be assumed from 3.14 (§11.1).
9. The "leakage in bits" headline metric is **downgraded** to exploratory. Distribution-free MI lower bounds grow only as O(ln N) (§9.5).

---

## 1. Threat mechanism

### 1.1 What a passive on-path observer sees

| # | Fact | Label | Source |
|---|---|---|---|
| M1 | A TLS 1.3 record carries a 5-byte cleartext header: outer content type (always `application_data`=23 once keys are established), legacy version `0x0303`, and a 2-byte length. | VERIFIED | [STD] RFC 8446 §5.1–5.2 [R68] |
| M2 | For AEAD suites, ciphertext length = plaintext length + 1 (inner content type) + padding + 16 (tag). A record never carries more than 2^14 plaintext bytes. | VERIFIED | [STD] RFC 8446 §5.2, §5.4 [R68]; [OWN] 16,384-byte plaintext records observed as 16,401-byte records (§11.3) |
| M3 | TLS 1.3 permits record padding, but none of the runtimes allowed for Crosstalk exposes a per-record padding API: Python `ssl`, Node `tls`, or Java JSSE. | INFERENCE (no such API found in their documentation) | [DOC] Python `ssl` docs [R74]; Node `tls`/`http2` docs [R78] |
| M4 | On TCP, a passive observer with full packet capture can reassemble each direction's byte stream and parse record headers. This yields the record sequence (length, direction, time), including records spanning or sharing segments. | VERIFIED (method used in published attacks) | [PR] PromptEcho App. A [R3]; [PP] Whisper Leak §3.1 ("application data record sizes (derived from TLS record lengths)") [R2] |
| M5 | Over QUIC (HTTP/3), frames are encrypted inside packet protection. The observer sees UDP datagram sizes and timing but no record layer. | VERIFIED | [STD] RFC 9000, RFC 9001, RFC 9114 [R71] |
| M6 | Connection metadata (IPs, ports, SNI unless ECH is used, ALPN, handshake shape) is visible in addition to record lengths and times. | VERIFIED | [STD] RFC 8446 [R68] |

### 1.2 Why LLM streaming creates the channel

| # | Fact | Label | Source |
|---|---|---|---|
| M7 | Autoregressive generation plus SSE streaming makes each emitted chunk small, content-dependent and timed by the decoder. With vLLM's default `--stream-interval 1`, each chunk carries one token. | VERIFIED | [DOC] vLLM serve CLI: `--stream-interval` default `1` [R62] |
| M8 | An OpenAI-compatible chunk is a JSON object whose size is a near-constant envelope plus the token text (after JSON escaping). Our probe events carried 176–185 plaintext bytes for 1–10-character tokens. | VERIFIED | [PR] Weiss et al. [R1]; [OWN] §11.3 |
| M9 | Three observable channels result. **Size:** per-record length reveals token or token-group character length. **Timing:** inter-arrival, time to first response record, total duration. **Count:** the number of records reveals the number of tokens or groups. | VERIFIED | [PR] Weiss [R1]; [PP] Whisper Leak (size-only avg AUPRC 93.3% vs time-only 69.7% across 28 models) [R2]; [PP] NetEcho Trace A (counts) / Trace B (lengths) [R4]; [PP] Carlini & Nasr (timing) [R5]; [PP] Time Will Tell (count via total time) [R7] |
| M10 | Multi-token aggregation does not remove the size channel; it turns it into a merged-length channel. | VERIFIED | [PR] PromptEcho: topic for 42.5% of sessions, 25.5% of responses at cosine > 0.9 on GPT-4o and DeepSeek-V3 [R3] |

### 1.3 The observation stack Crosstalk must model (corrected)

v1.0 assumed `SSE event → one write() → one TLS record → one TCP segment → one packet`. That assumption is **REJECTED**. The following facts replace it.

| Layer boundary | What actually happens | Label | Source |
|---|---|---|---|
| Application intent → socket write | HTTP libraries decide how many writes one "event" becomes. aiohttp sends chunk framing through `writelines()` once the payload reaches `MIN_PAYLOAD_FOR_WRITELINES = 2048` bytes. uvicorn joins framing and body into one `write()`. | VERIFIED | [DOC] aiohttp `http_writer.py` [R76]; uvicorn `httptools_impl.py` [R77] |
| Socket write → TLS record (Python) | `asyncio` SSL transport: each buffer handed to `write()`/`writelines()` becomes its own `SSL_write` call, hence its own record(s). `writelines([a,b,c])` produces three records. | VERIFIED | [DOC] CPython 3.13 `sslproto.py` `_write_appdata`/`_do_write` [R74]; [OWN] 150 records for 50 events via `writelines` (§11.3) |
| Socket write → TLS record (Node) | `TLSWrap::DoWrite` concatenates all buffers of one `writev` into a single `SSL_write`. Writes that queue while a write is pending, or that happen in the same tick (HTTP corking), merge into one record. | VERIFIED | [DOC] Node v24 `src/crypto/crypto_tls.cc` [R78]; [OWN] 50 same-tick `res.write()` calls → 1 record of 9,181 bytes (§11.3) |
| TLS record → TCP segments | Records larger than the MSS span segments. Several small records can share one segment when writes are coalesced. | VERIFIED | [PR] PromptEcho App. A ("a single TCP segment may contain one or multiple TLS records") [R3] |
| Kernel coalescing | Linux TCP autocorking coalesces consecutive small writes when a prior packet of the flow is still queued in the qdisc or device. It is enabled by default. | VERIFIED | [DOC] kernel `ip-sysctl.rst` `tcp_autocorking` [R79]; [OWN] `tcp_autocorking=1` on the probe kernel |
| Offloads | TSO/GSO hand the NIC (or the software GSO path) super-segments to split at MSS. GRO/LRO merge received segments before capture. A host-side capture can show packets larger than the MTU. | VERIFIED | [DOC] kernel `segmentation-offloads.rst` [R79]; Wireshark wiki "CaptureSetup/Offloading" [R80]; [PR] HotNets '25: TSO sends MSS packets at line rate, outside application timing control [R51] |
| Retransmission | Retransmitted data may be repacketized. Linux's `tcp_retrans_collapse` tries to send bigger packets on retransmit. | VERIFIED | [DOC] kernel `ip-sysctl.rst` [R79] |
| Loopback | On `lo` the MTU is 65,536 and TSO/GSO/GRO are on, so loopback captures do not represent wire packetization. | VERIFIED | [OWN] `ethtool -k lo` on the probe host (§11.3) |

**Consequence (INFERENCE):** Crosstalk measures and stores three distinct observation layers and validates the mapping between them empirically:

1. **ApplicationEmission**: what the shaper intended and handed to TLS (time, plaintext bytes, kind).
2. **TLSRecord**: what the TLS stack produced, reconstructed from the TCP byte stream.
3. **NetworkPacket**: what the capture point saw.

The primary attack features are defined on the TLSRecord layer, which is robust to segmentation. Packet-layer features are a secondary representation, valid only under a documented offload configuration.

---

## 2. LLM traffic-analysis attacks (passive network observer)

### 2.1 Summary

| # | Work | Status | Observer & protocol | Serving regime | Headline result |
|---|---|---|---|---|---|
| R1 | *What Was Your Prompt?* Weiss, Ayzenshteyn, Amit, Mirsky | [PR] USENIX Security 2024 | Passive; TLS record sizes | Commercial assistants (one token per message at the time) | Reconstructs 29% of responses; topic inferred from 55% |
| R2 | *Whisper Leak.* McDonald, Bar Or (Microsoft) | [PP] arXiv:2511.03675, Nov 2025 | Passive; `tcpdump` on cloud Ubuntu clients; TLS app-data record sizes + inter-arrival times | 28 commercial LLM APIs; load not controlled | Binary topic detection, often > 98% AUPRC; 17/28 models reach 100% precision at 5–20% recall at a simulated 10,000:1 base rate |
| R3 | *From Length to Content* (PromptEcho). Li et al. | [PR] USENIX Security 2026; artifact at Zenodo 10.5281/zenodo.20441510 | Passive; TLS record sizes of merged multi-token chunks | GPT-4o, DeepSeek-V3 and other APIs (1–5 tokens/record) | Topic for 42.5% of sessions; 25.5% of responses at cosine > 0.9 |
| R4 | *NetEcho.* Zhang, Wu, Deng, Wang, Zhang | [PP] arXiv:2510.25472, Oct 2025 | Passive; Wireshark-filtered app-data packets | 7 deployment scenarios incl. self-hosted vLLM with speculative decoding | Topic Top-1 up to 98.2% (Trace A+B); ~70% of conversation information recovered on medical/legal data |
| R5 | *Remote Timing Attacks on Efficient LM Inference.* Carlini, Nasr | [PP] arXiv:2410.17175, Oct 2024 | Passive and active; inter-packet delay (+ packet size to undo token clustering) | Open models with speculative/parallel decoding; ChatGPT, Claude | 89.5–99.8% medical-vs-coding on open models; language ID 49% top-1 of 10 on ChatGPT |
| R6 | *Wiretapping LLMs.* Soleimani, Jia, Gim, Lee, Khandelwal | [PP] IACR ePrint 2025/167 | Passive; packet sizes and timing | Reference service (Phi-3 14B + 3.8B draft, RTX 4090, **prompts processed sequentially**); ChatGPT | Private-attribute inference 71–92% (vLLM-style), 50–90% (ChatGPT) |
| R7 | *Time Will Tell.* Zhang, Saileshwar, Lie | [PP] arXiv:2412.15431, Dec 2024 | Total response time → output token count | **Batch size 1 assumed** | Translation-language ASR 75–83%; GPT-4o class leak 74.7% (remote timing) |
| R8 | *When Speculation Spills Secrets.* Wei et al. | [PP] arXiv:2411.01076 | Per-iteration token counts or packet sizes | Research prototypes and vLLM (EAGLE) | 1-of-50 query fingerprinting: REST 100%, BiLD 95.2%, LADE 91.6%, EAGLE 77.6% (T=0.3) |
| R9 | *Leaky Language Models* (LeakyLMs). Majidi, Mireshghallah, Taram | [PP] arXiv:2607.20723, Jul 2026 | **API client** measuring per-token arrival times (not a passive third party) | Commercial APIs; single requests, load not varied | Detects speculative decoding (Gemini 2.5 Flash draft context ≈ 128K) and ranks architecture hyperparameters |

### 2.2 Whisper Leak: what it did and did not do (read from full text)

- **Design** (VERIFIED): 100 LLM-paraphrased variants of one target question ("legality of money laundering"); 80 for training/validation and 20 held out per trial. Each variant queried 100×. 11,716 Quora negatives queried once each. Spaces inserted randomly to defeat caching; queries shuffled and interleaved; temperature 1.0; 5 seeds per model. [R2 §3.1]
- **Mitigations** (VERIFIED, Table 3):

  | Mitigation | Model | Baseline → mitigated AUPRC | How evaluated |
  |---|---|---|---|
  | Random padding | microsoft-gpt-4o-mini | 97.5% → 92.9% (Δ 4.5 pp per table) | **Real provider-side obfuscation**: "new parallel data collections … with and without obfuscation enabled" |
  | Token batching (5) | openai-gpt-4o-mini | 98.2% → 94.8% (Δ 3.5 pp) | **Simulated on captured traces**: "inter-packet arrival times and data lengths are summed to form a single, larger packet" |
  | Packet injection | openai-gpt-4o-mini | 98.1% → 93.3% (Δ 4.8 pp) | **Simulated on traces**; bandwidth "typically 2-3× traffic volume" |

- **Attacker training** (VERIFIED/INFERENCE): the mitigated AUPRCs come from classifiers trained and tested within the mitigated datasets. In effect this is a defense-aware attacker. The paper does **not** report the matching static (undefended-trained) number, so the static-vs-adaptive gap is unknown.
- **Not measured** (VERIFIED by absence): latency, TTFT or throughput cost of any mitigation; serving load; record-vs-packet boundary validation.
- **Other facts** (VERIFIED): more training data keeps improving the attack (121,111-capture ablation, Fig. 4); no clear AUPRC trend across temperatures (Fig. 5). Batching providers still showed 84–100% precision at 5% recall (alibaba-qwen2.5 ~4.4 tokens/event, gemini-2.5-pro ~17.7, claude-3-haiku ~6.0).
- **Disclosure timeline** (VERIFIED): OpenAI mitigated (Aug 2025), Mistral (Sep 2025), xAI (Aug 2025), Microsoft (Oct 2025).

### 2.3 PromptEcho (verified against the proceedings PDF)

- Per-service token merging varies. Table 1 reports Gemini ×1, GLM ×1, ChatGPT 2–3, DeepSeek 2–3, LLaMA 1–4, Qwen 1–4, StepFun 1–5 tokens per TLS record, with ASR 30–55%. (VERIFIED; a time-dependent vendor behaviour, so do not treat it as a stable property.)
- Mitigation discussion (VERIFIED, not a deployed evaluation): fixed padding to the MSS costs ≈ 3.2× bandwidth on average; "dynamic padding" ≈ 1.4×. OpenAI deployed default-on `include_obfuscation` in the Responses API.
- Appendix A (VERIFIED) documents the response preamble in encrypted traffic: **New Session Ticket records**, the HTTP header record and the first SSE event, plus the `data:[DONE]` + chunked terminator. This confirms that TLS control records and HTTP framing confound naive size features.

### 2.4 NetEcho (precise restatement)

- Evaluated scenarios A, B, D, E, F, G (Table IV). Scenarios F and G "simulate active defense settings by implementing padding logic in vLLM (using Qwen3-32B)" with speculative decoding. In F, padding is therefore deployed in a self-hosted serving path. (VERIFIED)
- Trace A (token counts per group) alone gives Top-1/Top-3 of 72.6–77.7 / 90.9–91.4% (medical) where it is informative. For the GPT-4o API it was ineffective (≈ 11% Top-1), which suggests OpenAI restructures packets. (VERIFIED; v1.0's "~80%+ via Trace A" was a loose paraphrase.)
- Dummy-packet defense was **simulated** by "inserting copies of existing packets at random positions". Accuracy drops ≈ 2% on average up to a 20% dummy rate, then sharply at 50–100% in several scenarios (e.g. 94→24% Top-1). (VERIFIED, Table IX)
- Padding degrades Trace B to Trace-A-only performance. Network noise (loss/reordering up to 10%) was simulated, with topic recovery staying above 80%. (VERIFIED)
- Downstream apps built on frameworks like Next.js or FastChat do not inherit upstream padding. "The compatibility problems are often more challenging than the modification cost itself." (VERIFIED)

### 2.5 Wiretapping LLMs (under-weighted in v1.0)

It measures five streaming designs on a reference service and reports **both** attack accuracy **and** overheads (VERIFIED, §6–7, Fig. 11–12):

| Design | Security result | Overhead measured |
|---|---|---|
| TokenPadding (pad each token to max encoding) | Ineffective (timing still leaks) | 7.5× network |
| PacketPerRound (one packet per speculative round) | Reduced accuracy vs TokenPadding | none |
| PacketPadding (pad + inject fake tokens per round) | Near random guess in evaluated cases | 119× bandwidth; negligible interactivity change |
| NonStreaming | Secure | time-per-token 11.6× / 9.7× |
| NoSpecDecoding | Secure | interactivity ~5.8× worse |

Serving limitation (VERIFIED): "a reference LLM service that processes user prompts sequentially". Concurrency and continuous batching were not studied.

### 2.6 Other cost evidence

- **Carlini & Nasr** (VERIFIED, §7): a constant-output-rate defense "which will thwart our attacks completely" (their timing attacks). Derived from observed inter-packet-delay distributions: a 10 ms interval gives essentially no latency and +50–300% bandwidth; 80 ms gives < 10% bandwidth and +4–13 ms per token. This is an **analysis over measured distributions, not a deployed shaper**, and it does not normalize sizes.
- **When Speculation Spills Secrets** (VERIFIED, §6): constant-length padding to 1,024 B drops accuracy to random guessing at 230× payload; random padding up to 48 B gives 5–8× payload; aggregating over up to 20 iterations reduces accuracy while latency "linearly increases".
- **Time Will Tell** (VERIFIED, §7.3): padding output to the maximum length costs +93% latency on average (Tower) or +13% (MBart50/M2M100), with 67–108% plaintext padding.
- **LeakyLMs** (VERIFIED from abstract/summary): constant per-token timing is "impractical", increasing latency "several times".

---

## 3. Timing, serving and cache channels (API-client or co-tenant adversary)

Threat model differs from Crosstalk's core: here the adversary measures its **own** request latency. Relevant only to supporting experiment E4.

| Work | Status | Mechanism | Key result |
|---|---|---|---|
| InputSnatch, Zheng et al. [R11] | [PP] arXiv:2411.18191 | Prefix/semantic cache timing | Up to 62% medical-data theft; up to 100% legal-topic identification |
| The Early Bird Catches the Leak, Song et al. [R12] | [PP] arXiv:2409.20002 (journal version reported, not re-verified) | KV and semantic cache timing | System-prompt and shared-prefix recovery on deployed services |
| I Know What You Asked (PromptPeek), Wu et al. [R13] | [PR] NDSS 2025 | SGLang radix-tree KV cache probing | 99–100% prompt reconstruction in their setting |
| Auditing Prompt Caching in LM APIs, Gu et al. [R14] | [PR] ICML 2025 | Statistical tests on TTFT | Global cross-user cache sharing detected in 7 API providers |
| SafeKV [R15], PrefixWall [R16] | [PP] 2025, 2026 | Selective KV sharing / reuse monitoring | Mitigations with reduced isolation cost |
| Contention-induced reliability collapse, Abu Bakar [R17] | [PP] arXiv:2609.06853, Sep 2026 | API-client cache timing **under load** | Effect size d 0.78 → 0.21 with two background workers; AUROC 0.650 → 0.531 near 61% overlap. Quiet-system measurements overestimate real reliability |
| vLLM cache salting [R18] | [DOC] vLLM PR #17045; prefix-caching design doc | Per-request `cache_salt` folded into the first block hash | Deployed mitigation for cross-tenant prefix-cache timing |

**INFERENCE:** prefix-cache leakage is well studied and already mitigated in vLLM (`cache_salt`). The newest evidence says load degrades it, even from the API-client seat. It is not a good flagship for Crosstalk. It stays a later, supporting experiment (E4), gated on E0–E2.

---

## 4. Defenses evaluated for LLM streams: the comparison that bounds Crosstalk's novelty

| Work | Defenses | Implementation | Attacker trained on defended data? | Static-vs-adaptive pair? | Costs reported | Serving load varied? | Record/packet boundary validated? |
|---|---|---|---|---|---|---|---|
| Weiss 2024 [R1] | padding, grouping, full batching | Proposed only | — | — | Qualitative | No | — |
| Carlini & Nasr 2024 [R5] | constant rate | Analytical over measured IPD distributions | n/a (claims thwarts timing attack) | No | Bandwidth vs latency curve | No | No |
| Spec Spills 2024 [R8] | const/random padding, iteration aggregation | Applied in their setup (payload overhead computed) | Likely (INFERENCE: fingerprints built under the mitigated setting) | No | Payload overhead; latency qualitative | No | No |
| Time Will Tell 2024 [R7] | pad to max length; prompt constraints | Analytical cost from measured lengths | Partly | No | Latency and padding % | No (batch 1) | No |
| Wiretapping LLMs 2025 [R6] | TokenPadding, PacketPerRound, PacketPadding, NonStreaming, NoSpecDecoding | **Implemented in a reference service** | Likely (INFERENCE: accuracy reported per streaming design) | No | **Bandwidth + time-per-token measured** | **No (sequential)** | Not reported |
| Whisper Leak 2025 [R2] | random padding; batching; injection | Padding: **real provider**; batching/injection: **trace simulation** | Yes (within-condition training) | **No** | Injection bandwidth only | No | No |
| NetEcho 2025 [R4] | padding (in vLLM), dummy packets | Padding deployed in own vLLM (F); dummies simulated | Yes (models trained per scenario) | No | No | No | No |
| PromptEcho 2026 [R3] | padding variants | Discussion; overhead estimates | — | No | Bandwidth estimates | No | Preamble documented |
| NetShaper 2024 [R38] (not LLM) | DP shaping (size + time) | **Deployed middlebox (QUIC tunnel)** | DP guarantee + empirical | n/a | Bandwidth + latency measured | Adapts to workload | Tunnel-level |

**What the table establishes (INFERENCE, reviewed as of 2026-09-19):**

- Isolated cost measurements exist (Wiretapping LLMs, NetShaper, Carlini & Nasr, Spec Spills, Time Will Tell). Any claim that "costs were never measured" is false.
- Training attackers on defended traffic exists: explicitly in Whisper Leak and NetEcho, and very likely in Wiretapping LLMs and Spec Spills. "Adaptive attackers were never used for LLM defenses" is false.
- **We found no work that combines** (a) several defense families deployed in the operator-controlled serving path in front of a production serving engine, (b) wire observation with the emission→record→packet mapping validated independently, (c) static and adaptive attackers reported as a pair under grouped statistics, (d) jointly measured latency (TTFT/ITL percentiles), bandwidth, throughput and CPU, and (e) **serving load and batching configuration varied as experimental factors**. Of these, (e) is the least covered: every LLM paper above either used uncontrolled commercial load or ran at batch size 1 / sequentially.
- The simulation-vs-deployment gap for LLM stream defenses is untested. Whisper Leak simulated batching; the WF literature shows simulated overheads can be badly wrong (§6.4).

---

## 5. Industry deployments (as of 2026-09-19)

### 5.1 Deployed mitigations

| Vendor | Mechanism | Detail | Label | Source |
|---|---|---|---|---|
| Cloudflare (Workers AI, AI Gateway) | Random-length `p` JSON property on each SSE message | Since 2024-03-14; automatic; example `data: {"response":"portugal","p":"abcdefghijklmnopqrstuvwxyz0123456789a"}`; no cost figures published | VERIFIED | [IND] Cloudflare blog [R63] |
| OpenAI | `obfuscation` field on streaming deltas, on by default | Opt out with `stream_options.include_obfuscation=false`, e.g. "if you trust the network links between your application and the OpenAI API"; adds "a small amount of overhead" | VERIFIED | [DOC] OpenAI API reference [R64] |
| Microsoft Azure | `obfuscation` field | Same mechanism | VERIFIED | [IND] Microsoft Security blog 2025-11-07 [R65] |
| Mistral | `p` field | Similar obfuscation | VERIFIED | [IND] [R65] |
| xAI | Unspecified | "deployed protections" | VERIFIED (no detail) | [IND] [R65] |
| Various APIs | Multi-token chunks (passive batching) | Tokens per record vary by vendor and over time | VERIFIED as observed; motivation unknown | [PP] Whisper Leak Table 2 [R2]; [PR] PromptEcho Table 1 [R3] |
| Self-hosted engines (vLLM) | Server-side token aggregation knob | `--stream-interval` (default 1) and per-request `stream_interval` (clamped up to the server value; first and last chunks sent immediately). A throughput knob, not a security feature | VERIFIED | [DOC] vLLM [R62] |

**INFERENCE:**
- Vendor mitigations are upstream and size-focused (random padding) or incidental (aggregation). Applications that re-stream an upstream API do not inherit them (NetEcho scenario E).
- No vendor publishes efficacy against an adaptive attacker, or cost figures.
- Gateway-side padding **already exists** (Cloudflare AI Gateway), so Crosstalk must not claim "no gateway defense exists".

### 5.2 Client compatibility of padding and cover traffic

| Client | Comment lines (`: …`) | Comment-only event block | `data:` event with unknown content | Unknown JSON fields in a chunk | Source |
|---|---|---|---|---|---|
| openai-python (`main`) | Ignored (`if line.startswith(":"): return None`) | Produces no event | JSON-decoded and **yielded to the application** (any `event:` name except `thread.*` special-casing) | Tolerated (vendor itself adds `obfuscation`) | [DOC] `src/openai/_streaming.py` [R66] |
| openai-node (`master`) | Ignored (`if (line.startsWith(':')) return null`) | Produces no event (`!this.event && !this.data.length`) | `JSON.parse`d and **yielded**; non-JSON throws `SyntaxError` | Tolerated (plain objects) | [DOC] `src/core/streaming.ts` [R66] |
| Spec-compliant EventSource/parsers | Ignored | No dispatch | Dispatched as a message | n/a | [STD] WHATWG HTML §9.2 [R67] |

**Consequences (INFERENCE):**
- Padding carried in SSE comment lines, or in an extra JSON field, is **deployment-compatible** with the official OpenAI SDKs.
- Cover traffic sent as comment-only blocks is **deployment-compatible**.
- Cover traffic sent as `data:` events is **experimental-only**: it reaches application code as a bogus chunk, or crashes the Node SDK on non-JSON data.
- Hand-rolled parsers (e.g. splitting on `\n\n` and `JSON.parse(block.slice(6))`) may break on comment blocks. Compatibility must therefore be claimed only for a tested client matrix.

---

## 6. Classical traffic-analysis lessons (website fingerprinting and adjacent)

Entries marked † were verified in v1.0 (2026-09-13) and spot-checked on 2026-09-19. The others were re-verified on 2026-09-19.

### 6.1 Same physics, older protocols

| Work | Status | Lesson |
|---|---|---|
| Song, Wagner, Tian — SSH keystroke timing † [R19] | [PR] USENIX Security 2001 | One keystroke per packet leaks timing |
| Wright et al. — *Spot Me if You Can* † [R21] | [PR] IEEE S&P 2008 | Variable-bit-rate encoding leaks phrases |
| Chen et al. — *Side-Channel Leaks in Web Applications* † [R20] | [PR] IEEE S&P 2010 | HTTPS size/timing leaks health and financial data |
| Schuster, Shmatikov, Tromer — *Beauty and the Burst* † [R22] | [PR] USENIX Security 2017 | Burst patterns identify encrypted video |

### 6.2 Attacks and representations

CUMUL (signed cumulative-size curve, 104 features) [R23] †; k-fingerprinting (175 features, RF leaf embedding) [R24] †; Deep Fingerprinting (1-D CNN; > 90% against WTF-PAD, 49.7% against Walkie-Talkie) [R25] †; Robust Fingerprinting (traffic aggregation matrix) [R26] †. **INFERENCE:** gradient-boosted trees over summary statistics plus CUMUL are the right cheap baseline; sequence CNNs are justified only if the baseline saturates.

### 6.3 Defenses and what they cost

| Defense | Status | Mechanism | Cost / outcome |
|---|---|---|---|
| BuFLO / *Peek-a-Boo* [R27] | [PR] IEEE S&P 2012 | Fixed-size packets at a fixed rate | Nine countermeasures fail against coarse features (total time, bandwidth, bursts); BuFLO needs very high overhead |
| Tamaraw [R28] † | [PR] CCS 2014 | Constant rate per direction; pad to a multiple of L | High data/time overhead |
| WTF-PAD [R29] † | [PR] ESORICS 2016 | Adaptive padding, zero latency | Later broken to > 90% by DF trained on defended traces |
| Walkie-Talkie [R30] † | [PR] USENIX Sec 2017 | Half-duplex + burst molding | ~31% bandwidth, ~34% time |
| FRONT/GLUE [R31] † | [PR] USENIX Sec 2020 | Front-loaded dummies | Zero latency, moderate data |
| RegulaTor [R32] †, Surakav [R33] †, Palette [R34] †, DeTorrent [R35] † | [PR] 2022–2024 | Regularisation, generative, cluster anonymisation, adversarial padding | Better trade-offs; Palette deployed as a Tor PT |
| ALPaCA [R36] † | [PR] PETS 2017 | **Server-side application-layer** padding | Distribution-aware padding far cheaper than naive padding for equal protection |
| Maybenot / Mullvad DAITA [R37] † | [PR] WPES 2023; [IND] | State-machine padding/blocking framework in a production VPN | Shippable traffic-shaping precedent |
| **NetShaper** [R38] | [PR] USENIX Security 2024 | Differentially private shaping of sizes and timing; QUIC tunnel; middlebox | Deployed, measured bandwidth/latency trade-offs on video and web workloads |
| Pacer [R39] † | [PP] | Hypervisor-level cloud shaping | — |
| Rate–distortion framing [R42] | [PP] arXiv:2607.17889, Jul 2026 | Theoretical Pareto frontier (MI vs Wasserstein cost) for WF defenses | Trace-based, not deployed, not adaptive |

### 6.4 Methodological lessons that transfer

| Lesson | Evidence | Label |
|---|---|---|
| Defenses must be evaluated against attackers trained on defended traffic | DF vs WTF-PAD [R25]; SoK re-evaluating nine "efficient" defenses with DL attacks and an on-network implementation [R52] | VERIFIED |
| Coarse features (total bytes, duration, counts) defeat defenses that only perturb fine structure | *Peek-a-Boo* [R27] | VERIFIED |
| Simulated overheads can be badly wrong | WFDefProxy: FRONT ≈ 23% more data than simulated; Tamaraw 28–45% less data and 21% time vs 51–242% simulated [R49] | VERIFIED ([PP]) |
| Application-layer defenses do not control on-wire packet timing | HotNets '25 (TSO sends MSS packets at line rate) [R51] | VERIFIED |
| Padding-only defenses add delay under network-wide deployment | Witwer, Holland, Hopper [R50] † | VERIFIED |
| Standards-blessed padding can underdeliver | Encrypted DNS: EDNS(0) padding leaves F1 ≈ 0.45 [R53] † | VERIFIED |
| Base rates govern real-world precision | Juarez et al. CCS 2014 [R43] † | VERIFIED |

---

## 7. Transport, TLS and HTTP realities

### 7.1 TLS 1.3 details that affect features

| Fact | Consequence for Crosstalk | Label / source |
|---|---|---|
| Server sends NewSessionTicket records after the handshake. OpenSSL defaults to 2 in TLS 1.3; Python exposes `SSLContext.num_tickets` for server contexts | Ticket records (~250–282 B in our probe) resemble token events. Baseline sets `num_tickets = 0` in the shaper and records it; a realism arm keeps the default | VERIFIED [DOC] Python `ssl` [R74]; [OWN] §11.3; [PR] PromptEcho App. A [R3] |
| `close_notify` alert is a small encrypted record at stream end (19 B on the wire in our probe) | The feature extractor must not count it as content | VERIFIED [OWN] |
| AEAD expansion is constant per suite | Emission→record prediction: `record_len = plaintext + 17` (AES-GCM / ChaCha20-Poly1305) | VERIFIED [R68]; [OWN] |
| `SSLContext.keylog_filename` writes NSS key logs | Enables tshark decryption in **validation-only** runs; key logs are restricted artifacts | VERIFIED [DOC] [R74] |

### 7.2 TCP

TCP is a byte stream with no message boundaries; retransmitted data need not reuse the original segmentation [STD RFC 9293, R73]. `TCP_NODELAY` disables Nagle. asyncio sets it on TCP transports by default, and Node's `http.createServer` `noDelay` defaults to `true` [R78]. Autocorking, offloads and retransmission repacketization are covered in §1.3. MSS depends on MTU: a GCP VPC defaults to MTU 1460 (§14), while a local Ethernet or veth has 1500.

### 7.3 HTTP/1.1

Chunked transfer coding adds `hex-size CRLF … CRLF` around each chunk [STD RFC 9112 §7.1, R69]. For our event sizes that is +6 bytes, and the hex width grows at 256 and 4,096 bytes. This framing is deterministic and becomes part of every record length.

### 7.4 HTTP/2

- Multiplexes streams on one connection. Stream IDs are encrypted, so an observer cannot attribute records to requests when several streams are active. [STD RFC 9113, R70]
- DATA frames can carry padding (Pad Length + PADDED flag). §10.7 warns that intermediaries may "remove padding or frame fragmentation", and that padding does not address timing. (VERIFIED)
- Node exposes `paddingStrategy` (NONE / MAX / ALIGNED to 8 bytes) [R78]. Python `h2` supports DATA padding via `send_data(..., pad_length=...)` [DOC h2 4.4.1].
- **INFERENCE:** using internal request IDs to split a multiplexed H2 capture is an oracle. The H2 arms must separate "one active stream per connection" (controlled) from "multiplexed" (session-level attack).

### 7.5 HTTP/3 / QUIC

Packet protection hides frames. Coalesced QUIC packets and PADDING frames exist. Observable features are UDP datagram sizes and timing only [R71]. Python `aioquic` 1.3.0 (Oct 2025) exists for a later arm. **INFERENCE:** H3 needs a separate feature pipeline and is out of the ten-day scope.

### 7.6 Capture artifacts

- A capture on the sending host sees pre-segmentation super-packets with TSO/GSO. A capture on the receiving host sees GRO-merged segments. [R79, R80, R51]
- On veth/bridge paths inside one kernel, GSO packets can traverse unsegmented unless offloads are disabled on the sending veth. (INFERENCE from kernel GSO semantics; **validated on Day 2** by checking max captured IP length ≤ MTU.)
- Software timestamps are taken at the AF_PACKET tap, not on the wire. Relative timing is valid within one capture host's clock. (INFERENCE)

---

## 8. Serving-stack behaviour

| Fact | Label | Source |
|---|---|---|
| vLLM 0.29.0 is current (released 2026-09-09; Python < 3.15, ≥ 3.10) | VERIFIED | [DOC] PyPI [R62] |
| `--stream-interval` default 1; per-request `stream_interval` raises it for that request; first and last chunks always sent immediately | VERIFIED | [DOC] vLLM docs [R62] |
| Chat-completions request accepts `min_tokens`, `ignore_eos`, `seed`, `max_completion_tokens`, `cache_salt`, `stream_interval` | VERIFIED | [DOC] vLLM `entrypoints/openai/chat_completion/protocol.py` (main, 2026-09-19) [R62] |
| `--max-num-seqs`, `--max-num-batched-tokens`, `--enable-chunked-prefill`, `--enable-prefix-caching`, `--prefix-caching-hash-algo` (default `sha256`), `--seed` (default 0) are serve options | VERIFIED | [DOC] vLLM serve CLI [R62] |
| V1 metrics: `vllm:time_to_first_token_seconds`, `vllm:inter_token_latency_seconds`, `vllm:e2e_request_latency_seconds`, `vllm:request_queue_time_seconds`, `vllm:num_requests_running`, `vllm:num_requests_waiting`, `vllm:kv_cache_usage_perc`, `vllm:prefix_cache_queries`, `vllm:prefix_cache_hits` | VERIFIED | [DOC] vLLM metrics design [R62] |
| Continuous (iteration-level) batching interleaves requests per decoding step; PagedAttention manages KV memory | VERIFIED | [PR] Orca, OSDI 2022 [R60]; vLLM, SOSP 2023 [R61] |
| Under continuous batching, per-token timing of one stream depends on co-scheduled requests | INFERENCE (mechanism); Time Will Tell expected the channel to "remain strong" but did not test it [R7] | — |
| `min_tokens = max_completion_tokens = L` yields exactly L output tokens (EOS suppressed until L) | INFERENCE from parameter semantics; **validated Day 3** by checking realized `completion_tokens` | [R62] |

---

## 9. Evaluation methodology

### 9.1 Pitfalls and the Crosstalk control for each

| Pitfall | Evidence | Crosstalk control (PROPOSED) |
|---|---|---|
| Data snooping via related samples in train/test | Arp et al. [R48] | Prompt-family-disjoint grouped CV; template-disjoint and session-disjoint checks |
| Spurious correlates (length) | Arp et al. [R48]; *Peek-a-Boo* [R27] | Naturalistic **and** length-matched workload regimes, reported side by side (§10) |
| Time-as-label | Arp et al. [R48]; Whisper Leak shuffling [R2] | Randomized interleaving of classes **and** defense arms within a session |
| Inappropriate baselines | Arp et al. [R48] | Chance, majority and null-label controls on every evaluation |
| Base-rate neglect | Juarez 2014 [R43] | Precision at fixed recall re-weighted to explicit base rates |
| Model-selection leakage | Cawley & Talbot [R56] | Nested grouped CV; scaler/feature selection/early stopping inside the inner loop |
| Non-independent observations (many traces per prompt family) | Hierarchical bootstrap [R55]; bootstrap theory [R58] | Cluster (prompt-family) bootstrap, paired across arms on identical resamples; session-level resampling as a robustness check |
| Many comparisons | Holm 1979 [R57] | Pre-declared primary comparisons with Holm correction; everything else labelled exploratory |
| "Your classifier was weak" | WeFDE [R45], Cherubin [R44], DeepSE-WF [R46] | Report the strongest attacker from a fixed ladder with a declared tuning budget; information-theoretic estimates only as exploratory |

### 9.2 Why non-overlapping CIs are not the test

Two configurations evaluated on the same prompt families are positively correlated. Separately computed CIs overlap even when the paired difference is clearly non-zero, so comparing them is both underpowered and the wrong test. **Crosstalk reports the paired difference and its CI under a family-level cluster bootstrap** (INFERENCE from standard paired/clustered bootstrap theory [R55, R58]).

### 9.3 Adaptive attacker discipline

Static and adaptive attackers must share the same held-out families, model class and tuning budget. The adaptive attacker's training data comes from defended collections of **training families only** (PROPOSED). Otherwise a retrained attacker can appear stronger merely because it saw the test prompts' siblings.

### 9.4 Learning curves

Whisper Leak shows attack performance still rising with data [R2]. Crosstalk reports learning curves for the flagship configurations so that a small-data result is not read as a ceiling (PROPOSED).

### 9.5 Leakage in bits: downgraded

Any distribution-free, high-confidence lower bound on mutual information from N samples is at most O(ln N) [R54]. kNN/KDE estimators in WF work are estimator-dependent [R44–R46]. **Decision:** balanced accuracy, macro-F1, PR-AUC, log-loss and retained leakage are the primary metrics. An MI **lower bound from the confusion matrix** (I(Y;Ŷ) ≤ I(Y;X) by data processing) is an optional P1 figure. kNN MI/Bayes-error estimators are P2 exploratory (INFERENCE).

---

## 10. Confounders and contamination specific to this problem

| Confounder | Mechanism | Control (PROPOSED unless stated) |
|---|---|---|
| Prompt length | Client→server request record sizes and TTFT (prefill) scale with prompt tokens | Matched regime stratifies prompt byte/token buckets per class; report request-size-only baseline |
| Response length | Record count and duration scale with output tokens (Time Will Tell) | Matched regime M2 forces equal output lengths via `min_tokens = max_completion_tokens` |
| Topic ↔ length coupling in naturalistic data | Classes naturally differ in answer length | Report "length-only" attacker (features: counts/durations only) next to the full attacker |
| Experiment-control headers | A per-request policy header would change request size by arm | Fixed-width header values; request-size parity asserted in a test |
| TLS control records | Session tickets, `close_notify` resemble content records | `num_tickets = 0` in baseline; a record-role classifier validated against key-logged decryption |
| HTTP framing | Headers record, chunk framing, `[DONE]` terminator | Modelled explicitly; the terminator is a public, attacker-known constant |
| Caching | Prefix-cache hits shorten TTFT | Unique vLLM `cache_salt` per request (fixed width), so no cross-request block reuse [R18]; E4 turns sharing on deliberately |
| Time drift | Load, thermal state, GPU clocks change over a session | Randomized interleaving of arms; session-disjoint validation; paired arms in the same session; E2 concurrency in time-alternating blocks with replicate sessions |
| Sampling randomness | Temperature changes token paths | Fixed temperature per regime; seeds recorded; determinism probe measures the noise floor |
| Capture vantage | Packetization differs between vantages | Primary features on TLSRecord layer; vantage recorded; packet features only with offload config recorded |

---

## 11. Runtime and data-plane implementation evidence

### 11.1 Source-level and documentation facts

| Fact | Label | Source |
|---|---|---|
| CPython `asyncio` SSL transport: one `SSL_write` per buffer; ciphertext from several writes is flushed together to the socket | VERIFIED | [DOC] CPython 3.13 `sslproto.py` [R74] |
| CPython `EpollSelector` rounds timeouts **up** to 1 ms ("epoll_wait() has a resolution of 1 millisecond") | VERIFIED | [DOC] CPython `selectors.py` [R74] |
| Python 3.14.0–3.14.4 shipped an incremental GC; **3.14.5 reverted** to the 3.13 generational GC after production memory-pressure reports | VERIFIED | [DOC] Python 3.14 What's New [R75] |
| uvloop 0.22.1 ships wheels for CPython 3.8–3.14; repository active (libuv 1.52.1 upgrade, Jul 2026) | VERIFIED | [DOC] PyPI, GitHub [R81] |
| dpkt: last commit 2024-05-05 (effectively unmaintained); pyshark last release 2023 | VERIFIED | [DOC] GitHub, PyPI [R81] |
| Node `TLSWrap::DoWrite` concatenates multi-buffer writes into one `SSL_write` | VERIFIED | [DOC] Node v24 [R78] |
| Envoy `ext_proc` `FULL_DUPLEX_STREAMED` lets an external processor buffer, split and re-emit body chunks. Each chunk crosses a gRPC stream, and Envoy performs the downstream TLS writes | VERIFIED | [DOC] Envoy `processing_mode.proto` [R82] |
| nginx `proxy_buffering off` passes the response "synchronously, immediately as it is received", with reads bounded by `proxy_buffer_size`. It has no scheduling of its own | VERIFIED | [DOC] nginx [R83] |
| OpenResty `ngx.sleep` is available only in rewrite/access/content/timer/ssl phases, **not** in body filters | VERIFIED | [DOC] lua-nginx-module README [R84] |

### 11.2 Data-plane options assessed

| Option | Can it implement timing defenses? | Record-boundary control | Verdict | Label |
|---|---|---|---|---|
| Python asyncio + uvloop, own TLS termination | Yes (timers on the loop) | Deterministic: 1 `write()` ≤ 16 KiB → 1 record, even back-to-back | **Primary candidate** | VERIFIED [OWN] + [DOC] |
| Node.js `https` | Yes | Same-tick or backpressured writes merge into one record; needs explicit emission serialization | Fallback candidate | VERIFIED [OWN] + [DOC] |
| Java + Netty (no Spring) | Yes (epoll transport has fine-grained scheduling; not measured) | Controllable via explicit `writeAndFlush` | Second fallback, only if both above fail the gate | UNVERIFIED (not measured) |
| nginx / OpenResty | Only by rewriting the proxy in Lua content handlers | Reads coalesce upstream bytes; nginx decides records | Rejected for the shaper | INFERENCE from [R83, R84] |
| Envoy + Python `ext_proc` | Yes, via `FULL_DUPLEX_STREAMED` | Envoy decides downstream TLS writes; extra gRPC hop per chunk | Rejected for P0 (opaque, extra hop); possible later "gateway-realism" arm | INFERENCE from [R82] |
| HAProxy / Caddy / Traefik | Lua (HAProxy) or Go plugins (Caddy/Traefik) required | Proxy-controlled | Rejected (language constraint + opacity) | INFERENCE |

### 11.3 Day-0 probe results [OWN]

**Status: indicative evidence, not the Day-1 gate.** The probe scripts are not kept in the repository. The Day-1 gate rebuilds and re-runs them on the lab host (IMPLEMENTATION_PLAN §3.2).

**Setup (2026-09-19):**
- Laptop (i5-12450HX, 16 GB), WSL2 kernel 6.6.87.2 with 10 vCPUs, Ubuntu 24.04.
- Python 3.13.15 (uv-managed) with uvloop 0.22.1; Node.js v24.21.0.
- TLS 1.3 with a self-signed P-256 certificate. Client and server on the same host over TCP loopback.
- Loopback is valid for runtime overhead, **not** for packet-layer claims (§1.3).

**(a) Emission → TLS record mapping.** An instrumented client parses record headers from raw ciphertext before decryption (no capture tool needed).

| Server behaviour | Events | Response app-data records | Observation |
|---|---|---|---|
| Python asyncio / uvloop, one `transport.write()` per event, 5 ms apart | 50 | 52 (headers + 50 + terminator) | **1 write = 1 record** |
| Same, 50 writes back-to-back in one loop iteration | 50 | 52 | Records stay separate under bursts |
| Python `transport.writelines([size_line, payload, CRLF])` per event | 50 | 152 | **3 records per event** |
| Python, 20,000-byte events | 3 | 8 | Split at 16,384 plaintext bytes (record length 16,401) |
| Node `res.write()` per event, 5 ms apart | 50 | 50 | Response headers merged into the first event's record; terminator merged into the last |
| Node, 50 `res.write()` in the same tick | 50 | **1** (9,181 B) | Same-tick writes are coalesced into one record |
| TLS 1.3 server, default tickets | — | +2 records (250 B Python, 282 B Node) after the handshake | `num_tickets = 0` removes them (Python) |
| Connection close | — | 19-byte record | `close_notify` alert |

**(b) Timer-driven emission lateness.** Per-stream absolute deadlines every 20 ms, one ~200-byte record per tick, 20 s per run, first 50 ticks excluded. Values are the median across repetitions; "max" is the worst repetition.

| Runtime | Streams | Reps | Ticks/rep | Lateness p50 | p99 | p99.9 | max | CPU (cores) | RSS |
|---|---|---|---|---|---|---|---|---|---|
| Python asyncio | 1 | 3 | 951 | 1.46 ms | 2.78 | 3.19 | 4.6 | 0.03 | 26 MB |
| Python uvloop | 1 | 3 | 951 | 0.73 | 1.61 | 2.29 | 3.0 | 0.03 | 29 MB |
| Node 24 | 1 | 3 | 951 | 0.46 | 1.50 | 1.71 | 2.5 | 0.03 | 61 MB |
| Python asyncio | 64 | 3 | 60,864 | 0.78 | 1.89 | 4.90 | 7.2 | 0.39 | 50 MB |
| Python uvloop | 64 | 3 | 60,864 | 0.33 | 1.60 | 2.37 | 10.4 | 0.34 | 38 MB |
| Node 24 | 64 | 3 | 60,864 | 0.01 | 1.54 | 2.19 | 16.7 | 0.28 | 87 MB |
| Python asyncio | 256 | 3 | 243,456 | 0.84 | **8.57** | **24.48** | 32.1 | 0.73 | 120 MB |
| Python uvloop | 256 | 6 | 243,456 | 0.42 | 3.18 | 7.03 | 17.3 | 0.60 | 63 MB |
| Node 24 | 256 | 3 | 243,456 | 0.47 | 4.32 | 9.13 | 29.0 | 0.50 | 119 MB |

**(c) Passthrough relay latency.** Mock OpenAI-compatible upstream (plain HTTP, one event per 25 ms per stream) → relay (TLS server, one write per SSE event) → TLS client. Latency is client receive time minus upstream send time on the same monotonic clock. 400 events per stream; median of 2 repetitions.

| Path | Streams | Events/rep | p50 | p99 | p99.9 | max | Relay CPU (cores) |
|---|---|---|---|---|---|---|---|
| Direct (no relay, no TLS) | 1 / 64 / 256 | 380 / 24,320 / 97,280 | 0.47 / 0.32 / 0.51 ms | 0.97 / 1.16 / 1.75 | 1.43 / 1.82 / 4.17 | 1.6 / 3.6 / 11.6 | — |
| Python uvloop relay | 1 / 64 / 256 | same | 0.83 / 0.69 / 0.92 | 1.56 / **2.04** / 6.15 | 2.01 / 2.86 / 13.92 | 2.1 / 6.3 / 55.2 | 0.02 / 0.23 / 0.44 |
| Python asyncio relay | 1 / 64 / 256 | same | 0.88 / 0.71 / 0.87 | 1.63 / 2.10 / 5.92 | 2.39 / 3.19 / 13.44 | 2.8 / 7.1 / 33.5 | 0.03 / 0.27 / 0.56 |
| Node relay | 1 / 64 / 256 | same | 1.11 / 0.68 / 0.74 | 2.02 / 2.05 / 10.16 | 2.47 / 4.06 / 20.42 | 2.6 / 6.4 / 32.5 | 0.04 / 0.31 / 0.57 |

**Interpretation (INFERENCE):**
- For deterministic record boundaries, Python's `write()` path is the simplest to reason about. Node needs an explicit emission serializer.
- The default asyncio loop degrades at 256 streams. uvloop is required.
- At the load the experiments need (≤ 64 concurrent streams, ≈ 2.5k events/s), a Python+uvloop relay adds ≈ 0.9 ms at p99, comparable to Node. That is small against the 15–40 ms inter-token intervals being measured.
- At ≈ 10k events/s, every single-process runtime develops multi-millisecond tails (Python Δp99 ≈ +4.3 ms, Node ≈ +8.4 ms). Scaling out across processes (`SO_REUSEPORT`) is the fix, not a language change.
- The client (also Python, decrypting TLS) shares the host, so these deltas are upper bounds on relay-added latency.
- Probe lesson: asyncio holds only weak references to tasks. The first relay run lost tasks to garbage collection at 64 streams until strong references were kept, and the production shaper must do the same (IMPLEMENTATION_PLAN §11.2).

---

## 12. Novelty matrix

| Proposed contribution | Closest prior work | What is different in Crosstalk | Classification |
|---|---|---|---|
| Topic/sensitive-topic leakage from TLS record sizes and timing on a self-hosted stack | Whisper Leak, NetEcho, Weiss, Carlini & Nasr | None. It is calibration (E0) | **PRIOR ART** (REPRODUCTION) |
| An in-path, open, policy-driven streaming shaper | Cloudflare `p` (closed), OpenAI/Azure `obfuscation` (closed), NetShaper (not LLM-aware), Wiretapping's reference service | Open implementation at the operator's last hop, several defense families, LLM/SSE-aware, client-compatibility classes | **ENGINEERING CONTRIBUTION** |
| Three-layer measurement (emission → record → packet) with independent validation of each defense's on-wire effect | HotNets '25 argument; WFDefProxy for Tor | Applied to LLM stream defenses in a serving path; quantifies emission→record fidelity and record→packet coalescing per defense | **METHODOLOGICAL CONTRIBUTION** |
| Static vs adaptive attacker reported as a pair, paired grouped statistics, retained-leakage ratio | WF practice; Whisper Leak/NetEcho train within defended data | Explicit pair on identical held-out families with cluster-bootstrap paired CIs | **METHODOLOGICAL CONTRIBUTION** |
| Naturalistic vs length-matched workload regimes | No LLM traffic paper among those reviewed reports a length-matched control | Quantifies how much leakage is "just length" | **METHODOLOGICAL CONTRIBUTION** |
| Serving load, continuous batching and stream interval as factors for leakage **and** defense cost/effectiveness | Time Will Tell (batch 1; deferred), Wiretapping (sequential), cache-contention preprint (API-client, cache only) | Controlled concurrency sweep on a real engine with paired arms | **POSSIBLE RESEARCH NOVELTY** (becomes stronger only if the effect is material and robust) |
| Simulation-vs-deployment delta for LLM stream defenses | WFDefProxy (Tor) | Compare Whisper-Leak-style trace simulation with the deployed defense on the same prompts | **POSSIBLE RESEARCH NOVELTY** (P1) |
| Leakage-vs-cost frontier | Carlini & Nasr (one curve), Wiretapping (five points), NetShaper (DP knob) | Frontier across families under load, adaptive attacker on the y-axis | **METHODOLOGICAL CONTRIBUTION** (the frontier is a presentation of C/B, not novelty by itself) |
| Prefix-cache timing visible to a passive observer | InputSnatch, PromptPeek, Early Bird, Auditing; vLLM `cache_salt` | Passive vantage under load | **NOT WORTH PURSUING** as a flagship; optional E4 |
| Autoscaling / cold-start fingerprinting | None found | Detecting a cold start is not by itself a meaningful privacy harm | **NOT WORTH PURSUING** as a privacy result; keep as a confounder/failure scenario |
| NSGA-II policy search | Generic MOO; padding theory [R40] | Optimiser over measured runs | **NOT WORTH PURSUING** before substantial measured data (P3) |
| "Leakage in bits" headline | WeFDE, DeepSE-WF, Cherubin | — | **NOT WORTH PURSUING** as a headline (§9.5) |

No item is classified STRONG RESEARCH NOVELTY. That label would require results we do not have.

## 13. The remaining gap (conservative statement)

> Existing work contains isolated attack results and isolated mitigation measurements for encrypted LLM streams. These include deployed provider padding (Whisper Leak), defenses implemented in reference services with measured overheads (Wiretapping LLMs), and trace-level simulations (Whisper Leak, NetEcho). **After reviewing the works in §2–§6, we found no study that evaluates several deployable defense families in the real serving path with independently validated wire observation, reports static and defense-aware retrained attackers as a pair under grouped statistics, jointly measures latency, bandwidth, throughput and CPU, and varies serving load and batching as experimental factors.** The serving-load dimension is the least covered: every LLM streaming study we found either ran at batch size 1 / sequentially or measured uncontrolled commercial load.

This statement is to be re-checked before any write-up. New preprints appear monthly (two relevant ones appeared in July and September 2026).

---

## 14. Cloud and networking implementation facts

| Fact | Label | Source |
|---|---|---|
| GCP VPC default MTU is 1,460 bytes (settable 1,300–8,896). Packets > 1,600 bytes leaving the VPC are dropped | VERIFIED | [DOC] VPC MTU [R85] |
| External passthrough NLB does **not** terminate TLS ("The passthrough Network Load Balancer cannot perform this task") and preserves source/destination IPs and ports | VERIFIED | [DOC] [R86] |
| Passthrough NLB uses **direct server return**: "Responses from the backend VMs go directly to the clients, not back through the load balancer" | VERIFIED | [DOC] [R86] |
| Packet Mirroring clones payloads and headers to collectors behind an internal passthrough NLB. It consumes the mirrored instance's bandwidth, and intranode Pod traffic needs Intranode visibility. Google recommends NSI Packet Mirroring for new deployments | VERIFIED | [DOC] [R87] |
| GKE Autopilot (page updated 2026-09-18): default capabilities exclude `NET_RAW`, but it can be added in the Pod securityContext. `NET_ADMIN` is allowed with `--workload-policies=allow-net-admin`. No privileged containers (except Google partners), no `hostNetwork`, no host namespaces | VERIFIED | [DOC] [R88] |
| **INFERENCE:** Autopilot is unsuitable for the measurement baseline because node NIC offloads, host networking and node-level capture are out of reach, not because capture capabilities are impossible | INFERENCE | — |
| g2-standard-4 (1× L4, 4 vCPU, 16 GiB) ≈ US$0.71/h on demand in us-central1 | UNVERIFIED (third-party calculators) | [R89] — confirm in the GCP pricing console before any campaign |
| Terraform GCS backend supports state locking; GitHub Actions authenticates to GCP via Workload Identity Federation (`google-github-actions/auth`) | VERIFIED (v1.0) | [DOC] [R90] |

## 15. Claims explicitly rejected

| v1.0 or folklore claim | Why rejected | Replacement |
|---|---|---|
| "Nobody has published what those defenses buy or cost" | Wiretapping, Carlini & Nasr, Spec Spills, Time Will Tell, Whisper Leak and PromptEcho report costs | Conservative gap statement (§13) |
| "Adaptive attacker evaluation has never been applied to LLM streaming defenses" | Whisper Leak and NetEcho train on defended data (Wiretapping very likely) | "No static-vs-adaptive pair reported under grouped statistics" |
| "First measured leakage-versus-cost Pareto frontier" | Partial frontiers exist (Carlini & Nasr curve; Wiretapping points; NetShaper DP knob) | Frontier across families under load |
| `SSE event → one write() → one TLS record → one TCP segment → one packet` | Runtime-dependent write→record mapping; TCP coalescing/splitting; offloads | Three observation layers, empirically validated (§1.3) |
| "Vantage A and Vantage B see the same bytes" | Same TLS byte stream behind L4 passthrough, but packetization, retransmission and timing differ by vantage; with DSR the response path does not traverse the LB at all | Vantage invariants/differences table (PRODUCT.md §6) |
| "GKE Autopilot drops `CAP_NET_RAW`/`CAP_NET_ADMIN`, which kills capture" | Both can be enabled | §14 |
| "GC pauses would invalidate timing measurements, so the shaper must be Rust" | Unmeasured assertion; the observer sees the whole system; Day-0 probe shows ms-scale tails in Python/Node | Measure, then decide (Day-1 gate) |
| "Go's dynamic record sizing is a reason to use Rust" | Irrelevant under the language constraint; Go is not a candidate | — |
| "Dummy events are ignored by clients" | OpenAI SDKs yield `data:` events to applications | Comment-line cover traffic only for deployment claims (§5.2) |
| "Whisper Leak's adaptivity is not determinable" | Mitigated results come from classifiers trained on mitigated data | §2.2 |
| "NetEcho: padded traces still leak ~80%+ via Trace A" | Loose paraphrase | §2.4 precise numbers |
| "Leakage in bits makes defense claims attacker-independent" | MI estimation limits [R54] | Exploratory only |
| "Hot-reloadable policies per run" | Adds nondeterminism to provenance | Fixed policy set per session, selected per request by a fixed-width header in lab mode (IMPLEMENTATION_PLAN.md) |
| "Autoscaling/cold-start signatures are a flagship privacy result" | Detecting a cold start is not a privacy harm by itself | Confounder / failure scenario only |
| "BigQuery/Pub/Sub/Cloud SQL are needed" | Data volumes fit Parquet + DuckDB on one host; orchestration fits one Postgres | Introduce only when a measured constraint appears |

---

## 16. References

**LLM network side channels**
- [R1] Weiss, Ayzenshteyn, Amit, Mirsky. *What Was Your Prompt? A Remote Keylogging Attack on AI Assistants.* USENIX Security 2024 [PR]. https://www.usenix.org/conference/usenixsecurity24/presentation/weiss · arXiv:2403.09751
- [R2] McDonald, Bar Or. *Whisper Leak: a side-channel attack on Large Language Models.* arXiv:2511.03675, 2025 [PP]. https://arxiv.org/abs/2511.03675
- [R3] Li, Cui, Chen, Lin, Gu, Deng, Xu, Li. *From Length to Content: Token-Length Side-Channel Attacks on LLM API Merged Outputs.* USENIX Security 2026 [PR]. https://www.usenix.org/conference/usenixsecurity26/presentation/li-sijia · artifact https://doi.org/10.5281/zenodo.20441510
- [R4] Zhang, Wu, Deng, Wang, Zhang. *NetEcho: From Real-World Streaming Side-Channels to Full LLM Conversation Recovery.* arXiv:2510.25472, 2025 [PP]. https://arxiv.org/abs/2510.25472
- [R5] Carlini, Nasr. *Remote Timing Attacks on Efficient Language Model Inference.* arXiv:2410.17175, 2024 [PP]. https://arxiv.org/abs/2410.17175
- [R6] Soleimani, Jia, Gim, Lee, Khandelwal. *Wiretapping LLMs: Network Side-Channel Attacks on Interactive LLM Services.* IACR ePrint 2025/167 [PP]. https://eprint.iacr.org/2025/167
- [R7] Zhang, Saileshwar, Lie. *Time Will Tell: Timing Side Channels via Output Token Count in Large Language Models.* arXiv:2412.15431, 2024 [PP]. https://arxiv.org/abs/2412.15431
- [R8] Wei, Abdulrazzag, Zhang, Muursepp, Saileshwar. *When Speculation Spills Secrets: Side Channels via Speculative Decoding in LLMs.* arXiv:2411.01076 [PP]. https://arxiv.org/abs/2411.01076
- [R9] Majidi, Mireshghallah, Taram. *Leaky Language Models: Stealing Architecture and Inference Optimizations via Per-Token Timing.* arXiv:2607.20723, 2026 [PP]. https://arxiv.org/abs/2607.20723
- [R10] Jeong, Teymoorianfard, Kumar, Houmansadr, Bagdasarian. *Network-Level Prompt and Trait Leakage in Local Research Agents.* arXiv:2508.20282 [PP] (future extension only). https://arxiv.org/abs/2508.20282

**Cache and serving timing**
- [R11] Zheng et al. *InputSnatch.* arXiv:2411.18191 [PP]. https://arxiv.org/abs/2411.18191
- [R12] Song et al. *The Early Bird Catches the Leak.* arXiv:2409.20002 [PP]. https://arxiv.org/abs/2409.20002
- [R13] Wu et al. *I Know What You Asked: Prompt Leakage via KV-Cache Sharing in Multi-Tenant LLM Serving.* NDSS 2025 [PR]. https://www.ndss-symposium.org/ndss-paper/i-know-what-you-asked-prompt-leakage-via-kv-cache-sharing-in-multi-tenant-llm-serving/
- [R14] Gu, Li, Kuditipudi, Liang, Hashimoto. *Auditing Prompt Caching in Language Model APIs.* ICML 2025 [PR]. https://arxiv.org/abs/2502.07776
- [R15] Chu et al. *Selective KV-Cache Sharing to Mitigate Timing Side-Channels in LLM Inference (SafeKV).* arXiv:2508.08438 [PP]. https://arxiv.org/abs/2508.08438
- [R16] Pennas, Papaioannou, Guarnieri, Doudali. *PrefixWall.* arXiv:2603.10726 [PP]. https://arxiv.org/abs/2603.10726
- [R17] Abu Bakar. *Characterizing Contention-Induced Reliability Collapse in KV-Cache Timing Side Channels for Multi-Tenant LLM Serving.* arXiv:2609.06853, 2026 [PP]. https://arxiv.org/abs/2609.06853
- [R18] vLLM. *[Core] Prevent side-channel attacks via cache salting* (PR #17045) and *Automatic Prefix Caching* [DOC]. https://github.com/vllm-project/vllm/pull/17045 · https://docs.vllm.ai/en/stable/design/prefix_caching/

**Classical traffic analysis: attacks**
- [R19] Song, Wagner, Tian. *Timing Analysis of Keystrokes and Timing Attacks on SSH.* USENIX Security 2001 [PR].
- [R20] Chen, Wang, Wang, Zhang. *Side-Channel Leaks in Web Applications.* IEEE S&P 2010 [PR].
- [R21] Wright, Ballard, Coull, Masson, Monrose. *Spot Me if You Can.* IEEE S&P 2008 [PR].
- [R22] Schuster, Shmatikov, Tromer. *Beauty and the Burst.* USENIX Security 2017 [PR].
- [R23] Panchenko et al. *Website Fingerprinting at Internet Scale (CUMUL).* NDSS 2016 [PR].
- [R24] Hayes, Danezis. *k-fingerprinting.* USENIX Security 2016 [PR].
- [R25] Sirinam, Imani, Juarez, Wright. *Deep Fingerprinting.* CCS 2018 [PR]. https://arxiv.org/abs/1801.02265
- [R26] Shen et al. *Subverting Website Fingerprinting Defenses with Robust Traffic Representation.* USENIX Security 2023 [PR].

**Classical traffic analysis: defenses**
- [R27] Dyer, Coull, Ristenpart, Shrimpton. *Peek-a-Boo, I Still See You: Why Efficient Traffic Analysis Countermeasures Fail.* IEEE S&P 2012 [PR]. https://dl.acm.org/doi/10.1109/SP.2012.28
- [R28] Cai et al. *Tamaraw.* CCS 2014 [PR].
- [R29] Juarez et al. *WTF-PAD.* ESORICS 2016 [PR].
- [R30] Wang, Goldberg. *Walkie-Talkie.* USENIX Security 2017 [PR].
- [R31] Gong, Wang. *Zero-delay Lightweight Defenses against Website Fingerprinting (FRONT).* USENIX Security 2020 [PR].
- [R32] Holland, Hopper. *RegulaTor.* PETS 2022 [PR].
- [R33] Gong et al. *Surakav.* IEEE S&P 2022 [PR].
- [R34] Shen et al. *Palette.* IEEE S&P 2024 [PR].
- [R35] Holland, Carpenter, Oh, Hopper. *DeTorrent.* PETS 2024 [PR].
- [R36] Cherubin, Hayes, Juarez. *Website Fingerprinting Defenses at the Application Layer (ALPaCA).* PETS 2017 [PR].
- [R37] Pulls, Witwer. *Maybenot.* WPES 2023 [PR]. https://arxiv.org/abs/2304.09510 · Mullvad DAITA [IND] https://mullvad.net/en/blog/introducing-defense-against-ai-guided-traffic-analysis
- [R38] Sabzi, Vora, Goswami, Seltzer, Lécuyer, Mehta. *NetShaper: A Differentially Private Network Side-Channel Mitigation System.* USENIX Security 2024 [PR]. https://www.usenix.org/conference/usenixsecurity24/presentation/sabzi
- [R39] Mehta et al. *Pacer.* arXiv:1908.11568 [PP].
- [R40] Reed, Reiter. *Optimally Hiding Object Sizes with Constrained Padding.* arXiv:2108.01753 [PP].
- [R41] Wright, Coull, Monrose. *Traffic Morphing.* NDSS 2009 [PR].
- [R42] Liu, Cheng, Liu, Wang. *Rate-Distortion Function for Encrypted Traffic Side-Channel Defense.* arXiv:2607.17889, 2026 [PP]. https://arxiv.org/abs/2607.17889

**Methodology and statistics**
- [R43] Juarez, Afroz, Acar, Diaz, Greenstadt. *A Critical Evaluation of Website Fingerprinting Attacks.* CCS 2014 [PR].
- [R44] Cherubin. *Bayes, not Naïve: Security Bounds on Website Fingerprinting Defenses.* PETS 2017 [PR].
- [R45] Li, Guo, Hopper. *Measuring Information Leakage in Website Fingerprinting Attacks and Defenses (WeFDE).* CCS 2018 [PR].
- [R46] Veicht, Renggli, Barradas. *DeepSE-WF.* arXiv:2203.04428 [PP].
- [R47] Cherubin, Jansen, Troncoso. *Online Website Fingerprinting.* USENIX Security 2022 [PR].
- [R48] Arp et al. *Dos and Don'ts of Machine Learning in Computer Security.* USENIX Security 2022 [PR]. https://www.usenix.org/system/files/sec22summer_arp.pdf
- [R49] Gong, Zhang, Zhang, Wang. *WFDefProxy.* arXiv:2111.12629, 2021 [PP]. https://arxiv.org/abs/2111.12629
- [R50] Witwer, Holland, Hopper. *Padding-only Defenses Add Delay in Tor.* WPES 2022 [PR]. https://arxiv.org/abs/2208.02917
- [R51] Lavrentieva, Juarez, Honda. *Rethinking the Role of Network Stacks for Website Fingerprinting Defenses.* HotNets 2025 [PR]. https://doi.org/10.1145/3772356.3772428
- [R52] Mathews, Holland, Oh, Rahman, Hopper, Wright. *SoK: A Critical Evaluation of Efficient Website Fingerprinting Defenses.* IEEE S&P 2023 [PR]. https://ieeexplore.ieee.org/document/10179289/
- [R53] Siby, Juarez, Diaz, Vallina-Rodriguez, Troncoso. *Encrypted DNS ⇒ Privacy? A Traffic Analysis Perspective.* NDSS 2020 [PR].
- [R54] McAllester, Stratos. *Formal Limitations on the Measurement of Mutual Information.* AISTATS 2020 (PMLR 108:875–884) [PR]. https://proceedings.mlr.press/v108/mcallester20a.html
- [R55] Saravanan, Berman, Sober. *Application of the hierarchical bootstrap to multi-level data in neuroscience.* NBDT 3(5), 2020 [PR]. https://nbdt.scholasticahq.com/article/13927
- [R56] Cawley, Talbot. *On Over-fitting in Model Selection and Subsequent Selection Bias in Performance Evaluation.* JMLR 11:2079–2107, 2010 [PR]. https://www.jmlr.org/papers/v11/cawley10a.html
- [R57] Holm. *A Simple Sequentially Rejective Multiple Test Procedure.* Scandinavian Journal of Statistics 6(2), 1979 [PR].
- [R58] Efron, Tibshirani. *An Introduction to the Bootstrap.* Chapman & Hall, 1993 [book].
- [R59] Witwer. *State Machine Frameworks for Website Fingerprinting Defenses: Maybe Not.* arXiv:2310.10789 [PP].

**Serving systems**
- [R60] Yu et al. *Orca: A Distributed Serving System for Transformer-Based Generative Models.* OSDI 2022 [PR].
- [R61] Kwon et al. *Efficient Memory Management for Large Language Model Serving with PagedAttention.* SOSP 2023 [PR].
- [R62] vLLM documentation and source, checked 2026-09-19 [DOC]: serve CLI https://docs.vllm.ai/en/latest/cli/serve/ · OpenAI-compatible server https://docs.vllm.ai/en/latest/serving/online_serving/openai_compatible_server/ · metrics https://docs.vllm.ai/en/latest/design/metrics/ · request schema https://github.com/vllm-project/vllm/blob/main/vllm/entrypoints/openai/chat_completion/protocol.py · PyPI https://pypi.org/project/vllm/

**Industry and client behaviour**
- [R63] Cloudflare. *Mitigating a token-length side-channel attack in our AI products.* 2024-03-14 [IND]. https://blog.cloudflare.com/ai-side-channel-attack-mitigated/
- [R64] OpenAI. *Chat Completions streaming events* (`obfuscation`, `stream_options.include_obfuscation`) [DOC]. https://developers.openai.com/api/reference/resources/chat/subresources/completions/streaming-events
- [R65] Microsoft Security Blog. *Whisper Leak: A novel side-channel attack on remote language models.* 2025-11-07 [IND]. https://www.microsoft.com/en-us/security/blog/2025/11/07/whisper-leak-a-novel-side-channel-cyberattack-on-remote-language-models/
- [R66] openai-python `src/openai/_streaming.py` (main) and openai-node `src/core/streaming.ts` (master), read 2026-09-19 [DOC]. https://github.com/openai/openai-python · https://github.com/openai/openai-node
- [R67] WHATWG. *HTML Living Standard — Server-sent events, event stream interpretation* [STD]. https://html.spec.whatwg.org/multipage/server-sent-events.html#event-stream-interpretation

**Standards**
- [R68] RFC 8446 — TLS 1.3 [STD]. https://www.rfc-editor.org/rfc/rfc8446
- [R69] RFC 9112 — HTTP/1.1 [STD]. https://www.rfc-editor.org/rfc/rfc9112
- [R70] RFC 9113 — HTTP/2 [STD]. https://www.rfc-editor.org/rfc/rfc9113
- [R71] RFC 9000 (QUIC), RFC 9001 (QUIC-TLS), RFC 9114 (HTTP/3) [STD].
- [R72] RFC 9457 — Problem Details for HTTP APIs [STD].
- [R73] RFC 9293 — Transmission Control Protocol [STD]. https://www.rfc-editor.org/rfc/rfc9293

**Runtime and proxy documentation / source**
- [R74] CPython 3.13: `Lib/asyncio/sslproto.py`, `Lib/selectors.py`, `Doc/library/ssl.rst` [DOC]. https://github.com/python/cpython/tree/3.13
- [R75] Python 3.14 What's New (GC revert in 3.14.5) [DOC]. https://docs.python.org/3.14/whatsnew/3.14.html
- [R76] aiohttp `aiohttp/http_writer.py` (master) [DOC]. https://github.com/aio-libs/aiohttp
- [R77] uvicorn `uvicorn/protocols/http/httptools_impl.py` (master) [DOC]. https://github.com/encode/uvicorn
- [R78] Node.js v24: `src/crypto/crypto_tls.cc`, `doc/api/http.md`, `doc/api/http2.md` [DOC]. https://github.com/nodejs/node/tree/v24.x
- [R79] Linux kernel `Documentation/networking/ip-sysctl.rst`, `segmentation-offloads.rst` [DOC]. https://github.com/torvalds/linux/tree/master/Documentation/networking
- [R80] Wireshark wiki. *CaptureSetup/Offloading* [DOC]. https://wiki.wireshark.org/CaptureSetup/Offloading
- [R81] PyPI/GitHub metadata for uvloop, dpkt, pyshark, h2, aioquic (read 2026-09-19) [DOC].
- [R82] Envoy `api/envoy/extensions/filters/http/ext_proc/v3/processing_mode.proto` [DOC]. https://github.com/envoyproxy/envoy
- [R83] nginx `ngx_http_proxy_module` [DOC]. https://nginx.org/en/docs/http/ngx_http_proxy_module.html
- [R84] OpenResty `lua-nginx-module` README [DOC]. https://github.com/openresty/lua-nginx-module

**Cloud**
- [R85] Google Cloud. *Maximum transmission unit* [DOC]. https://docs.cloud.google.com/vpc/docs/mtu
- [R86] Google Cloud. *Passthrough Network Load Balancer overview* [DOC]. https://docs.cloud.google.com/load-balancing/docs/passthrough-network-load-balancer
- [R87] Google Cloud. *Packet Mirroring* [DOC]. https://docs.cloud.google.com/vpc/docs/packet-mirroring
- [R88] Google Cloud. *GKE Autopilot security capabilities* (updated 2026-09-18) [DOC]. https://docs.cloud.google.com/kubernetes-engine/docs/concepts/autopilot-security
- [R89] Third-party price calculators for g2-standard-4 (Holori, Vantage) — UNVERIFIED; confirm at https://cloud.google.com/compute/gpus-pricing
- [R90] HashiCorp. *Terraform GCS backend* [DOC] https://developer.hashicorp.com/terraform/language/backend/gcs · `google-github-actions/auth` [DOC] https://github.com/google-github-actions/auth

---

*End of RESEARCH.md. Product and research decisions derived from this evidence are in `PRODUCT.md`; engineering execution is in `IMPLEMENTATION_PLAN.md`.*
