# RESEARCH.md — Crosstalk

**Literature, industry and open-source review for**
**CROSSTALK — Measuring Privacy Leaks in Encrypted AI Traffic and Testing Defenses on Google Cloud**

Compiled: 13 September 2026. All sources checked against primary documents (paper PDFs/HTML, official docs, vendor engineering blogs, repository READMEs) on that date.

---

## 0. How to read this document

### 0.1 Claim discipline

Every substantive statement carries one of four tags. **Nothing in Crosstalk's public write-up may promote a claim to a higher tier without new evidence.**

| Tag | Meaning |
|---|---|
| **[VERIFIED]** | Directly supported by a primary source read during this review. The number/claim appears in the paper, official documentation, or vendor engineering post cited on the same line. |
| **[INFERENCE]** | A conclusion we draw from verified evidence. Logically supported but *not* stated by any source. |
| **[PROPOSAL]** | Something Crosstalk intends to build or do. Not a claim about the world. |
| **[UNVERIFIED]** | An open question requiring our own experiment, or a claim found only in secondary sources (news, vendor comparison blogs) that we could not confirm against a primary source. |

### 0.2 Reading order

- §1 — What the LLM-traffic attack literature has established (the state of the art we must not re-invent).
- §2 — The adjacent cache/timing-channel literature (different threat model; matters for one of our experiments).
- §3 — Classical encrypted-traffic analysis: 25 years of attacks, defenses, and — most importantly — *evaluation methodology*.
- §4 — Protocol-level padding facilities that actually exist in TLS/HTTP/QUIC/DNS.
- §5 — Industry: what vendors shipped, what confidential computing does and does not do, who sells the attack.
- §6 — Open-source landscape with reuse verdicts.
- §7 — The gap matrix and where Crosstalk sits.
- §8 — Full reference list with URLs.

---

## 1. LLM streaming traffic side channels

### 1.1 Summary table

| # | Work | Venue / Year | Observer | Signal | Headline result | Code |
|---|---|---|---|---|---|---|
| 1 | What Was Your Prompt? (GPT_Keylogger) | USENIX Sec '24 | Passive network | Per-token TLS record sizes → token *character lengths* | Reconstructs 29% of responses; topic from 55% | MIT |
| 2 | Whisper Leak | Microsoft, arXiv Nov 2025 | Passive network | TLS record sizes + inter-arrival times | >98% AUPRC topic detection on many of 28 models | MIT |
| 3 | PromptEcho ("From Length to Content") | USENIX Sec '26 | Passive network | *Merged* multi-token chunk lengths | Topic for 42.5% of sessions; 25.5% responses at cos>0.9 | Not found |
| 4 | NetEcho | arXiv Oct 2025 | Passive network | Token-count-per-chunk **and** char-length-per-chunk | ~70% conversation recovery across 7 real deployment scenarios | Not stated |
| 5 | Remote Timing Attacks on Efficient LM Inference | Carlini & Nasr, arXiv Oct 2024 | Passive + active network | Inter-token *timing* from speculative/parallel decoding | 90%+ topic precision (open-source); 100% message distinction on Claude 3 | Not found |
| 6 | Wiretapping LLMs | Yale, ePrint 2025/167 | Passive network | Packet size + timing under speculative decoding | 71–92% private-attribute inference (vLLM); 50–90% (ChatGPT) | Not stated |
| 7 | Time Will Tell | U. Toronto, arXiv Dec 2024 | Passive network | *Total response time* → output token count | 75–83% translation-language ASR; 74.7% GPT-4o class leak | Not found |
| 8 | When Speculation Spills Secrets | arXiv Nov 2024 / OpenReview | Network or co-tenant | Per-iteration accepted-token counts / packet sizes | Fingerprints 1-of-50 prompts: REST 100%, BiLD 95.2%, LADE 91.6%, EAGLE 77.6% | Not found |

### 1.2 Paper-by-paper

---

#### [1] What Was Your Prompt? A Remote Keylogging Attack on AI Assistants

- **Authors** [VERIFIED]: Roy Weiss, Daniel Ayzenshteyn, Guy Amit, Yisroel Mirsky — Ben-Gurion University of the Negev.
- **Venue** [VERIFIED]: 33rd USENIX Security Symposium (2024), pp. 3367–3384. Preprint arXiv:2403.09751.
- **Threat model** [VERIFIED]: "Eve" eavesdrops on encrypted traffic between user and assistant, positioned "within the LAN of Bob or somewhere in the internet infrastructure." No decryption, no endpoint access.
- **Mechanism** [VERIFIED]: Vendors streamed **one token per SSE message**. Token character length is therefore recoverable from the encrypted record size:
  - Independent messages: `tᵢ = |packetᵢ| − h` where `h` is fixed header overhead.
  - Cumulative messages: `tᵢ = |mᵢ| − |mᵢ₋₁|`.
  - Paper notes QUIC/HTTP3 overhead of 28 bytes/packet with 1200-byte max payload.
- **Attack pipeline** [VERIFIED]: token-length sequence → heuristic sentence segmentation (split where `tᵢ=1`, i.e. punctuation; merge segments <10 tokens; list patterns like `(3,1,1)` for `":\n\n1."`) → two fine-tuned T5 models: `LLM_A` for the first segment `p(R₁|T₁)`, `LLM_B` for subsequent segments `p(Rᵢ|Tᵢ, Rᵢ₋₁)` using previous-sentence context. Vocabulary replaced by special length tokens (`_5`, `_8`, …).
- **Data** [VERIFIED]: UltraChat (1.5M GPT-4-Turbo dialogues); 570k general inquiries used; 550k train / 10k val / 10k test. Average 12.57 sentences/response, 17.5 tokens/sentence.
- **Metrics** [VERIFIED]: sentence-transformer cosine similarity φ; **φ > 0.5 counted as "topic captured."** Also ROUGE-1/L, edit distance.
- **Results** [VERIFIED]:

  | Configuration | ASR (φ>0.5) | φ>0.9 | Exact (φ=1.0) |
  |---|---|---|---|
  | First segment (ideal conditions) | 54.52% | 28.91% | 16.03% |
  | Entire response (average) | 37.96% | 5.49% | 0.34% |
  | ChatGPT-4, real traffic | 38.21% | 15.64% | 4.57% |
  | Microsoft Copilot, real traffic | 40.87% | 17.42% | 7.96% |
  | Baseline (GPT-4 guessing from lengths) | ~0.07% | — | — |

  Abstract-level headline is "29% of responses reconstructed, topic inferred from 55%." [INFERENCE] The abstract figures and the table figures are different slices (abstract aggregates differently from per-configuration tables); **cite the table, not the abstract, when precision matters.**
- **Training-distribution sensitivity** [VERIFIED]: a model trained on C4 achieved 5% ASR versus 54.5% for the ChatGPT-style-trained model — the attack depends heavily on modelling the *target's writing style*.
- **Transferability** [VERIFIED]: GPT-4-trained attacker reached 40.87% on Copilot, which the authors attribute to "consistency in their construction of responses."
- **Vendor status at publication** [VERIFIED]: vulnerable — OpenAI (ChatGPT/API), Microsoft Copilot, Writesonic, Anthropic Claude, Notion, ClickUp, TextCortex. **Google Bard/Gemini was not vulnerable**, attributed to batching. Perplexity and Poe listed as uncertain.
- **Defenses proposed** [VERIFIED]: random padding; token grouping (multiple tokens/message); full batch response. Explicitly framed as a usability/latency trade-off with no quantitative cost analysis.
- **Code** [VERIFIED]: `github.com/royweiss1/GPT_Keylogger`, MIT, Python, ~66 stars. **Does not operate on PCAPs** — token sequences must be extracted externally. No capture tooling.

**Why it matters to Crosstalk** [INFERENCE]: this is the canonical "size channel" result and the reason `obfuscation`/`p` padding fields exist. It is also the clearest demonstration that the *finest* granularity (one token per record) is what makes reconstruction possible — which is why the modern question is about *merged* chunks, not single tokens.

---

#### [2] Whisper Leak: a side-channel attack on Large Language Models

- **Authors** [VERIFIED]: Geoff McDonald, Jonathan Bar Or (Microsoft). arXiv:2511.03675, Nov 2025. Accompanying Microsoft Security blog, 7 Nov 2025.
- **Threat model** [VERIFIED]: passive network adversary — ISP, government, or local network observer (explicitly "coffee shop WiFi") — who sees TLS but cannot decrypt.
- **Task** [VERIFIED]: **binary** detection of one sensitive topic ("legality of money laundering") against background traffic.
- **Data collection** [VERIFIED]:
  - 100 semantically similar target prompt variants generated by LLM paraphrasing; **80 for train/val, 20 held out per trial**.
  - 100 repetitions per variant (100×100).
  - 11,716 negatives from the Quora Question Pairs dataset, queried once each.
  - Space-insertion perturbations applied to every query to defeat server-side caching.
  - Temperature 1.0. Queries shuffled and interspersed.
  - Capture: **`tcpdump` on Ubuntu clients**; features are "application data record sizes (derived from TLS record lengths) and the inter-arrival times."
- **Models covered** [VERIFIED] — 28 across OpenAI, Microsoft/Azure, Google, Mistral, xAI, DeepSeek, Alibaba, Anthropic, Lambda Labs, Groq, Amazon.
- **Features/preprocessing** [VERIFIED]: zero-pad to the 95th-percentile sequence length for that model; for the BERT variant, discretise sizes and times into **50 bins each** → `[LEN_0..49]`, `[TIME_0..49]`, ≤255 tokens per feature stream; LightGBM gets flattened vectors; LSTM gets 32-dim embeddings per feature.
- **Classifiers** [VERIFIED]: LightGBM (5,000 estimators, lr 0.02, early-stop patience 40); Bi-LSTM + attention (2×128, attn dim 64, MLP [128,64], dropout 0.3, lr 2e-4); fine-tuned DistilBERT (differential LRs 2e-4 base / 2e-2 head). **5 trials with different random seeds per model.**
- **Results** [VERIFIED]:
  - Median AUPRC (best feature combination): `mistral-large` 100.0%, `xai-grok-3-mini-beta` 99.9%, `groq-llama-4-maverick` 99.7%. Worst: `amazon-nova-lite-v1` 71.2%.
  - Across all 28: **size-only average 93.3%**, **time-only average 69.7%**. *Size is the dominant channel; timing alone is materially weaker but far above chance.*
  - At a simulated 10,000:1 noise-to-target ratio, **17 of 28 models achieved 100% precision at 5–20% recall.**
- **Batching correlates with lower leakage** [VERIFIED]: least-vulnerable models are those already emitting many tokens per SSE event — Amazon Nova (pro 77.5%, lite 71.2%), Google Gemini (1.5-flash 83.5%, 2.5-pro 84.0%; 17.7–55.3 tokens/event), Anthropic Claude-3-Haiku (91.2%, 6.0 tokens/event) — versus a 1.0 tokens/event baseline elsewhere. The paper describes this protection as **"incomplete."**
- **Mitigation experiments** [VERIFIED, with an important caveat]:

  | Mitigation | Model | Baseline AUPRC | Mitigated | Δ |
  |---|---|---|---|---|
  | Random padding (real, provider-side `obfuscation`) | microsoft-gpt-4o-mini | 97.5% | 92.9% | −4.6 pp |
  | Token batching, k=5 (simulated on traces) | openai-gpt-4o-mini | 98.2% | 94.8% | −3.4 pp |
  | Packet injection (simulated on traces) | openai-gpt-4o-mini | 98.1% | 93.3% | −4.8 pp |

  - Batching simulation method [VERIFIED]: "grouping packets into fixed-size batches of N. For each batch, the inter-packet arrival times and data lengths are **summed** to form a single, larger packet."
  - Injection parameters [VERIFIED]: intervals sampled from `𝒩(μ_dataset/k, σ)` with `injections_per_mean=2.0`, `injection_stddev_multiplier=2.0`.
  - Injection cost [VERIFIED]: **2–3× traffic volume**. No latency cost is reported for any mitigation.
  - **Adaptive-attacker status** [UNVERIFIED]: the padding experiment used "new parallel data collections … capturing traffic both with and without obfuscation enabled," but the paper does not present an explicit static-attacker vs. retrained-attacker comparison. Whether the reported post-mitigation AUPRC reflects a *defense-aware* attacker is **not determinable from the text**. This ambiguity is a direct motivation for Crosstalk's core experiment.
- **Scaling** [VERIFIED]: an extended run of 121,111 captures at 9.4:1 noise ratio; "attack effectiveness continues improving with more training data, suggesting current results may underestimate real-world risk."
- **Vendor response** [VERIFIED, Microsoft blog]: OpenAI and Microsoft Azure added an `obfuscation` field of variable-length random text; Mistral added a `p` parameter; xAI deployed protections.
- **Code** [VERIFIED]: `github.com/yo-yo-yo-jbo/whisper_leak`, MIT, Python 3.10+, ~82 stars, ~275 commits, drivers for 40+ services, `tcpdump`-based capture, LGBM/LSTM/BERT/CNN training scripts, benchmark harness.

**Why it matters to Crosstalk** [INFERENCE]: this is the single most directly comparable prior work and the best-engineered public artifact. Three of its properties define our opportunity: (a) one topic, binary, so generalisation across topics/tasks is untested; (b) two of three mitigations are *trace transformations*, not deployed systems, so no latency/throughput cost exists in the record; (c) adaptivity is unaddressed.

---

#### [3] From Length to Content: Token-Length Side-Channel Attacks on LLM API Merged Outputs (PromptEcho)

- **Authors** [VERIFIED]: Sijia Li, Tianyu Cui, Miao Chen, Xinjie Lin, Zheyuan Gu (Zhongguancun Laboratory); Xinhao Deng (Tsinghua University); Ke Xu, Qi Li (Tsinghua University & Zhongguancun Laboratory).
- **Venue** [VERIFIED]: USENIX Security '26.
- **Core claim** [VERIFIED]: token aggregation does **not** eliminate the channel; it creates a *merged* token-length side channel where encrypted chunk sizes expose the character-length pattern of a token *group*. The attack performs "semantics-aware decomposition of merged token lengths" using LLM linguistic priors and chain-of-thought to resolve uncertain token boundaries.
- **Results** [VERIFIED]: on real API traffic from OpenAI GPT-4o and DeepSeek-V3 — **topic correctly inferred for 42.5% of sessions**; **25.5% of responses reconstructed at cosine similarity > 0.9**.
- **Artifacts** [UNVERIFIED]: no public code located; the USENIX PDF endpoint returned HTTP 403 to automated fetching, so all figures above come from the USENIX abstract page and indexed abstract text rather than the full PDF. **Re-verify against the PDF before citing in a report.**

**Why it matters** [INFERENCE]: this is the direct refutation of "just batch tokens and you're fine." Any batching defense Crosstalk implements must be evaluated against an attacker that models merged lengths, not one that assumes one token per chunk.

---

#### [4] NetEcho: From Real-World Streaming Side-Channels to Full LLM Conversation Recovery

- **Authors** [VERIFIED]: Zheng Zhang, Guanlong Wu, Sen Deng, Shuai Wang, Yinqian Zhang — Southern University of Science and Technology; Hong Kong University of Science and Technology. arXiv:2510.25472, Oct 2025.
- **Most valuable contribution for us** [VERIFIED]: a taxonomy of **seven real deployment scenarios** and what defense each does or does not inherit:

  | Scenario | Backend | Defense present | Example |
  |---|---|---|---|
  | A | Open-source w/ multi-token prediction | None | DeepSeek web chatbot |
  | B | OpenAI-style API | Passive batching 1–3 tokens | DeepSeek-v3 API |
  | C | Proprietary vendor chatbot | Active: batching + padding to K bytes | Vendor chat UIs |
  | D | OpenAI-style API | Passive batching ~2 tokens | GPT/Gemini APIs |
  | E | Self-hosted app over upstream API | Active upstream only — **no downstream coverage** | Glass.health, Harvey AI |
  | F | Third-party proxy with padding | Upstream padding only | Cloudflare LLM app |
  | G | vLLM/SGLang w/ speculative decoding | Passive, dynamic token counts | FastChat, Vercel AI |

- **Two-trace decomposition** [VERIFIED]: **Trace A** = token count per packet group (from arrival intervals and size multiples); **Trace B** = character length per token group (packet size minus fixed metadata, ~102–264 bytes depending on context). Padding attacks Trace B; **Trace A survives**.
- **Quantified defense failure** [VERIFIED]: with simulated padding, traces "still leak ~80%+ via Trace A." **20% dummy-packet injection produced only ~2% accuracy reduction.**
- **The deployment-gap finding** [VERIFIED]: "Scenario E & G show upstream defenses ineffective for downstream frontends (e.g., Next.js cannot inherit OpenAI padding)." Also: "Only giant vendors implement padding" because of protocol-modification cost.
- **Attack pipeline** [VERIFIED]: offline probing (~5K–10K queries) to build ⟨trace, text⟩ pairs → dual-tower contrastive retrieval (MiniLM text tower + LSTM trace tower, InfoNCE) → reconstruction by a local open LLM (DeepSeek-v2-lite / Qwen3-32B) with **logits processing constrained to match observed token/group lengths** → prompt recovery by a remote reasoning LLM → up to 3 rounds of iterative refinement by re-probing with recovered candidates.
- **Results** [VERIFIED]: medical 65% response recovery (NED) / 80% prompt recovery; legal 80% / 55%; topic classification Top-1 **73.3%–98.2%** across scenarios A–G; attack success (≥50% similarity) averaging 95%.
- **Economics** [VERIFIED]: **$55 total to attack 1,200 victims** ($40 probing + ~$5 refinement + GPU time).
- **Robustness** [VERIFIED]: tested to 10% packet loss/reordering — topic recovery stays >80%, full recovery degrades. Cross-tokenizer transfer costs ~10%. Out-of-distribution recovery ~50% of in-distribution.
- **Code** [VERIFIED]: not mentioned in the paper.

**Why it matters** [INFERENCE]: NetEcho establishes (i) that timing-derived token *counts* are an independent channel that padding does not touch, and (ii) that **the defense must live at the last hop to the user**, not upstream. Both are load-bearing for Crosstalk's architecture: our shaper sits at the edge the client actually talks to, and our defense catalogue must attack Trace A (timing/counts) as well as Trace B (sizes).

---

#### [5] Remote Timing Attacks on Efficient Language Model Inference

- **Authors** [VERIFIED]: Nicholas Carlini, Milad Nasr. arXiv:2410.17175, Oct 2024.
- **Insight** [VERIFIED]: efficiency techniques (speculative sampling, parallel/lookahead decoding, Medusa, SpecInfer, Speculative Streaming, consistency LLMs) make generation time **data-dependent** — acceptance rate rises with predictable text — so inter-token delays leak content.
- **Threat models** [VERIFIED]: passive (observe inter-packet delays on HTTPS) and **active** (adversary controls part of the prompt — a chosen-plaintext analogue).
- **Measurement detail that matters** [VERIFIED]: **Claude 3 clusters multiple tokens per TCP packet**, so the authors had to use packet-*size* analysis to infer token counts before computing inter-token delays. Size and timing are not separable channels in practice.
- **Methods** [VERIFIED]: Gaussian Mixture Models fit on 100 queries/condition; CNNs for the web-UI setting (~79–82% topic accuracy).
- **Results** [VERIFIED]:
  - Open-source passive: **89.5%–99.8%** distinguishing medical vs. coding topics.
  - GPT-3.5: 94.7% accuracy distinguishing easy vs. hard queries. GPT-4-turbo "twice as fast" on easy queries.
  - **Claude 3: 100%** accuracy after compensating for token clustering.
  - ChatGPT language identification: **49% top-1 across 10 languages** (German, Russian, Chinese leak most).
  - Active 1-of-1000: perfect classification using 100 adversary-controlled suffixes.
  - Secret extraction: 97.2% / 99.3% success eliciting capability gaps, enabling partial recovery of secrets such as phone numbers.
- **Defense evaluated** [VERIFIED]: **constant output rate.** Transmitting every 80 ms added **4–13 ms latency per token with <10% bandwidth overhead**. Protocol morphing was discussed and rejected as impractical. The authors note defenses are more viable here than in classical traffic analysis because required timing precision is high and inter-response variance is modest.
- **Stability** [VERIFIED]: timing signatures degrade over weeks for GPT models; Claude remained stable.

**Why it matters** [INFERENCE]: this is the strongest existing evidence that a *timing-only* defense (constant rate) is both necessary and surprisingly cheap in bandwidth. It gives Crosstalk a concrete, citable baseline configuration (80 ms constant rate) to reproduce and to place on our cost frontier.

---

#### [6] Wiretapping LLMs: Network Side-Channel Attacks on Interactive LLM Services

- **Authors** [VERIFIED]: Mahdi Soleimani, Grace Jia, In Gim, Seung-seob Lee, Anurag Khandelwal — Yale University. IACR ePrint 2025/167, Feb 2025.
- **Claim** [VERIFIED]: server-side optimizations including speculative decoding introduce input-dependent packet timing/size variations; network adversaries learn sensitive content from encrypted prompts and responses.
- **Results** [VERIFIED]: **71–92%** accuracy predicting private attributes on vLLM; **50–90%** on ChatGPT. Application domains: medicine and finance.
- **Defenses** [VERIFIED]: stated to exist with security/performance trade-offs (interactivity, bandwidth); no quantitative frontier given.
- **Code** [UNVERIFIED]: not mentioned.

---

#### [7] Time Will Tell: Timing Side Channels via Output Token Count in LLMs

- **Authors** [VERIFIED]: Tianchen Zhang, Gururaj Saileshwar, David Lie — University of Toronto. arXiv:2412.15431, Dec 2024.
- **Mechanism** [VERIFIED]: autoregressive decoding makes time-per-output-token roughly constant, so **total response time ⇒ output token count**. Pearson r > 0.987 for locally served models (Tower, M2M100, MBart50, Gemma2); **r = 0.370 for GPT-4o** due to server-side variation and network congestion, requiring multi-day profiling and concurrent TTFT/TPOT sampling to compensate.
- **Serving setup** [VERIFIED]: translation models on AWS `g6.xlarge` (Oregon); classification models locally on RTX A6000; GPT-4o via API.
- **Results** [VERIFIED]: translation-language ASR — Tower 82.5% local / 83.2% remote-timing, M2M100 77.3% / 75.8%, MBart50 80.6% / 81.3%. Classification — GPT-4o 86.9% local, **74.7% remote timing**; Gemma2-9B 81.4–82.9%.
- **Mitigations with costs** [VERIFIED]: prompt-level fixed-length output — GPT-4o ASR dropped 26% (to 61.9%) but Gemma2 negligible. System-level padding to maximum length — **Tower +93% latency**, M2M100/MBart50 +13% average; plaintext padding requirement 67%–108%.
- **Limitations acknowledged** [VERIFIED]: assumes batch size 1; continuous batching adds noise; speculative decoding may break the linear relation.

**Why it matters** [INFERENCE]: this paper's own stated limitation — batch size 1, no continuous batching — is precisely one of Crosstalk's research questions. The fact that a top-venue result explicitly brackets production serving conditions is strong evidence the gap is real and recognised.

---

#### [8] When Speculation Spills Secrets: Side Channels via Speculative Decoding in LLMs

- **Authors** [VERIFIED]: Jiankun Wei, Abdulrahman Abdulrazzag, Tianchen Zhang, Adel Muursepp, Gururaj Saileshwar. arXiv:2411.01076; OpenReview.
- **Mechanism** [VERIFIED]: input-dependent patterns of correct/incorrect speculation are inferable "by monitoring per-iteration token counts **or packet sizes**."
- **Results** [VERIFIED], 1-of-50 prompt fingerprinting on research prototypes and production-grade vLLM, temperature 0.3: REST 100%, LADE 91.6%, BiLD 95.2%, EAGLE 77.6%. At temperature 1.0: REST 99.6%, LADE 61.2%, BiLD 63.6%, EAGLE 24% (random baseline 2%). Confidential datastore contents leaked at >25 tokens/sec.
- **Mitigations proposed** [VERIFIED]: packet padding and **iteration-wise token aggregation**.

---

### 1.3 Adjacent: agent traffic

**Network-Level Prompt and Trait Leakage in Local Research Agents** [VERIFIED] — Hyejun Jeong, Mohammadreza Teymoorianfard, Abhinav Kumar, Amir Houmansadr, Eugene Bagdasarian (UMass). arXiv:2508.20282, Aug 2025, revised Jan 2026.
- Threat model: passive observers including DNS resolvers, ISPs, VPNs, proxies, firewalls.
- Agents visit **70–140 domains per request**; from visited IPs, domain sequences and request timing the attack recovers **73% of the prompt's functional and domain knowledge** and up to **19 of 32 latent user traits** across sessions.
- Mitigation (constraining domain diversity / obfuscating traces) reduces effectiveness by an average of 29%.
- Code: `umass-aisec/wra` [VERIFIED].

[INFERENCE] Agentic traffic is a *richer* channel than chat streaming because DNS/SNI/destination sets are visible in plaintext. Crosstalk deliberately excludes it from the core (different capture surface, different defenses) but it is the single strongest future extension.

---

## 2. Adjacent literature: cache and timing channels with an API-client adversary

**Threat model differs.** In §1 the adversary is a *passive observer of someone else's connection*. In this section the adversary is a *legitimate API client* who measures their own request latency to learn about other tenants. Crosstalk's threat model is §1. This section matters for exactly one reason, stated at the end.

| Work | Venue | Mechanism | Key result |
|---|---|---|---|
| **InputSnatch** (Xinyao Zheng et al., arXiv:2411.18191) | Nov 2024 | Prefix-cache and semantic-cache hit/miss timing; Input Constructor + Time Analyzer | Up to **62%** success stealing medical data; up to **100%** identifying legal topics [VERIFIED] |
| **The Early Bird Catches the Leak** (Linke Song, Zixuan Pang, Wenhao Wang, Zihao Wang, XiaoFeng Wang, Hongbo Chen, Wei Song, Yier Jin, Dan Meng, Rui Hou) | arXiv:2409.20002, accepted IEEE TIFS | KV-cache and semantic-cache timing; classification models to detect cache hits | Recovers system prompts and shared prefixes; black-box validated on deployed services [VERIFIED] |
| **I Know What You Asked / PromptPeek** (Guanlong Wu, Zheng Zhang, Yao Zhang, Weili Wang, Jianyu Niu, Ye Wu, Yinqian Zhang — SUSTech + ByteDance) | NDSS 2025 | Probing SGLang's radix-tree KV cache; TTFT fingerprint correlation | **99–100% ASR** reconstructing prompts token-by-token [VERIFIED] |
| **Auditing Prompt Caching in Language Model APIs** (Chenchen Gu, Xiang Lisa Li, Rohith Kuditipudi, Percy Liang, Tatsunori Hashimoto — Stanford) | ICML 2025 | Statistical hypothesis tests on TTFT | **Global cache sharing across users in 7 API providers including OpenAI**; also revealed OpenAI's embedding model is decoder-only [VERIFIED] |
| **SafeKV** (Kexin Chu et al., arXiv:2508.08438) | Aug 2025 / rev. Feb 2026 | Selective KV-cache sharing with sensitivity-aware eviction | Cuts TTFT overhead vs. full isolation by up to 40.58%; up to 2.66× throughput [VERIFIED] |
| **PrefixWall** (Panagiotis Georgios Pennas, Konstantinos Papaioannou, Marco Guarnieri, Thaleia Dimitra Doudali, arXiv:2603.10726) | Mar 2026 | Monitors cross-user reuse, selectively isolates prefixes | Up to 70% higher cache reuse, 30% lower latency than full isolation [VERIFIED] |
| **MoEcho** (Ruyi Ding, Tianhong Xu, Xinyi Shen, Aidong Adam Ding, Yunsi Fei) | CCS 2025 | **Microarchitectural**: cache occupancy, Pageout+Reload, GPU perf counters, TLB Evict+Reload | Out of scope — requires local co-location, not network observation [VERIFIED] |

**The one reason this matters to Crosstalk** [INFERENCE + PROPOSAL]: automatic prefix caching is **on by default in vLLM's V1 engine** [VERIFIED, vLLM docs], and a cache hit shortens TTFT by a large margin (vLLM documentation and community reporting describe 60–80% TTFT reductions for long shared system prompts [UNVERIFIED — secondary source]). TTFT is directly observable on the wire as the gap between request completion and first response record. The cache literature measures this from the *API client's* seat; nobody has measured whether it survives to a *passive network observer* behind a load balancer under concurrent load. That is a cheap, well-posed, genuinely open experiment for Crosstalk (see PRODUCT.md §13, experiment E4).

---

## 3. Classical encrypted-traffic analysis (1996–2026)

This is the field Crosstalk borrows its *methodology* from. The LLM papers are 2 years old; this literature is 25 years old and has already made — and corrected — most of the mistakes available.

### 3.1 Foundational leaks: the same physics, older protocols

| Work | Venue | Result |
|---|---|---|
| **Timing Analysis of Keystrokes and Timing Attacks on SSH** — Dawn Song, David Wagner, Xuqing Tian | USENIX Security 2001 | SSH interactive mode sends **each keystroke in its own packet**, leaking inter-keystroke timing; block ciphers pad only to 8-byte boundaries, revealing approximate data size [VERIFIED] |
| **Side-Channel Leaks in Web Applications** — Shuo Chen, Rui Wang, XiaoFeng Wang, Kehuan Zhang | IEEE S&P 2010 | Over HTTPS, inferred illnesses, medications, surgeries, family income, investment activity; search autocomplete grows the request by **one byte per keystroke** [VERIFIED] |
| **Spot Me if You Can** — Charles Wright, Lucas Ballard, Scott Coull, Gerald Masson, Fabian Monrose | IEEE S&P 2008 | Variable-bitrate codecs leak phonetic content; phrase spotting at **50% average accuracy, >90% for some phrases** [VERIFIED] |
| **Beauty and the Burst** — Roei Schuster, Vitaly Shmatikov, Eran Tromer | USENIX Security 2017 | MPEG-DASH segmentation creates content-dependent **bursts**; CNNs identify streams; feasible even for a JavaScript ad on a nearby machine [VERIFIED] |

[INFERENCE] The LLM token-length channel is structurally identical to the SSH keystroke channel and the VBR-codec channel: a **variable-size, content-dependent unit emitted in its own transport unit, in real time**. Every defense idea that worked or failed there transfers.

### 3.2 Website fingerprinting attacks — the attack ladder

| Attack | Venue | Representation | Headline |
|---|---|---|---|
| **CUMUL** — Panchenko et al. | NDSS 2016 | Cumulative sum of ±packet lengths, interpolated to 100 points + 4 totals = **104 features**, RBF-SVM | 90–93% on 100 sites [VERIFIED] |
| **k-fingerprinting (k-FP)** — Jamie Hayes, George Danezis | USENIX Security 2016 | **175 features** incl. packets-per-second timing; random forest used as an *embedding* (leaf encoding) feeding k-NN | **85% TPR at 0.02% FPR** against 30 monitored hidden services in a 100,000-page open world [VERIFIED] |
| **Deep Fingerprinting (DF)** — Sirinam, Imani, Juarez, Wright | CCS 2018 | 1-D CNN over direction sequence | **>98%** undefended; **>90% against WTF-PAD**; 49.7% against Walkie-Talkie [VERIFIED] |
| **Robust Fingerprinting (RF)** — Meng Shen, Kexin Ji, Zhenbo Gao, Qi Li, Liehuang Zhu, Ke Xu | USENIX Security 2023 | Traffic Aggregation Matrix (TAM) representation | Subverts existing defenses [VERIFIED] |

**Design lesson for Crosstalk** [INFERENCE]: the two representations that have survived a decade are (a) the **cumulative signed-size curve** (CUMUL) and (b) the **raw direction/size sequence into a 1-D CNN** (DF). Both are trivially applicable to SSE chunk streams. A gradient-boosted model over k-FP-style summary statistics is the right cheap baseline. This is why Crosstalk's attack ladder is LR → GBDT(CUMUL+k-FP features) → 1-D CNN, and why a Transformer is unnecessary until the CNN saturates.

### 3.3 Defenses — and what they actually cost

| Defense | Venue | Mechanism | Measured overhead | Outcome |
|---|---|---|---|---|
| **Traffic Morphing** — Wright, Coull, Monrose | NDSS 2009 | Convex optimization to morph one traffic class into another | Lower than padding | **Broken**: Dyer et al. got 98% with *total bytes alone* [VERIFIED] |
| **BuFLO** — Dyer, Coull, Ristenpart, Shrimpton | IEEE S&P 2012 | Fixed-size packets at fixed intervals | **>400% bandwidth** to push accuracy to 5% | 27% attack accuracy at k=128 [VERIFIED] |
| **Tamaraw** — Cai, Nithyanand, Wang, Johnson, Goldberg | CCS 2014 | Constant rate per direction, pad to multiple of L | **142% data, 30% time** (measured) | Provable-ish baseline, impractical [VERIFIED] |
| **WTF-PAD** — Juarez, Imani, Perry, Diaz, Wright | ESORICS 2016 | Adaptive padding, histogram-driven, fills gaps | **0 latency, <60% bandwidth** | 91%→20% vs. 2016 attacks; **then broken to >90% by DF** [VERIFIED] |
| **Walkie-Talkie** — Tao Wang, Ian Goldberg | USENIX Security 2017 | Half-duplex browsing + burst molding so sensitive/non-sensitive pages look alike | **31% bandwidth, 34% time** | Held DF to 49.7% [VERIFIED] |
| **FRONT / GLUE** — Jiajun Gong, Tao Wang | USENIX Security 2020 | Zero-delay random dummy padding concentrated at trace front; GLUE merges traces | FRONT **33% data, zero latency**; GLUE 22–44% | FRONT beats WTF-PAD at similar cost [VERIFIED] |
| **RegulaTor** — James K. Holland, Nicholas Hopper | PETS 2022 | Regularised decaying send-rate surges | — | Exploits common web-traffic shape [VERIFIED] |
| **Surakav** — Gong et al. | IEEE S&P 2022 | GAN-generated realistic send patterns regulating buffered data | **55% data, 16% time** | TPR −57%; 42% less data than FRONT [VERIFIED] |
| **Palette** — Shen et al. | IEEE S&P 2024 | Cluster similar sites, regularise each cluster to a common pattern | — | Attack accuracy **−73.6%** avg; +33.5–43.5 pp over prior defenses; deployed as a Tor Pluggable Transport [VERIFIED] |
| **DeTorrent** — Holland, Carpenter, Oh, Hopper | PETS 2024 | Two competing neural networks generate padding-only defenses | Padding-only, no delay | Accuracy −61.5%; FC TPR at 1e-5 FPR ≈ 0.12; **deployed live alongside Tor** [VERIFIED] |
| **ALPaCA / LLaMA** — Cherubin, Hayes, Juarez | PETS 2017 | **Server-side application-layer padding** of page objects; LLaMA is the client-side counterpart | D-ALPaCA needs **23.6× less bandwidth** than P-ALPaCA | Attack accuracy to **10%** [VERIFIED] |
| **Maybenot** — Tobias Pulls, Ethan Witwer | WPES 2023 | Framework: defenses as probabilistic state machines scheduling padding/blocking | — | Descends from Tor's circuit padding framework ← WTF-PAD ← Adaptive Padding; **shipped in Mullvad DAITA** [VERIFIED] |
| **Pacer** — Aastha Mehta, Mohamed Alzayat, Roberta de Viti, Björn Brandenburg, Peter Druschel, Deepak Garg | arXiv 1908.11568 | **Hypervisor-level** traffic shaping in IaaS clouds, respecting flow control and congestion signals | "moderate" | First end-to-end cloud NSC elimination [VERIFIED] |

**Two structural lessons** [INFERENCE]:
1. **Every lightweight defense in this table was eventually broken by a stronger attacker trained against it.** WTF-PAD is the canonical case: 20% accuracy in 2016, >90% in 2018 against the same defense, because DF was trained on defended traces. *A defense evaluated only against a pre-existing attacker is not evaluated.*
2. **Server-side application-layer padding (ALPaCA) is the closest classical analogue to what an LLM provider can actually deploy** — and its key result was that a *distribution-aware* padding target (D-ALPaCA) is 23.6× cheaper in bandwidth than naive padding (P-ALPaCA) for the same protection. That is directly portable to token-chunk padding and is the intellectual ancestor of Crosstalk's bucket-padding policy.

### 3.4 Evaluation methodology — the part most student projects skip

| Work | Venue | What it establishes |
|---|---|---|
| **A Critical Evaluation of Website Fingerprinting Attacks** — Juarez, Afroz, Acar, Diaz, Greenstadt | CCS 2014 | Prior results rested on unrealistic assumptions; browsing habits, location and client version materially change accuracy; prior work **succumbs to the base-rate fallacy** in open-world settings [VERIFIED] |
| **Bayes, not Naïve** — Giovanni Cherubin | PETS 2017 | A practical method to derive **security bounds for any defense** by estimating the Bayes error for a chosen feature set — a lower bound on error for *any* classifier the adversary may use [VERIFIED] |
| **Measuring Information Leakage in WF Attacks and Defenses (WeFDE)** — Li, Guo, Hopper | CCS 2018 | Mutual information between site and fingerprint, via adaptive kernel density estimation, over 3,043 features — **leakage expressed in bits** rather than one classifier's accuracy [VERIFIED] |
| **DeepSE-WF** — Alexander Veicht, Cedric Renggli, Diogo Barradas | arXiv:2203.04428 | kNN-based Bayes-error and mutual-information estimation **from learned latent features**, giving tighter estimates than hand-crafted-feature frameworks at an order of magnitude less compute [VERIFIED] |
| **Online Website Fingerprinting** — Giovanni Cherubin, Rob Jansen, Carmela Troncoso | USENIX Security 2022 | First evaluation on **genuine** Tor traffic in a true open world; Distinguished Paper; Internet Defense Prize 2nd [VERIFIED] |
| **Dos and Don'ts of Machine Learning in Computer Security** — Arp, Quiring, Pendlebury, Warnecke, Pierazzi, Wressnegger, Cavallaro, Rieck | USENIX Security 2022 | Catalogue of pitfalls (sampling bias, data snooping, spurious correlations, inappropriate baselines/metrics, base-rate neglect) found in 30 top-venue papers; actionable recommendations [VERIFIED] |

**Direct consequence for Crosstalk** [PROPOSAL]: we adopt (a) prompt-family-disjoint and time-disjoint splits as the default, not an option; (b) balanced accuracy + PR-AUC + precision-at-fixed-recall with bootstrap CIs over ≥5 seeds; (c) a Bayes-error/MI leakage estimate (DeepSE-WF-style kNN estimator) reported *alongside* classifier accuracy so that "our attacker was weak" is not a valid criticism of a defense claim; (d) an explicit label-shuffled null run as a contamination canary.

### 3.5 Simulation versus implementation — the finding that defines our engineering

| Work | Venue | Finding |
|---|---|---|
| **WFDefProxy** — Jiajun Gong, Wuqi Zhang, Charles Zhang, Tao Wang | arXiv:2111.12629 | Implemented FRONT, Tamaraw and Random-WT as real Tor pluggable transports. Simulation captured *effectiveness* correctly but **overhead badly**: FRONT ~23% **higher** data overhead than simulated; Tamaraw 28–45% **lower**; Tamaraw time overhead **21% measured vs. 51–242% previously simulated**. Causes: packet-delay assumptions, queuing, page-load stop conditions [VERIFIED] |
| **Padding-only defenses add delay in Tor** — Ethan Witwer, James Holland, Nicholas Hopper | arXiv:2208.02917, WPES | Padding-only defenses are widely assumed "zero-delay." Shadow simulations show that **network-wide deployment lengthens queues and adds delay**. Defenses should be evaluated with network-wide deployment simulations [VERIFIED] |
| **Rethinking the Role of Network Stacks for WF Defenses** — Elisaveta Lavrentieva, Marc Juarez, Michio Honda | HotNets 2025 | Defenses are designed over the packet sequence the attacker sees but **implemented at the application layer**, operating on data that has not yet traversed the network stack. Advocates integrating packet-sequence obfuscation into host network stacks [VERIFIED] |
| **State Machine Frameworks for WF Defenses: Maybe Not** — Ethan Witwer | arXiv:2310.10789 | Implemented three SOTA defenses in Maybenot; demonstrates the potential of state-machine frameworks but raises doubt about long-term feasibility; recommends enhancements before Arti commits to one [VERIFIED] |

**This is the single most important methodological import into Crosstalk** [INFERENCE]. Whisper Leak simulated batching by *summing packet sizes and inter-arrival times on captured traces*. WFDefProxy proves that this class of simulation gets overhead wrong by factors of 2–10× and misses queuing entirely. HotNets'25 proves that an application-layer defense does not control what the attacker actually observes, because the network stack re-frames it. Therefore: **Crosstalk implements defenses in a real proxy, in the path, and measures the result on the wire.** That is not gold-plating; it is the correction the literature explicitly asks for.

### 3.6 Encrypted DNS: proof that naive padding underdelivers

**Encrypted DNS ⇒ Privacy? A Traffic Analysis Perspective** — Sandra Siby, Marc Juarez, Claudia Diaz, Narseo Vallina-Rodriguez, Carmela Troncoso, NDSS 2020 [VERIFIED]. EDNS(0) padding "is not as effective as expected"; even with the recommended padding level, domain classification retains **F1 ≈ 0.45 — six orders of magnitude better than random guessing** — using 124× less data than HTTPS-flow attacks.

[INFERENCE] The precedent is exact: a standards-body-blessed padding policy, deployed, still leaves an attack far above chance. It is the strongest historical reason to doubt that `obfuscation`-style random padding is sufficient for LLM streams.

### 3.7 Padding theory

**Optimally Hiding Object Sizes with Constrained Padding** — Andrew C. Reed, Michael K. Reiter, arXiv:2108.01753 [VERIFIED]. Gives algorithms for **privacy-optimal padding schemes that minimise the observer's information gain from the padded size**, subject to a bounded padding-overhead constraint, in three regimes: per-object padding (same padded copy each time), per-request padding (re-padded per request), and the case where the object store has no known query distribution.

[INFERENCE] This is the formal foundation for Crosstalk's `pad-bucket` policy and for the claim that bucket selection should be *distribution-aware*, not uniform-random. It also supplies the right framing for the budget controller: minimise information gain subject to an overhead constraint.

---

## 4. Protocol-level padding: what actually exists

| Layer | Facility | Status |
|---|---|---|
| **TLS 1.3** | Record padding — arbitrary zero padding inside the encrypted record (RFC 8446 §5.4) | Exists. In OpenSSL: `SSL_CTX_set_block_padding()` / `SSL_set_block_padding()` pad records to a multiple of `block_size`; `SSL_CTX_set_record_padding_callback()` gives per-record control. **TLS 1.3 write path only**, and **incompatible with Kernel TLS** [VERIFIED, OpenSSL docs] |
| **TLS (Rust)** | rustls record padding | **[UNVERIFIED]** — no documentation of a record-padding API was found. Assume unavailable; verify before designing around it |
| **HTTP/2** | `DATA` frame `PADDED` flag (RFC 9113 §6.1) | Exists in the protocol; server-library exposure varies [UNVERIFIED for specific Rust/Go stacks] |
| **QUIC** | `PADDING` frames (RFC 9000 §19.1) | Exists |
| **DNS** | EDNS(0) Padding with a recommended policy (RFC 8467): clients pad queries to a multiple of **128 octets**, servers pad responses to a multiple of **468 octets**; 468 chosen so three blocks fit a typical MTU. A Random-Block-Length variant is also described [VERIFIED] |
| **Go TLS stack** | **Dynamic record sizing**: `crypto/tls` sends small records (bounded by `tcpMSSEstimate` = 1280 − 40 − 32 bytes) until ~1 MB has been written on the connection, then boosts to maximum record size; disable with `DynamicRecordSizingDisabled` [VERIFIED] |

**Two engineering consequences** [INFERENCE]:
1. Application-layer padding (a JSON field like `obfuscation` / `p`) is what every vendor actually shipped, because it requires no TLS-stack change and survives any intermediary. Crosstalk's primary padding policies must therefore be application-layer to be *comparable to industry*. TLS record padding is a legitimate secondary arm (OpenSSL-backed) that tests whether pushing the defense down a layer buys anything.
2. **Go's dynamic record sizing is a confound**, not a defense: a Go-implemented gateway would silently change observed record sizes as a function of *bytes written so far on the connection*, which correlates with response length. Any experiment run through a Go TLS server must either disable it or report it. This is a concrete, verified reason to prefer Rust for the shaper and to document the TLS write path precisely.

---

## 5. Industry and startup landscape

### 5.1 What vendors actually shipped against this attack

| Vendor | Mitigation | Detail | Status |
|---|---|---|---|
| **Cloudflare** (Workers AI, AI Gateway) | Application-layer random padding | Added a `"p"` property of **variable random length** to each SSE JSON object, e.g. `data: {"response":"portugal","p":"abcdefghijklmnopqrstuvwxyz0123456789a"}`. Automatic, no customer action. Response to a Ben-Gurion bug-bounty report, March 2024 | [VERIFIED, Cloudflare blog] |
| **OpenAI** | `obfuscation` field | Official API reference: *"An obfuscation string added to normalize the size of streamed chunks as a mitigation to certain side-channel attacks. The field is included by default and omitted when `stream_options.include_obfuscation` is `false`."* Deployed August 2025 per Whisper Leak | [VERIFIED, OpenAI docs] |
| **Microsoft Azure** | `obfuscation` field | Same mechanism | [VERIFIED, Microsoft blog] |
| **Mistral** | `p` parameter | Equivalent effect | [VERIFIED, Microsoft blog] |
| **xAI** | Unspecified protections | Deployed | [VERIFIED, Microsoft blog] |
| **Google (Gemini), Amazon (Nova), Anthropic (Claude)** | **Token batching as a side effect**, not an announced defense | Whisper Leak measured 17.7–55.3 tokens/event (Gemini), 6.0 (Claude-3-Haiku) vs. 1.0 baseline, and correspondingly lower AUPRC | [VERIFIED, Whisper Leak] |
| **Anyone else** | Nothing found | NetEcho: "Only giant vendors implement padding" | [VERIFIED] |

**Critical observation** [VERIFIED via NetEcho scenarios E/F/G]: these are all **upstream** mitigations. An application that calls OpenAI from its own Next.js backend and re-streams to its users **does not inherit** the `obfuscation` padding — the app's own downstream leg is unprotected. Every self-hosted or fine-tuned deployment (vLLM, SGLang, Ollama, TGI) has no mitigation at all.

[INFERENCE] **The unprotected surface is enormous and growing**: every self-hosted LLM, every AI gateway that re-streams, every RAG app, every agent framework. And there is no reusable, measured, open implementation of a downstream defense. That is Crosstalk's product wedge.

### 5.2 Confidential computing — and why it is NOT the answer

| Company / product | What it sells | Layer protected |
|---|---|---|
| **Google Cloud Confidential GKE Nodes / Confidential VMs** | Memory encryption for nodes; GPU workloads on A3 + NVIDIA H100 with Intel TDX (`a3-highgpu-1g`, single H100, PCIe traffic encrypted and integrity-protected). Confidential Space GA on C3/Intel TDX [VERIFIED] | Data **in use**, vs. the infrastructure operator |
| **Anthropic** | "Confidential inference via trusted virtual machines" whitepaper (June 2025, with Pattern Labs), extending weight protection to prompt privacy and output non-disclosure; fallback architecture bridging CPU enclave to accelerators without native support [VERIFIED] | Data in use + model weights |
| **Edgeless Systems (Privatemode AI, Continuum, Contrast, Constellation)** | End-to-end confidential LLM service on NVIDIA H100/Blackwell with remote attestation; claims no third party including Edgeless can access prompts [VERIFIED] | Data in use |
| **Tinfoil** | Verifiably private AI via secure enclaves + NVIDIA confidential GPUs; SDK fetches and verifies the attestation report before connecting; OpenAI-compatible API; Tinfoil Containers [VERIFIED] | Data in use |
| **Opaque Systems** | "Confidential AI" platform; $24M at $300M valuation (Feb 2026); acquired MPC/FHE and post-quantum tech from TII (May 2026) [VERIFIED] | Data in use + governance |
| **Fortanix / Anjuna** | Confidential AI for on-prem AI factories (model IP protection); Anjuna Seaglass hardware-enforced isolation across clouds [VERIFIED] | Data in use |
| **Apple Private Cloud Compute** | Attested nodes, **Oblivious HTTP relay** (relay sees IP, cannot decrypt; node decrypts but cannot see IP), **target diffusion** with RSA blind-signature single-use credentials so requests cannot be routed to a chosen node [VERIFIED] | Data in use **+ identity/metadata linkage** |
| **Confident Security — CONFSEC / OpenPCC** | Open-source framework for verifiably private inference; encrypted streaming, hardware attestation, unlinkable requests, OHTTP relay+gateway; **explicitly specifies an anonymization scheme against side channels including API timing, payment side channels and metadata analysis** [VERIFIED] | Data in use + metadata |

**The distinction Crosstalk must state clearly and repeatedly** [VERIFIED by construction + INFERENCE]:

> A TEE protects **the contents of memory from the machine operator**. It does not change **how many bytes leave the machine, or when**. An attested H100 enclave streaming one token per SSE event emits exactly the same packet-size and timing sequence as an unattested one. Confidential computing and traffic-analysis resistance are **orthogonal**; neither substitutes for the other.

Two industry designs are exceptions worth naming because they *do* treat metadata as in-scope: Apple PCC (OHTTP + target diffusion) and OpenPCC (explicit side-channel anonymization). Notably, neither claims to defeat *response-stream shape* analysis; they address **identity linkage** (who is asking), not **content inference** (what is being discussed). And empirically, relay-based identity protection does not confer traffic-analysis protection: **Investigating Traffic Analysis Attacks on Apple iCloud Private Relay** (Ali Zohaib, Jade Sheffey, Amir Houmansadr, AsiaCCS 2023) found Private Relay "highly vulnerable to state-of-the-art website fingerprinting and flow correlation attacks" [VERIFIED].

### 5.3 AI security vendors — securing content, not metadata

| Company | Status | What it secures |
|---|---|---|
| **HiddenLayer** | Independent as of 2026 [UNVERIFIED — secondary] | Model scanning, runtime detection & response, red teaming, agentic/MCP protection |
| **Protect AI** | Acquired by **Palo Alto Networks**, July 2025; folded into Prisma AIRS [UNVERIFIED — secondary] | MLSecOps, model supply chain |
| **Robust Intelligence** | Acquired by **Cisco**, October 2024; basis of Cisco AI Defense [UNVERIFIED — secondary] | Model validation, guardrails |
| **Lakera** | Acquired by **Check Point**; announced 16 Sep 2025, completed 11 Nov 2025 [VERIFIED, Check Point press release] | Prompt-injection defense, AI red teaming |
| **CalypsoAI** | Acquired by **F5** for **$180M**, closed 26 Sep 2025 [VERIFIED, F5 press release] | AI guardrails, red teaming |
| **Portkey** | Acquired by **Palo Alto Networks** for **$117M**, closed 29 May 2026 [VERIFIED, PANW press release] | AI Gateway → Prisma AIRS control plane |

[INFERENCE] The entire AI-security category is consolidating around **content-layer** threats — prompt injection, jailbreaks, data exfiltration via model output, agent authorization. **Not one of these products addresses network metadata leakage of encrypted inference traffic.** The category that *would* naturally own the fix (AI gateways) has been absorbed into platforms whose threat model is content. This is a genuine, documentable market gap, not a hypothetical one.

### 5.4 AI gateways — the natural deployment point for a defense

| Gateway | Model | Relevance |
|---|---|---|
| **LiteLLM** | Open-source self-hosted proxy; virtual keys, budgets, fallbacks | The reference for what a self-hosted LLM proxy does; **no traffic-shaping defense** |
| **Kong AI Gateway** | Extends Kong API GW to AI traffic | Enterprise ops model |
| **Envoy AI Gateway** | Envoy + Kubernetes primitives | Closest to Crosstalk's deployment shape |
| **Cloudflare AI Gateway** | Managed edge; one endpoint, many models | **The only one shipping a padding defense** (the `p` field) |
| **Portkey** | Managed; now PANW | — |
| **GKE Inference Gateway** (Gateway API Inference Extension, `kubernetes-sigs/gateway-api-inference-extension`) | `InferencePool` + Endpoint Picker routing on KV-cache utilization, queue length, LoRA adapters; reported 60% tail-latency reduction, up to 40% throughput gain [UNVERIFIED — vendor/secondary figures] | **Directly relevant**: it makes routing decisions from *cache state*, which is exactly the kind of infrastructure behaviour Crosstalk asks whether an observer can see |

[INFERENCE] A defense policy engine belongs in an AI gateway. Every gateway in the market already sits in the right place in the path, holds the streaming buffer, and is the last hop before the client. None implement shaping beyond Cloudflare's padding. Crosstalk's shaper is, architecturally, "the missing gateway plugin," and that framing makes the project legible to industry.

### 5.5 The other side: who sells encrypted-traffic inference

- **Cisco Encrypted Traffic Analytics** [VERIFIED]: extracts the **sequence of packet lengths and times (SPLT)**, TLS-specific features, and the initial data packet; classifies malware "without the need for bulk decryption." SPLT is defined as, for each of the first few packets carrying application payload, the payload length and the inter-arrival time from the previous packet.
- **NDR vendors** [UNVERIFIED — secondary]: the first Gartner Magic Quadrant for NDR (May 2025) named Vectra AI, Darktrace, ExtraHop and Corelight as Leaders; all market encrypted-traffic analysis without decryption (Corelight via Zeek/Suricata evidence, Vectra explicitly "never decrypts").

[INFERENCE] Cisco's SPLT is *the same feature vector* the academic attacks use, productised and shipped in routers since 2017. This is decisive evidence for a sceptical professor that "packet sizes and times are a usable signal" is engineering reality, not academic novelty. It also means Crosstalk's attacker features should be described in SPLT terms — it is the industry vocabulary.

### 5.6 VPN / anonymity network defenses in production

- **Mullvad DAITA** [VERIFIED]: "Defense against AI-guided Traffic Analysis," built on **Maybenot**, integrated into WireGuard. Mechanism: **constant packet sizes**, random background (cover) traffic, and pattern distortion. Mullvad funds Maybenot development; the framework is peer-reviewed and open access.
- **NymVPN / Nym mixnet** [VERIFIED]: 5-hop mixnet based on **Loopix**; Poisson mixing (per-hop exponentially distributed delay) plus continuous cover traffic.

[INFERENCE] DAITA is the closest production precedent for what Crosstalk's shaper does — a deployed, open-source, state-machine-driven traffic-shaping layer whose entire purpose is defeating ML traffic classifiers. It is also proof that this class of defense is shippable to real users at acceptable cost. The difference: DAITA protects a *tunnel* with no knowledge of application semantics; Crosstalk shapes a *stream whose application semantics it knows*, which should allow better protection per unit of overhead.

---

## 6. Open-source landscape and reuse verdicts

Verdicts: **REUSE** (take as a dependency) · **ADAPT** (port ideas/code with attribution) · **STUDY** (read, do not depend) · **AVOID**.

### 6.1 Attack / research artifacts

| Repo | License | State | Verdict |
|---|---|---|---|
| `yo-yo-yo-jbo/whisper_leak` | MIT, Python, ~82★, ~275 commits, active [VERIFIED] | tcpdump capture, 40+ service drivers, LGBM/LSTM/BERT/CNN training, benchmark harness, prompt packs (100 targets + 11,716 Quora negatives) | **STUDY + ADAPT.** Read the feature pipeline and mitigation harness carefully; reimplement ours. Optionally reuse their *prompt pack* as an external replication set to cross-check our numbers. Do not build on their codebase — our system is a platform, not a script. |
| `royweiss1/GPT_Keylogger` | MIT, Python, ~66★ [VERIFIED] | Two fine-tuned T5 models + eval; **no PCAP parsing** | **STUDY.** Reconstruction is a stretch goal, not the core. If we ever do reconstruction, this is the reference. |
| `umass-aisec/wra` | [VERIFIED to exist] | Local research-agent leakage | **STUDY.** Future-extension reference only. |
| `robust-fingerprinting/RF`, `deep-fingerprinting/df`, `kxdkxd/Palette`, `jkhollandjr/RegulaTor`, `jkhollandjr/PETS_DeTorrent`, `wtfpad/wtfpad`, `camelids/ALPaCA` | Various | Classical WF attacks/defenses | **STUDY.** Source of representations (TAM, DF CNN) and defense parameterisations to port to chunk-level. |
| `maybenot-io/maybenot` | **MIT OR Apache-2.0**, Rust, ~166★, ~324 commits, active; crates: `maybenot`, `maybenot-ffi`, `maybenot-simulator`, `maybenot-machines`, `maybenot-gen`, `maybenot-cli`; used by Mullvad DAITA [VERIFIED] | Probabilistic state machines scheduling padding/blocking | **ADAPT (P3).** Licence and language are ideal. Use as an *optional policy backend* for a state-machine defense arm so we can compare "anonymity-network-style machine" vs. "LLM-aware scheduler." Must not become the project. |

### 6.2 Capture and traffic tooling

| Tool | License | Verdict |
|---|---|---|
| `tcpdump`/`libpcap` | BSD | **REUSE** — ground-truth capture, dev loop |
| `tshark`/Wireshark | GPL-2 | **REUSE as a CLI tool** (not linked into our code) for TLS record dissection and cross-validation of our parser |
| **Zeek** | BSD | **STUDY.** Excellent for connection-level logs, but we need per-record size/time sequences; a purpose-built extractor is simpler than a Zeek script + deployment |
| **Suricata** | GPL-2 | **AVOID** — IDS signature matching is irrelevant to our threat model |
| `gopacket` / `pcap` crates / `scapy` / `dpkt` / `pyshark` | BSD / MIT / GPL-2 / BSD | **REUSE** — one of these for offline trace parsing |
| **NFStream** | LGPL-3 [UNVERIFIED — confirm before linking] | **STUDY.** Flow-level features; our features are finer-grained |
| **nPrint / nPrintML** (CCS 2021) | — | **STUDY.** Standardised packet representation for ML; good citation for "why a canonical representation matters" |
| **Cilium / Hubble** (GKE Dataplane V2 observability) | Apache-2 | **AVOID for features.** [VERIFIED, GCP docs] Hubble flow metrics "don't count the amount of data or number of packets transmitted" — L3/L4 flow metadata and NetworkPolicy verdicts only. **Insufficient for size/timing sequences.** Useful only for NetworkPolicy debugging. |
| **eBPF (direct)** | — | **P2.** A tc/socket-filter program emitting (ts, dir, len) is elegant and avoids pcap overhead, but adds kernel-version risk. Start with AF_PACKET/libpcap. |
| **Linux `tc` / `netem`** | GPL | **REUSE** — controlled latency/jitter/loss/bandwidth |
| **Toxiproxy** (MIT) / **Chaos Mesh** (Apache-2) | | **REUSE (P2)** — failure injection |

### 6.3 Serving, gateway, load generation

| Tool | Note | Verdict |
|---|---|---|
| **vLLM** | Apache-2. Verified: `--stream-interval` controls "number of tokens to batch into each streamed chunk" (per-request override clamped up to the server setting; first and last chunks always sent immediately); **automatic prefix caching on by default in V1**; TTFT histograms and speculative-decoding acceptance metrics exposed [VERIFIED, vLLM docs] | **REUSE — primary backend.** `--stream-interval` is a gift: it gives us a *model-server-side* batching defense to compare against our gateway-side one, for free. |
| **SGLang** | RadixAttention prefix cache (the target of PromptPeek) | **P3 alternative backend** for backend-disjoint generalisation |
| **Ollama / llama.cpp** | MIT | **REUSE — local dev backend.** Real SSE token streaming on a laptop with no GPU. |
| **LiteLLM** | MIT | **STUDY.** Reference for provider normalisation; we need a much thinner adapter |
| **Envoy** (`ext_proc`) | Apache-2 | **STUDY / AVOID as the shaper.** `ext_proc` gives body mutation but adds an extra hop and its own buffering, which perturbs the timing channel we are measuring. Purpose-built proxy is both simpler and more precise. |
| **NGINX / HAProxy** | BSD-2 / GPL-2 | **STUDY.** Neither exposes per-chunk write scheduling at the granularity we need |
| **k6** | **AGPL-3.0** [VERIFIED]; SSE via the `xk6-sse` extension [VERIFIED to exist] | **AVOID as a library**; acceptable as an external binary for a generic HTTP load smoke test. AGPL + our need for exact per-chunk timestamps and reproducible seeding makes a custom async generator better. |
| **Locust** | MIT | **STUDY.** Fine for RPS, poor for per-chunk arrival timestamping |
| **Custom async generator** | — | **BUILD.** We need seeded reproducibility, per-chunk receive timestamps at the client, and ground-truth logging keyed by `run_id`. ~400 lines of Python/`httpx` or Rust. |

### 6.4 Platform

| Tool | Note | Verdict |
|---|---|---|
| **Terraform** | **BUSL-1.1 for ≥ v1.6.0** [VERIFIED]. OpenTofu (MPL-2.0, Linux Foundation) is the open fork | **REUSE Terraform** for the project (student/educational use is unaffected by BUSL's competitive-use restriction) — **but document the licence explicitly** in the ADR; note OpenTofu as the drop-in alternative and that it additionally offers native state encryption |
| **Terraform GCS backend** | Supports **state locking**; Object Versioning "highly recommended"; KMS encryption supported with automatic migration [VERIFIED] | **REUSE** |
| **Helm** (Apache-2), **Argo CD** (Apache-2) | | **Helm REUSE; Argo CD P2** (GitHub Actions push-deploy is sufficient at our scale; Argo adds GitOps demonstrably but also a control plane to run) |
| **Prometheus / Managed Service for Prometheus, Grafana (AGPL-3), OpenTelemetry (Apache-2)** | | **REUSE** |
| **Optuna** | NSGA-II is the default multi-objective sampler; supports `constraints_func` where >0 means violated; constraint-aware Pareto plots [VERIFIED] | **REUSE** for the defense policy search (P2) |
| **XGBoost / LightGBM / scikit-learn / PyTorch** | Apache-2 / MIT / BSD-3 / BSD-3 | **REUSE** |
| **Trivy, Gitleaks, Semgrep, Syft/Grype, Cosign** | | **REUSE** in CI |

**Standing rule** [PROPOSAL]: Crosstalk is not a wrapper around anyone's repository. The shaper, the probe, the feature extractor, the experiment control plane, the workload engine, and the evaluation harness are ours. External code is used for capture primitives, model serving, ML libraries, and infrastructure — never for the contributions we claim.

---

## 7. Gap matrix

| # | Existing capability | Already solved? | Quality of existing work | Gap remaining | Crosstalk opportunity |
|---|---|---|---|---|---|
| 1 | Token-length → content reconstruction | **Yes** | Excellent (USENIX '24, USENIX '26) | None worth chasing | **Do not rebuild.** Cite; optionally replicate on our own stack as a sanity check |
| 2 | Packet size+time → topic classification | **Yes** | Excellent (Whisper Leak, 28 models, 5 seeds) | Single topic, binary, one task family | Multi-class, multi-task, with disjoint-split discipline |
| 3 | Timing → topic via speculative decoding | **Yes** | Excellent (Carlini & Nasr) | — | Cite; use their 80 ms constant-rate result as our baseline |
| 4 | Leakage under **production serving conditions** (continuous batching, concurrency, autoscaling, cold starts, cache hits) | **No** | Time Will Tell explicitly assumes batch size 1 and flags this as a limitation | **Wide open** | **Core contribution C4.** Concurrency sweep, HPA event visibility, prefix-cache TTFT channel behind an LB |
| 5 | Defenses for LLM streams | Partially | Proposed in a paragraph; two of Whisper Leak's three were **simulated by summing sizes/times on captured traces**; NetEcho simulates padding | No deployed, measured, open implementation | **Core contribution C1.** Real in-path shaper, measured on the wire |
| 6 | **Cost** of those defenses (TTFT, ITL, p95, throughput, $/1k req) | **No** | Only Carlini & Nasr (80 ms → +4–13 ms/token, <10% bw) and Time Will Tell (padding → +13–93% latency) give any numbers, for one policy each | No frontier exists | **Core contribution C3.** Empirical leakage-vs-cost Pareto frontier |
| 7 | **Defense-aware (adaptive) attacker** evaluation | **No** for LLM traffic; **Yes, mandatory** in WF literature since DF (2018) | Whisper Leak's adaptivity is not determinable from the text | Wide open | **Core contribution C2.** Static vs. retrained attacker, reported as a pair, for every defense |
| 8 | Simulation-vs-implementation gap | Established in WF (WFDefProxy: 21% vs 51–242% time overhead) | High quality, but Tor-specific | Never tested for LLM streams | Measure our own simulated-vs-deployed delta and report it |
| 9 | Leakage measured in **bits** (not one classifier's accuracy) | **Yes** in WF (WeFDE, DeepSE-WF, Cherubin bounds) | Mature | Never applied to LLM traffic | Port a kNN Bayes/MI estimator; makes defense claims attacker-independent |
| 10 | Defense **configuration search** under constraints | Padding theory exists (Reed & Reiter); GAN/adversarial defenses exist (Surakav, DeTorrent) | Strong theory, not for streaming LLM | No practical constrained search over measured runs | **Contribution C5.** Multi-objective search (NSGA-II) over *real measured* trials |
| 11 | Cache-timing channels | **Yes**, from the API-client seat (InputSnatch, PromptPeek, Early Bird, Auditing Prompt Caching) | Excellent | Whether it survives to a **passive observer** behind an LB under load is untested | Experiment E4 — bridges two literatures |
| 12 | Confidential computing for AI | **Yes**, commercially mature | Strong | **Does not address traffic metadata** | Use as the framing contrast; do not build |
| 13 | Vendor mitigations deployed | **Yes** (`obfuscation`, `p`, batching) | Shipped, undocumented in effect | No public measurement of their real-world efficacy or cost | Reproduce `obfuscation`-equivalent padding as our **industry baseline arm** and measure it honestly |
| 14 | Downstream/self-hosted deployments | — | NetEcho shows they inherit nothing | No reusable defense exists for them | The shaper is deployable artifact value beyond the paper |

### 7.1 Contribution classification (be honest in the report)

| Category | What belongs to Crosstalk |
|---|---|
| **Reproduced research** | Token-length/timing topic inference on a self-hosted stack; a Whisper-Leak-style binary detection as a calibration point |
| **Engineering contribution** | `crosstalk-shaper` — a pluggable, measured, in-path streaming-defense proxy; `crosstalk-probe` — a threat-model-enforcing capture path that never touches plaintext |
| **System contribution** | The experiment platform: reproducible run definitions, cross-layer correlation (app trace ↔ packet trace ↔ GPU/infra metrics), adaptive-attacker loop as first-class infrastructure |
| **Experimental contribution** | Leakage under production serving conditions (concurrency, batching, cache, autoscaling, LB tier, network impairment); static-vs-adaptive attacker deltas; measured leakage-vs-cost frontier |
| **Genuinely new idea (modest, defensible)** | Treating **serving-system configuration as an independent variable in a privacy experiment**, and constrained multi-objective search over *measured* defense configurations |
| **Speculative extension** | Agent/tool-call traffic leakage; TLS-record-layer vs application-layer defense comparison; cross-provider transfer of defended-traffic attackers |

**What Crosstalk must never claim**: a new attack; breaking TLS; that any defense is "secure"; any number that came from a simulation when a measurement was possible; or novelty for anything in rows 1–3 of the gap matrix.

---

## 8. References

### 8.1 LLM traffic and inference side channels
1. Weiss, Ayzenshteyn, Amit, Mirsky. *What Was Your Prompt? A Remote Keylogging Attack on AI Assistants.* USENIX Security '24. https://www.usenix.org/conference/usenixsecurity24/presentation/weiss · https://arxiv.org/abs/2403.09751 · code https://github.com/royweiss1/GPT_Keylogger
2. McDonald, Bar Or. *Whisper Leak: a side-channel attack on Large Language Models.* arXiv:2511.03675. https://arxiv.org/abs/2511.03675 · blog https://www.microsoft.com/en-us/security/blog/2025/11/07/whisper-leak-a-novel-side-channel-cyberattack-on-remote-language-models/ · code https://github.com/yo-yo-yo-jbo/whisper_leak
3. Li, Cui, Chen, Lin, Gu, Deng, Xu, Li. *From Length to Content: Token-Length Side-Channel Attacks on LLM API Merged Outputs (PromptEcho).* USENIX Security '26. https://www.usenix.org/conference/usenixsecurity26/presentation/li-sijia
4. Zhang, Wu, Deng, Wang, Zhang. *NetEcho: From Real-World Streaming Side-Channels to Full LLM Conversation Recovery.* arXiv:2510.25472. https://arxiv.org/abs/2510.25472
5. Carlini, Nasr. *Remote Timing Attacks on Efficient Language Model Inference.* arXiv:2410.17175. https://arxiv.org/abs/2410.17175
6. Soleimani, Jia, Gim, Lee, Khandelwal. *Wiretapping LLMs: Network Side-Channel Attacks on Interactive LLM Services.* IACR ePrint 2025/167. https://eprint.iacr.org/2025/167
7. Zhang, Saileshwar, Lie. *Time Will Tell: Timing Side Channels via Output Token Count in Large Language Models.* arXiv:2412.15431. https://arxiv.org/abs/2412.15431
8. Wei, Abdulrazzag, Zhang, Muursepp, Saileshwar. *When Speculation Spills Secrets: Side Channels via Speculative Decoding in LLMs.* arXiv:2411.01076. https://arxiv.org/abs/2411.01076
9. Jeong, Teymoorianfard, Kumar, Houmansadr, Bagdasarian. *Network-Level Prompt and Trait Leakage in Local Research Agents.* arXiv:2508.20282. https://arxiv.org/abs/2508.20282 · code https://github.com/umass-aisec/wra
10. Schneier. *Side-Channel Attacks Against LLMs.* Feb 2026. https://www.schneier.com/blog/archives/2026/02/side-channel-attacks-against-llms.html

### 8.2 Cache / API-timing channels
11. Zheng et al. *InputSnatch: Stealing Input in LLM Services via Timing Side-Channel Attacks.* arXiv:2411.18191. https://arxiv.org/abs/2411.18191
12. Song, Pang, Wang, Wang, Wang, Chen, Song, Jin, Meng, Hou. *The Early Bird Catches the Leak: Unveiling Timing Side Channels in LLM Serving Systems.* arXiv:2409.20002 (IEEE TIFS). https://arxiv.org/abs/2409.20002
13. Wu, Zhang, Zhang, Wang, Niu, Wu, Zhang. *I Know What You Asked: Prompt Leakage via KV-Cache Sharing in Multi-Tenant LLM Serving.* NDSS 2025. https://www.ndss-symposium.org/ndss-paper/i-know-what-you-asked-prompt-leakage-via-kv-cache-sharing-in-multi-tenant-llm-serving/
14. Gu, Li, Kuditipudi, Liang, Hashimoto. *Auditing Prompt Caching in Language Model APIs.* ICML 2025. https://arxiv.org/abs/2502.07776
15. Chu et al. *Selective KV-Cache Sharing to Mitigate Timing Side-Channels in LLM Inference (SafeKV).* arXiv:2508.08438. https://arxiv.org/abs/2508.08438
16. Pennas, Papaioannou, Guarnieri, Doudali. *PrefixWall: Mitigating Prefix Caching Side Channels in Shared LLM Systems.* arXiv:2603.10726. https://arxiv.org/abs/2603.10726
17. Ding, Xu, Shen, Ding, Fei. *MoEcho: Exploiting Side-Channel Attacks to Compromise User Privacy in Mixture-of-Experts LLMs.* CCS 2025. https://arxiv.org/abs/2508.15036

### 8.3 Classical traffic analysis — attacks
18. Song, Wagner, Tian. *Timing Analysis of Keystrokes and Timing Attacks on SSH.* USENIX Security 2001. https://www.usenix.org/conference/10th-usenix-security-symposium/timing-analysis-keystrokes-and-timing-attacks-ssh
19. Chen, Wang, Wang, Zhang. *Side-Channel Leaks in Web Applications.* IEEE S&P 2010. https://www.microsoft.com/en-us/research/publication/side-channel-leaks-in-web-applications-a-reality-today-a-challenge-tomorrow/
20. Wright, Ballard, Coull, Masson, Monrose. *Spot Me if You Can: Uncovering Spoken Phrases in Encrypted VoIP Conversations.* IEEE S&P 2008. https://www.cs.unc.edu/~fabian/papers/oakland08.pdf
21. Schuster, Shmatikov, Tromer. *Beauty and the Burst: Remote Identification of Encrypted Video Streams.* USENIX Security 2017. https://www.usenix.org/conference/usenixsecurity17/technical-sessions/presentation/schuster
22. Panchenko et al. *Website Fingerprinting at Internet Scale (CUMUL).* NDSS 2016. https://www.comsys.rwth-aachen.de/publication/2016/2016_panchenko_website-fingerprinting-at-internet/2016_panchenko_website-fingerprinting-at-internet.pdf
23. Hayes, Danezis. *k-fingerprinting: A Robust Scalable Website Fingerprinting Technique.* USENIX Security 2016. https://www.usenix.org/conference/usenixsecurity16/technical-sessions/presentation/hayes
24. Sirinam, Imani, Juarez, Wright. *Deep Fingerprinting.* CCS 2018. https://arxiv.org/abs/1801.02265
25. Shen, Ji, Gao, Li, Zhu, Xu. *Subverting Website Fingerprinting Defenses with Robust Traffic Representation.* USENIX Security 2023. https://www.usenix.org/conference/usenixsecurity23/presentation/shen-meng
26. Zohaib, Sheffey, Houmansadr. *Investigating Traffic Analysis Attacks on Apple iCloud Private Relay.* AsiaCCS 2023. https://dl.acm.org/doi/10.1145/3579856.3595793

### 8.4 Classical traffic analysis — defenses
27. Wright, Coull, Monrose. *Traffic Morphing.* NDSS 2009. https://www.ndss-symposium.org/ndss2009/traffic-morphing-efficient-defense-against-statistical-traffic-analysis/
28. Dyer, Coull, Ristenpart, Shrimpton. *Peek-a-Boo, I Still See You (BuFLO).* IEEE S&P 2012. https://www.freehaven.net/anonbib/cache/oakland2012-peekaboo.pdf
29. Cai, Nithyanand, Wang, Johnson, Goldberg. *A Systematic Approach to Developing and Evaluating Website Fingerprinting Defenses (Tamaraw).* CCS 2014. https://dl.acm.org/doi/10.1145/2660267.2660362
30. Juarez, Imani, Perry, Diaz, Wright. *Toward an Efficient Website Fingerprinting Defense (WTF-PAD).* ESORICS 2016. https://arxiv.org/abs/1512.00524
31. Wang, Goldberg. *Walkie-Talkie.* USENIX Security 2017. https://www.usenix.org/conference/usenixsecurity17/technical-sessions/presentation/wang-tao
32. Gong, Wang. *Zero-delay Lightweight Defenses against Website Fingerprinting (FRONT/GLUE).* USENIX Security 2020. https://www.usenix.org/conference/usenixsecurity20/presentation/gong
33. Holland, Hopper. *RegulaTor.* PETS 2022. https://petsymposium.org/popets/2022/popets-2022-0049.pdf
34. Gong et al. *Surakav: Generating Realistic Traces for a Strong Website Fingerprinting Defense.* IEEE S&P 2022. https://ieeexplore.ieee.org/document/9833722/
35. Shen et al. *Real-Time Website Fingerprinting Defense via Traffic Cluster Anonymization (Palette).* IEEE S&P 2024. http://www.thucsnet.com/wp-content/papers/meng_sp2024.pdf
36. Holland, Carpenter, Oh, Hopper. *DeTorrent: An Adversarial Padding-only Traffic Analysis Defense.* PETS 2024. https://petsymposium.org/popets/2024/popets-2024-0007.pdf
37. Cherubin, Hayes, Juarez. *Website Fingerprinting Defenses at the Application Layer (ALPaCA/LLaMA).* PETS 2017. https://petsymposium.org/2017/papers/issue2/paper54-2017-2-source.pdf
38. Pulls, Witwer. *Maybenot: A Framework for Traffic Analysis Defenses.* WPES 2023. https://arxiv.org/abs/2304.09510 · code https://github.com/maybenot-io/maybenot
39. Mehta, Alzayat, de Viti, Brandenburg, Druschel, Garg. *Pacer: Comprehensive Network Side-Channel Mitigation in the Cloud.* arXiv:1908.11568. https://arxiv.org/abs/1908.11568
40. Reed, Reiter. *Optimally Hiding Object Sizes with Constrained Padding.* arXiv:2108.01753. https://arxiv.org/abs/2108.01753

### 8.5 Methodology and measurement
41. Juarez, Afroz, Acar, Diaz, Greenstadt. *A Critical Evaluation of Website Fingerprinting Attacks.* CCS 2014. https://www.freehaven.net/anonbib/cache/ccs2014-critical.pdf
42. Cherubin. *Bayes, not Naïve: Security Bounds on Website Fingerprinting Defenses.* PETS 2017. https://arxiv.org/abs/1702.07707
43. Li, Guo, Hopper. *Measuring Information Leakage in Website Fingerprinting Attacks and Defenses (WeFDE).* CCS 2018. https://www-users.cse.umn.edu/~hoppernj/wefde-ccs2018.pdf
44. Veicht, Renggli, Barradas. *DeepSE-WF: Unified Security Estimation for Website Fingerprinting Defenses.* arXiv:2203.04428. https://arxiv.org/abs/2203.04428
45. Cherubin, Jansen, Troncoso. *Online Website Fingerprinting.* USENIX Security 2022. https://www.usenix.org/conference/usenixsecurity22/presentation/cherubin
46. Arp, Quiring, Pendlebury, Warnecke, Pierazzi, Wressnegger, Cavallaro, Rieck. *Dos and Don'ts of Machine Learning in Computer Security.* USENIX Security 2022. https://www.usenix.org/system/files/sec22summer_arp.pdf
47. Gong, Zhang, Zhang, Wang. *WFDefProxy: Modularly Implementing and Empirically Evaluating Website Fingerprinting Defenses.* arXiv:2111.12629. https://arxiv.org/abs/2111.12629
48. Witwer, Holland, Hopper. *Padding-only defenses add delay in Tor.* arXiv:2208.02917. https://arxiv.org/abs/2208.02917
49. Lavrentieva, Juarez, Honda. *Rethinking the Role of Network Stacks for Website Fingerprinting Defenses.* HotNets 2025. https://conferences.sigcomm.org/hotnets/2025/papers/hotnets25-final494.pdf
50. Witwer. *State Machine Frameworks for Website Fingerprinting Defenses: Maybe Not.* arXiv:2310.10789. https://arxiv.org/abs/2310.10789
51. Siby, Juarez, Diaz, Vallina-Rodriguez, Troncoso. *Encrypted DNS ⇒ Privacy? A Traffic Analysis Perspective.* NDSS 2020. https://www.ndss-symposium.org/ndss-paper/encrypted-dns-privacy-a-traffic-analysis-perspective/

### 8.6 Industry, standards and platform documentation
52. Cloudflare. *Mitigating a token-length side-channel attack in our AI products.* https://blog.cloudflare.com/ai-side-channel-attack-mitigated/
53. OpenAI. *Chat Completions streaming events* (`obfuscation`, `stream_options.include_obfuscation`). https://developers.openai.com/api/reference/resources/chat/subresources/completions/streaming-events
54. Apple Security Research. *Private Cloud Compute.* https://security.apple.com/blog/private-cloud-compute/
55. Anthropic. *Confidential inference via trusted virtual machines.* https://www.anthropic.com/research/confidential-inference-trusted-vms
56. Confident Security. *Introducing OpenPCC.* https://blog.confident.security/openpcc/ · code https://github.com/openpcc/openpcc
57. Cisco. *Encrypted Traffic Analytics* (SPLT). https://www.cisco.com/c/en/us/solutions/collateral/enterprise-networks/enterprise-network-security/at-a-glance-c45-740079.html
58. Mullvad. *Introducing DAITA.* https://mullvad.net/en/blog/introducing-defense-against-ai-guided-traffic-analysis
59. Nym. *Nym Mixnet.* https://nym.com/mixnet
60. RFC 8446 §5.4 (TLS 1.3 record padding); RFC 9113 §6.1 (HTTP/2 DATA padding); RFC 9000 §19.1 (QUIC PADDING); RFC 8467 (DNS EDNS(0) padding policies) https://www.rfc-editor.org/rfc/rfc8467
61. OpenSSL. *SSL_CTX_set_block_padding / SSL_CTX_set_record_padding_callback.* https://www.openssl.org/docs/manmaster/man3/SSL_CTX_set_record_padding_callback.html
62. Go. *crypto/tls dynamic record sizing* (`tcpMSSEstimate`, `recordSizeBoostThreshold`, `DynamicRecordSizingDisabled`). https://tip.golang.org/src/crypto/tls/conn.go
63. Google Cloud. *Packet Mirroring.* https://docs.cloud.google.com/vpc/docs/packet-mirroring
64. Google Cloud. *Network Security Integration — out-of-band integration overview.* https://cloud.google.com/network-security-integration/docs/out-of-band/out-of-band-integration-overview
65. Google Cloud. *GKE Autopilot security capabilities.* https://docs.cloud.google.com/kubernetes-engine/docs/concepts/autopilot-security
66. Google Cloud. *Global external Application Load Balancer overview.* https://docs.cloud.google.com/load-balancing/docs/https
67. Google Cloud. *Passthrough Network Load Balancer overview.* https://docs.cloud.google.com/load-balancing/docs/passthrough-network-load-balancer
68. Google Cloud. *Pub/Sub exactly-once delivery.* https://docs.cloud.google.com/pubsub/docs/exactly-once-delivery
69. Google Cloud. *GKE Dataplane V2 observability.* https://docs.cloud.google.com/kubernetes-engine/docs/concepts/about-dpv2-observability
70. Google Cloud. *Serve Gemma open models using GPUs on GKE with vLLM.* https://docs.cloud.google.com/kubernetes-engine/docs/tutorials/serve-gemma-gpu-vllm
71. HashiCorp. *Terraform GCS backend.* https://developer.hashicorp.com/terraform/language/backend/gcs
72. `google-github-actions/auth` — Workload Identity Federation. https://github.com/google-github-actions/auth
73. vLLM. *OpenAI-Compatible Server* (`--stream-interval`) and *Automatic Prefix Caching*. https://docs.vllm.ai/en/latest/serving/online_serving/openai_compatible_server/ · https://docs.vllm.ai/en/stable/design/prefix_caching/
74. Kubernetes SIGs. *Gateway API Inference Extension.* https://github.com/kubernetes-sigs/gateway-api-inference-extension
75. Check Point. *Check Point Acquires Lakera.* https://www.checkpoint.com/press-releases/check-point-acquires-lakera-to-deliver-end-to-end-ai-security-for-enterprises/
76. F5. *F5 to acquire CalypsoAI.* https://www.f5.com/company/news/press-releases/f5-to-acquire-calypsoai-to-bring-advanced-ai-guardrails-to-large-enterprises
77. Palo Alto Networks. *Completes Acquisition of Portkey.* https://www.paloaltonetworks.com/company/press/2026/palo-alto-networks-completes-acquisition-of-portkey-to-secure-ai-agents

---

*End of RESEARCH.md. Product decisions derived from this review are in `PRODUCT.md`; engineering execution is in `IMPLEMENTATION_PLAN.md`.*
