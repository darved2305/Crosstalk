// Builds docs/report/CrossTalk_Project_Report.docx from committed evidence.
// Run from docs/report: `npm ci && node build_report.mjs`, then export_pdf.ps1 for the PDF.
// Every measured number below is read from docs/evidence/* at build time.
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import {
  AlignmentType, BorderStyle, Document, Footer, Header, HeadingLevel, ImageRun,
  LevelFormat, PageBreak, PageNumber, Packer, Paragraph, ShadingType, Table, TableCell,
  TableOfContents, TableRow, TabStopType, TextRun, VerticalAlign, WidthType,
} from "docx";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(HERE, "..", "..");
const EVID = path.join(ROOT, "docs", "evidence");
// Python json writes NaN/Infinity for undefined statistics; render them as n/a.
const read = (p) => JSON.parse(fs.readFileSync(path.join(ROOT, p), "utf8").replace(/(?<=[:\[,]\s*)-?(NaN|Infinity)(?=\s*[,\]}])/g, "null"));
const exists = (p) => fs.existsSync(path.join(ROOT, p));

// ---------- measured inputs ----------
const campaign = read("docs/evidence/campaign/campaign.json");
const e1 = read("docs/evidence/campaign/e1-e2e/summary.json");
const e2 = read("docs/evidence/campaign/e2-e2e/summary.json");
const runtimeV1 = read("docs/evidence/runtime-linux-20261012.json");
const pipelineRun = exists("docs/evidence/pipeline/pipeline-run.json")
  ? read("docs/evidence/pipeline/pipeline-run.json") : null;
const runtimeV2Files = fs.readdirSync(EVID).filter((f) => /^runtime-linux-v2-.*\.json$/.test(f) && !/soak/.test(f)).sort();
const soakFiles = fs.readdirSync(EVID).filter((f) => /^runtime-linux-v2-.*soak.*\.json$/.test(f)).sort();
const runtimeV2 = runtimeV2Files.length ? read(`docs/evidence/${runtimeV2Files.at(-1)}`) : null;
const soakV2 = soakFiles.length ? read(`docs/evidence/${soakFiles.at(-1)}`) : null;

// ---------- typography ----------
const BODY = "Cambria";
const HEAD = "Calibri";
const ACCENT = "1F3A5F";
const GREY = "595959";
const PAGE_W = 11906, PAGE_H = 16838, MARGIN = 1247; // A4, 2.2 cm margins
const TEXT_W = PAGE_W - 2 * MARGIN; // 9412 DXA
let figureNo = 0, tableNo = 0;
let LABELS = {}; const NEXT = {};
const resolve = (t) => t.replace(/\{(fig|tab):([a-z0-9-]+)\}/g, (_, kind, key) => String(LABELS[`${kind}:${key}`] ?? "?"));

const fmt = (v, d = 2) => (v === null || v === undefined || Number.isNaN(v) ? "n/a" : Number(v).toFixed(d));
const ci = (o, d = 1) => (o && o.value !== null && !Number.isNaN(o.value) ? `${fmt(o.value, d)} [${fmt(o.ci_lo, d)}, ${fmt(o.ci_hi, d)}]` : "n/a");

// Inline markup: **bold**, *italic*, `code`.
function runs(text, base = {}) {
  text = resolve(text);
  const out = [];
  const re = /(\*\*[^*]+\*\*|`[^`]+`|\*[^*]+\*)/g;
  let last = 0, m;
  while ((m = re.exec(text))) {
    if (m.index > last) out.push(new TextRun({ text: text.slice(last, m.index), ...base }));
    const t = m[0];
    if (t.startsWith("**")) out.push(new TextRun({ text: t.slice(2, -2), bold: true, ...base }));
    else if (t.startsWith("`")) out.push(new TextRun({ text: t.slice(1, -1), font: "Consolas", size: 18, ...base }));
    else out.push(new TextRun({ text: t.slice(1, -1), italics: true, ...base }));
    last = m.index + t.length;
  }
  if (last < text.length) out.push(new TextRun({ text: text.slice(last), ...base }));
  return out;
}
const P = (text, opts = {}) => new Paragraph({ children: runs(text), spacing: { after: 140, line: 288 }, alignment: AlignmentType.JUSTIFIED, ...opts });
const H1 = (text) => new Paragraph({ heading: HeadingLevel.HEADING_1, children: [new TextRun(text)], pageBreakBefore: false });
const H2 = (text) => new Paragraph({ heading: HeadingLevel.HEADING_2, children: [new TextRun(text)] });
const H3 = (text) => new Paragraph({ heading: HeadingLevel.HEADING_3, children: [new TextRun(text)] });
const bullet = (text, level = 0) => new Paragraph({ numbering: { reference: "bullets", level }, children: runs(text), spacing: { after: 80, line: 276 } });
const numbered = (text, ref = "numbers") => new Paragraph({ numbering: { reference: ref, level: 0 }, children: runs(text), spacing: { after: 80, line: 276 } });
const code = (lines) => lines.map((line, i) => new Paragraph({
  children: [new TextRun({ text: line || " ", font: "Consolas", size: 17 })],
  shading: { type: ShadingType.CLEAR, fill: "F3F4F6", color: "auto" },
  spacing: { before: i === 0 ? 60 : 0, after: i === lines.length - 1 ? 160 : 0, line: 240 },
  indent: { left: 120, right: 120 },
}));
const pageBreak = () => new Paragraph({ children: [new PageBreak()] });

function pngSize(file) {
  const b = fs.readFileSync(file);
  return { w: b.readUInt32BE(16), h: b.readUInt32BE(20) };
}
function figure(rel, caption, widthIn = 6.2, maxHeightIn = 7.6, label = null) {
  const file = path.join(ROOT, rel);
  const { w, h } = pngSize(file);
  let width = widthIn * 96;
  let height = (width * h) / w;
  if (height > maxHeightIn * 96) { height = maxHeightIn * 96; width = (height * w) / h; }
  figureNo += 1;
  if (label) NEXT[`fig:${label}`] = figureNo;
  return [
    new Paragraph({ alignment: AlignmentType.CENTER, keepNext: true, spacing: { before: 120, after: 60 },
      children: [new ImageRun({ type: "png", data: fs.readFileSync(file), transformation: { width: Math.round(width), height: Math.round(height) },
        altText: { title: `Figure ${figureNo}`, description: caption, name: `figure-${figureNo}` } })] }),
    new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 220 },
      children: [new TextRun({ text: `Figure ${figureNo}. `, bold: true, size: 19, color: ACCENT }), ...runs(caption, { size: 19, color: GREY })] }),
  ];
}
const border = { style: BorderStyle.SINGLE, size: 4, color: "BFC5CC" };
function table(caption, headers, rows, widths, opts = {}) {
  tableNo += 1;
  if (opts.label) NEXT[`tab:${opts.label}`] = tableNo;
  const total = widths.reduce((a, b) => a + b, 0);
  const scale = TEXT_W / total;
  const w = widths.map((x) => Math.floor(x * scale));
  w[w.length - 1] += TEXT_W - w.reduce((a, b) => a + b, 0);
  const cell = (text, i, head) => new TableCell({
    width: { size: w[i], type: WidthType.DXA },
    borders: { top: border, bottom: border, left: border, right: border },
    shading: head ? { type: ShadingType.CLEAR, fill: "E8EEF5", color: "auto" } : undefined,
    margins: { top: 50, bottom: 50, left: 90, right: 90 },
    verticalAlign: VerticalAlign.CENTER,
    children: [new Paragraph({ alignment: (opts.align?.[i] ?? "left") === "right" ? AlignmentType.RIGHT : AlignmentType.LEFT,
      children: runs(String(text), { size: 17, bold: head || undefined, font: head ? HEAD : undefined }) })],
  });
  return [
    new Paragraph({ keepNext: true, spacing: { before: 160, after: 80 },
      children: [new TextRun({ text: `Table ${tableNo}. `, bold: true, size: 19, color: ACCENT }), ...runs(caption, { size: 19, color: GREY })] }),
    new Table({ width: { size: TEXT_W, type: WidthType.DXA }, columnWidths: w,
      rows: [new TableRow({ tableHeader: true, children: headers.map((h, i) => cell(h, i, true)) }),
        ...rows.map((r) => new TableRow({ cantSplit: true, children: r.map((c, i) => cell(c, i, false)) }))] }),
    new Paragraph({ spacing: { after: 160 }, children: [] }),
  ];
}

// ---------- derived measured tables ----------
const expByName = Object.fromEntries(campaign.experiments.map((e) => [e.name, e]));
const campaignRows = ["e0-e2e", "e1-e2e", "e2-e2e"].map((name) => {
  const e = expByName[name];
  const planned = e.sessions.reduce((a, s) => a + s.planned, 0);
  const completed = e.sessions.reduce((a, s) => a + s.completed, 0);
  const failed = e.sessions.reduce((a, s) => a + s.failed, 0);
  return [name.slice(0, 2).toUpperCase(), e.sessions.length, planned, completed, failed, e.evaluations, e.sessions.map((s) => s.status)[0]];
});
const totals = [1, 2, 3, 4, 5].map((i) => campaignRows.reduce((a, r) => a + r[i], 0));
const sum = (i) => totals[i - 1];
campaignRows.push(["Total", ...totals, "—"]);

const armNames = { a0: "D0 passthrough", a1: "D1 bucket padding", a2: "D2 window batching", a3: "D3 constant rate", a4: "D4 hybrid" };
const e1Costs = Object.entries(e1.costs.NAT).map(([arm, c]) => [
  armNames[arm] ?? arm, c.n, fmt(c.ttft_ms.p95, 1), fmt(c.itl_ms.p99, 1), fmt(c.pad_bytes_mean, 0), fmt(c.dummies_mean, 1),
  arm === "a0" ? "baseline" : ci(c.bandwidth_overhead_pct, 1),
]);
const e1Tests = Object.entries(e1.primary.NAT.tests).map(([key, t]) => [
  armNames[t.arm] ?? t.arm, t.setting, fmt(t.balanced_accuracy, 3), ci(t.delta, 3), fmt(t.holm.p_holm, 2), t.holm.reject ? "yes" : "no",
]);
const e2Rows = [];
for (const [level, arms] of Object.entries(e2.costs)) {
  for (const [cond, c] of Object.entries(arms)) {
    e2Rows.push([`C=${level}`, cond, c.n, fmt(c.ttft_ms.p95, 1), fmt(c.itl_ms.p99, 1), fmt(c.wire_bytes_mean, 0)]);
  }
}

// Runtime gate rows (v1 always; v2 when present).
const med = (o) => o.median ?? o;
const v1Relay = runtimeV1.relay_latency;
const v1Timer = runtimeV1.timers;
const v1Bp = runtimeV1.backpressure.median;
function gateRows() {
  const rows = [];
  const v2 = runtimeV2;
  const t2 = v2?.timers, r2 = v2?.relay_latency, b2 = v2?.backpressure?.median;
  const pf = (ok) => (ok === undefined ? "—" : ok ? "PASS" : "FAIL");
  const timer = (t, n) => (t ? med(t[n]) : null);
  const relay = (r, n) => (r && r[n] ? med(r[n]) : null);
  const v1t64 = timer(v1Timer, "64"), v1t256 = timer(v1Timer, "256");
  const v2t64 = timer(t2, "64"), v2t256 = timer(t2, "256");
  rows.push(["G2 timer, N=64", "p99 ≤ 2.5 ms; p99.9 ≤ 6 ms", `${fmt(v1t64.p99_ms)} / ${fmt(v1t64.p999_ms)} ms`,
    v2t64 ? `${fmt(v2t64.p99_ms)} / ${fmt(v2t64.p999_ms)} ms` : "—", pf(v2t64 ? v2t64.p99_ms <= 2.5 && v2t64.p999_ms <= 6 : undefined)]);
  rows.push(["G2 timer, N=256", "p99 ≤ 5 ms; p99.9 ≤ 12 ms", `${fmt(v1t256.p99_ms)} / ${fmt(v1t256.p999_ms)} ms`,
    v2t256 ? `${fmt(v2t256.p99_ms)} / ${fmt(v2t256.p999_ms)} ms` : "—", pf(v2t256 ? v2t256.p99_ms <= 5 && v2t256.p999_ms <= 12 : undefined)]);
  const v1r64 = relay(v1Relay, "64"), v1r256 = relay(v1Relay, "256");
  const v2r64 = relay(r2, "64"), v2r256 = relay(r2, "256");
  rows.push(["G3 relay, N=64", "Δp99 ≤ 2 ms", `${fmt(v1r64.added_p99_ms)} ms; ${v1r64.errors} errors`,
    v2r64 ? `${fmt(v2r64.added_p99_ms)} ms; ${v2r64.errors} errors` : "—", pf(v2r64 ? v2r64.added_p99_ms <= 2 && v2r64.errors === 0 : undefined)]);
  rows.push(["G3 relay, N=256", "0 errors; Δp99 ≤ 8 ms", `${fmt(v1r256.added_p99_ms)} ms; ${v1r256.errors} errors`,
    v2r256 ? `${fmt(v2r256.added_p99_ms)} ms; ${v2r256.errors} errors` : "—", pf(v2r256 ? v2r256.added_p99_ms <= 8 && v2r256.errors === 0 : undefined)]);
  rows.push(["G4 CPU / RSS, N=256", "≤ 1.0 core; ≤ 256 MB", `${fmt(v1t256.cpu_cores)} core; ${fmt(v1t256.rss_after_mb, 0)} MB`,
    v2t256 ? `${fmt(v2t256.cpu_cores)} core; ${fmt(v2t256.rss_after_mb, 0)} MB` : "—", pf(v2t256 ? v2t256.cpu_cores <= 1 && v2t256.rss_after_mb <= 256 : undefined)]);
  const soak1 = runtimeV1.soak;
  const soak2 = soakV2?.soak ?? soakV2;
  rows.push(["G4 soak, N=64", "30 min; RSS growth ≤ 10%", `${fmt(soak1.duration_seconds / 60, 1)} min; ${fmt(100 * soak1.rss_growth_fraction, 1)}%`,
    soak2?.measured ? `${fmt(soak2.duration_seconds / 60, 1)} min; ${fmt(100 * soak2.rss_growth_fraction, 1)}%` : "—",
    pf(soak2?.measured ? soak2.duration_seconds >= 1800 && soak2.rss_growth_fraction <= 0.1 : undefined)]);
  rows.push(["G5 backpressure", "buffer ≤ 1 MiB; abort; Δp99 change ≤ 1 ms",
    `${fmt(v1Bp.peak_write_buffer_bytes / 1024, 0)} KiB; ${v1Bp.slow_stream_aborted ? "aborted" : "not aborted"}; ${fmt(v1Bp.normal_p99_change_ms)} ms`,
    b2 ? `${fmt(b2.peak_write_buffer_bytes / 1024, 0)} KiB; ${b2.slow_stream_aborted ? "aborted" : "not aborted"}; ${fmt(b2.normal_p99_change_ms)} ms` : "—",
    pf(b2 ? b2.buffer_within_cap && b2.slow_stream_aborted && b2.normal_p99_change_ms <= 1 : undefined)]);
  return rows;
}

// ---------- document ----------
function content() {
const children = [];
const add = (...items) => items.flat().forEach((x) => children.push(x));

// Title page
add(
  new Paragraph({ spacing: { before: 2600 }, children: [] }),
  new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 200 }, children: [new TextRun({ text: "CrossTalk", font: HEAD, size: 64, bold: true, color: ACCENT })] }),
  new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 600 }, children: [new TextRun({ text: "A measurement laboratory and control plane for encrypted LLM streaming side channels and deployable traffic-shaping defenses", font: HEAD, size: 30, color: "333333" })] }),
  new Paragraph({ alignment: AlignmentType.CENTER, border: { top: { style: BorderStyle.SINGLE, size: 6, color: ACCENT, space: 12 } }, spacing: { after: 120 }, children: [new TextRun({ text: "DevOps Mini Project — Technical Report", font: HEAD, size: 24, color: GREY })] }),
  new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 120 }, children: [new TextRun({ text: "Repository: github.com/darved2305/Crosstalk", size: 21, color: GREY })] }),
  new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 1600 }, children: [new TextRun({ text: "Evidence date: 12 October 2026", size: 21, color: GREY })] }),
  new Paragraph({ alignment: AlignmentType.CENTER, children: [new TextRun({ text: "Status of results: development measurements on a synthetic upstream. They are not validated scientific findings (see Section 8).", italics: true, size: 19, color: "8A1C1C" })] }),
  pageBreak(),
);

// Abstract
add(
  new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 200 }, children: [new TextRun({ text: "Abstract", font: HEAD, size: 28, bold: true, color: ACCENT })] }),
  P("Transport Layer Security (TLS) hides the content of a streamed large-language-model (LLM) response, but it does not hide the size and timing of the records that carry it. Published attacks recover topics and even text from that channel. CrossTalk is an instrument for measuring this leakage *on the wire*. It also measures what deployable traffic-shaping defenses cost and how much leakage they leave behind. The system combines five components: a Python/uvloop TLS-terminating streaming proxy that implements five defense policies; an independent packet-to-record reconstruction path; family-disjoint attacker evaluation with cluster-bootstrap statistics; a FastAPI and PostgreSQL control plane with a durable job queue and production worker; and a React/TypeScript research console."),
  P("This report documents the architecture, the methodology, and the evidence actually obtained. An experiment submitted from the console now executes end to end: the API persists the run, workers claim leased jobs from PostgreSQL, and each session is collected, reconstructed and validated before the experiment-level analysis and report stages run. A retained development campaign (E0–E2) produced " + `${sum(3)} completed requests with ${sum(4)} failures and ${sum(5)} evaluation rows. ` + "It used a synthetic upstream and synthetic TCP segmentation, so every session is labelled PARTIAL and every report NOT REPORTABLE. The Linux runtime gates, the Google Cloud state and the remaining limitations are reported exactly as measured."),
  P("**Keywords:** encrypted traffic analysis; side channels; large language models; traffic shaping; TLS; DevOps; job queues; reproducibility."),
  pageBreak(),
  new Paragraph({ spacing: { after: 200 }, children: [new TextRun({ text: "Contents", font: HEAD, size: 28, bold: true, color: ACCENT })] }),
  new TableOfContents("Contents", { hyperlink: true, headingStyleRange: "1-2" }),
  pageBreak(),
);

// 1 Introduction
add(
  H1("1. Introduction"),
  P("Interactive LLM services stream their answers token by token over Server-Sent Events (SSE). Each event is written to the TLS connection as it is produced, so the ciphertext record lengths track token lengths and the inter-record gaps track generation time. Weiss et al. reconstructed 29% of assistant responses from record sizes alone [1]. Later work extended the attack to topic inference across 28 commercial APIs [2], to merged multi-token chunks [3], and to timing leaks from efficient inference [5]. Providers have shipped mitigations such as random padding fields [8] and token batching [7], but those mitigations are usually evaluated in simulation or against attackers that were never retrained on defended traffic."),
  P("CrossTalk addresses a specific gap. Several defense families are deployed in front of a serving engine; the bytes that reach the wire are validated against what the proxy intended to emit; static and defense-retrained (adaptive) attackers are reported as a pair; and serving load is varied. The project also treats the laboratory as a production system, so this report covers its DevOps platform: the control API, durable queue, workers, console, CI/CD, infrastructure as code and the verification evidence."),
  H2("1.1 Research question"),
  P("*How effective are deployable traffic-shaping defenses against encrypted LLM streaming side channels when they are evaluated on the wire against an attacker retrained on defended traffic, and how does the privacy–performance trade-off change under realistic inference-serving load?*"),
  H2("1.2 Contributions of this project phase"),
  bullet("A production worker that executes runs submitted from the dashboard: API → PostgreSQL queue → collection → reconstruction → validation → analysis → report (Section 4.3)."),
  bullet("A diagnosis and fix of the 256-stream relay failures caused by the aiohttp connection pool, plus a corrected runtime benchmark harness (Section 7.3)."),
  bullet("A polished research console tested against the real stack (Section 7.4)."),
  bullet("An evidence-backed account of what has and has not been validated, including actual Google Cloud console state (Sections 7.5 and 8)."),
);

// 2 Objectives
add(
  H1("2. Objectives and scope"),
  P("The objectives were fixed before implementation, in the project's product and implementation plans:"),
  numbered("Measure leakage at three observation layers (application emission, TLS record and network packet) and validate the mapping between them, not assume it."),
  numbered("Implement five defense arms in the real serving path: passthrough (D0), bucket padding (D1), window batching (D2), constant-rate emission (D3) and a hybrid (D4)."),
  numbered("Evaluate topic leakage with family-disjoint cross-validation, static and adaptive attackers, cluster-bootstrap confidence intervals and null-label controls (experiments E0–E2)."),
  numbered("Operate the laboratory through a durable control plane: an idempotent API, a leased job queue, retries, cancellation and auditable artifacts."),
  numbered("Provide a research console for creating experiments, following runs, and inspecting results, traces and reports."),
  numbered("Automate quality, security and deployment checks, and define the cloud deployment as code under an explicit cost constraint."),
  P("**Ethics and scope.** Only CrossTalk's own infrastructure, models and synthetic prompts are measured. No third-party traffic is captured and no personal data is used. The attack code exists to evaluate defenses."),
);

// 3 Background
add(
  H1("3. Background"),
  P("A TLS 1.3 record carries at most 2^14 plaintext bytes. Its ciphertext length is the plaintext length plus one inner content-type byte, any padding, and a 16-byte authentication tag [9]. An observer who can map records to SSE events therefore learns event sizes to within a constant. In CPython's asyncio SSL transport, each buffer passed to `write()` becomes its own record [10]. CrossTalk relies on this property: one write per emission makes the shaper's emission log directly comparable with the records seen on the wire. The SSE format itself is defined by the WHATWG HTML standard [11]; comment lines, which begin with a colon, are ignored by compliant clients, so the shaper uses them for padding and cover traffic."),
  P("The defenses come from website-fingerprinting research. Constant-rate schemes such as Tamaraw [12] and zero-delay randomized schemes such as FRONT [13] trade bandwidth and latency for indistinguishability. Padding to a bucket ladder hides object sizes at a bounded overhead [14]. For inference, vLLM's `stream_interval` setting batches tokens per event [15], and Cloudflare deployed padding against the token-length attack [7]. Statistical comparisons use percentile cluster-bootstrap intervals [16, 17] and Holm's step-down correction for the family of primary contrasts [18]."),
);

// 4 Architecture
add(
  H1("4. System architecture"),
  P("CrossTalk separates a *control plane* (the console, API, PostgreSQL and workers) from a *data path* (load generator, capture, shaper and upstream). It also separates an *analysis path* (reconstruction, validation, attackers and statistics). Figure 1 shows the components and their interfaces. Import-linter contracts enforce the isolation boundaries; for example, attacker code may not import oracle or load-generator modules, so attack models cannot read ground truth."),
  figure("docs/report/figures/fig_architecture.png", "CrossTalk components. Arrows show data flow. The dashed region runs as a child process of the worker for each collected session."),
  table("Major components and technologies.", ["Component", "Technology", "Responsibility"], [
    ["Research console", "React 19, TypeScript, Vite, TanStack Query, Zod", "Create experiments; start, cancel and retry runs; follow stages; view results, traces and reports"],
    ["Control API", "FastAPI, Pydantic, SQLAlchemy", "Bearer-scoped `/v1` API; idempotent run creation; RFC 9457 problem responses"],
    ["State store", "PostgreSQL 16/18, Alembic", "Experiments, sessions, jobs, events and idempotency keys"],
    ["Worker", "Python asyncio; `crosstalk-worker`", "Claims leased jobs per queue; runs stage handlers; reaps expired leases"],
    ["TLS shaper", "Python 3.13, uvloop, h11, aiohttp", "TLS 1.3 SSE proxy; D0–D4 policies; per-emission oracle log; backpressure"],
    ["Capture / reconstruction", "dumpcap (lab) or recording relay (dev); own pcapng/TCP/TLS parser", "Packet → record → request flow mapping; tshark oracle in the lab"],
    ["Evaluation", "scikit-learn, LightGBM, NumPy", "Features; LR/LightGBM attackers; family-disjoint CV; bootstrap; Holm"],
    ["Results serving", "Parquet, DuckDB", "Evaluations, frontier and downsampled traces through the API"],
    ["Delivery", "GitHub Actions, Terraform, Trivy, Semgrep", "Lint, types, tests, coverage gate, security scans, IaC validation"],
  ], [2, 2.6, 4]),
  H2("4.1 Control plane"),
  P("`POST /v1/experiments` validates and stores an experiment configuration. `POST /v1/experiments/{id}/runs` requires an `Idempotency-Key`. Under a row lock on the experiment, it creates one session and one `plan` job per configured session; a replayed key returns the original response. Every job has a deterministic `stage_key` (a SHA-256 over stage, inputs, configuration hash and code commit) with a unique constraint, which makes enqueueing idempotent."),
  H2("4.2 Durable queue"),
  P("A worker claims a job with a single `UPDATE … WHERE id = (SELECT … FOR UPDATE SKIP LOCKED LIMIT 1) RETURNING *` statement. The claim sets a 60-second lease, and a heartbeat extends it every 20 s. A failed job is requeued with full-jitter exponential backoff (5 s base, 300 s cap) and becomes DEAD after five attempts. A reaper requeues expired leases. Cancellation is immediate for queued jobs and cooperative for running ones: a running collection child process is terminated, and its partial artifacts are moved aside rather than deleted."),
  H2("4.3 Production stage pipeline"),
  P("Figure 2 shows the stage graph that the worker executes. Per-session stages advance the session state machine (PLANNED → COLLECTING → COLLECTED → RECONSTRUCTED → VALID / INVALID / PARTIAL). Feature extraction, attacker training and statistics pool every session of an experiment, because family-disjoint folds span sessions; they therefore run in a single experiment-level `analyze` stage. The last session to reach a terminal state enqueues that stage while holding the experiment row lock, so it is created exactly once. Outputs are keyed by the experiment ULID, so rerunning a configuration never pools sessions or overwrites earlier results."),
  figure("docs/report/figures/fig_pipeline.png", "Stage graph executed by the production worker, with job queues and the session states each stage produces.", 6.4),
  P("Collection runs in a child process of the worker. That process loads only the measurement stack, so a crash during collection cannot take down the worker. On Windows this isolation is necessary rather than cosmetic: with TLS key logging enabled, CPython's `keylog_filename` aborts the process under the OpenSSL DLL build (`OPENSSL_Uplink: no OPENSSL_Applink`). The worker records that failure as a job error, and the local development lab disables key logging on Windows with an explicit session note."),
  P("Two lab back-ends exist. The *external* lab targets the Linux laboratory's shaper and dumpcap capture supervisor. The *local-dev* lab starts a synthetic OpenAI-compatible upstream, the production TLS shaper (with the experiment's policy set) and a recording relay inside the collection process. The relay records real TLS ciphertext but synthesises TCP segmentation. Local-dev sessions are stamped `upstream_kind: mock` and `lab: local-dev`, any configured model is replaced by `mock` (and noted), and validation marks them PARTIAL with explicit reasons."),
);

// 5 Methodology
add(
  H1("5. Methodology"),
  H2("5.1 Experiments"),
  table("Experiment design.", ["Experiment", "Question", "Factors", "Primary output"], [
    ["E0 calibration", "Does topic leak through record sizes and timing without defenses?", "Regime: NAT, MATCH-P (prompt-matched), MATCH-PR (prompt- and response-length-matched)", "Balanced accuracy for T1 (topic) and T2 (sensitive vs rest); null and noise-floor controls"],
    ["E1 defenses", "How much leakage do D1–D4 remove, and at what cost?", "Arm D0–D4, interleaved per block; static vs adaptive attacker", "Paired Δ balanced accuracy with Holm correction; retained leakage; bandwidth and latency cost"],
    ["E2 load", "How does serving load change leakage and cost?", "Concurrency level; vLLM stream interval; time-alternating blocks", "Per-level contrasts; leakage–cost frontier"],
  ], [1.6, 2.6, 2.8, 3]),
  H2("5.2 Defense policies"),
  table("Defense arms implemented in the shaper.", ["Arm", "Policy", "Mechanism"], [
    ["D0", "Passthrough", "One TLS record per upstream SSE event"],
    ["D1", "Bucket padding", "Each event padded with SSE comments to the smallest bucket of a doubling ladder (capped at 16 KiB)"],
    ["D2", "Window batching", "Events merged over a fixed time window, then emitted as one record"],
    ["D3", "Constant rate", "Fixed-size records on a fixed tick, with dummy records when idle and a quantized tail"],
    ["D4", "Hybrid", "Window batching followed by bucket padding"],
  ], [0.8, 1.8, 6]),
  H2("5.3 Attackers and statistics"),
  P("Features are derived from observer-layer data only: record sizes, inter-arrival times and summary statistics (representations r1, r2 and total length). Attackers are a chance baseline, a majority classifier, logistic regression and LightGBM. Outer folds are prompt-family-disjoint, so no paraphrase family appears in both training and test data; hyperparameters are tuned in inner folds. Confidence intervals are 95% percentile intervals from a bootstrap that resamples prompt families, stratified by class. The E1/E2 primary contrasts are pre-declared and Holm-corrected. A null-label permutation control and a noise floor guard against spurious leakage."),
  H2("5.4 Validity labelling"),
  P("A session is VALID only if every check passes. Capture must show zero kernel and interface drops and verified offload settings; every TCP flow must be complete; TLS records must agree with tshark; at least 99.99% of emissions must match their records; keylog plaintext must match where keylogs are enabled; and provenance must be complete (commit, clean tree, image digests and model revision). Any analysis over a session that is not VALID is labelled NOT REPORTABLE in its report and in every API response."),
  H2("5.5 Runtime gate"),
  P("Before the shaper runtime is accepted for research use, it must pass gates G1–G6 on Linux with uvloop. Each benchmark runs three repetitions and the decision uses the median repetition (Table {tab:gates}, Section 7.3). The thresholds come from the implementation plan and were not changed during this work."),
);

// 6 DevOps
add(
  H1("6. Implementation and DevOps practice"),
  table("Delivery pipeline and quality gates.", ["Stage", "Tooling", "Gate"], [
    ["Static quality", "Ruff (lint and format), Pyright strict, import-linter", "Zero findings; seven architecture contracts kept"],
    ["API contract", "OpenAPI export; openapi-typescript", "Committed `docs/openapi.json` must match the code; console types are regenerated from it"],
    ["Python tests", "pytest, Hypothesis; PostgreSQL integration tests", "≥ 80% line coverage; warnings are errors"],
    ["Queue/worker integration", "Runner's preinstalled PostgreSQL (no container)", "Full API → queue → worker → report path"],
    ["Console", "ESLint, tsc, Vitest, Vite build, Playwright", "All checks green; fixture browser tests"],
    ["Security", "Trivy, Semgrep, gitleaks, pip-audit", "HIGH/CRITICAL findings fail the build"],
    ["Infrastructure", "Terraform fmt/validate; Compose config", "Static validation only (Section 7.5)"],
  ], [1.8, 3.6, 4]),
  P("Configuration follows twelve-factor practice: services read prefixed environment variables (`CT_API_*`, `CT_WORKER_*`, `CT_SHAPER_*`), secrets never enter the repository, and bearer tokens live only in process environments and browser session storage. Containers run as non-root with read-only file systems and all capabilities dropped. The Terraform modules define a budget alert, a VPC, an Artifact Registry repository, a storage bucket, IAM roles, a GPU server and a client VM."),
);

// 7 Results
const pipelineParas = [];
if (pipelineRun) {
  pipelineParas.push(P(pipelineRun.summary));
  if (pipelineRun.table) pipelineParas.push(...table(pipelineRun.table.caption, pipelineRun.table.headers, pipelineRun.table.rows, pipelineRun.table.widths));
}
add(
  H1("7. Results"),
  P("All results in this section were measured on the stated hosts. Where a gate failed, it is reported as a failure."),
  H2("7.1 End-to-end execution of dashboard runs"),
  P("Before this phase, a run submitted from the console created sessions and `plan` jobs that stayed queued, because no process executed the stages. With the worker in place, runs execute to completion. `tests/test_pipeline_postgres.py` creates a temporary PostgreSQL database, applies the Alembic migrations, submits experiments through the real FastAPI application and drains the queue with the production runner. It then asserts every session and experiment state, job state, pipeline event, `report.md`, evaluation row and artifact index. It also covers a validation-kind experiment and cancellation of a queued run. Both tests pass locally against PostgreSQL 18 and run in CI against the runner's PostgreSQL."),
  ...pipelineParas,
  H2("7.2 Development campaign E0–E2"),
  P("The retained campaign (12 October 2026) executed E0, E1 and E2 on Windows. A synthetic OpenAI-compatible SSE upstream sat behind the production TLS shaper, and a recording relay captured the ciphertext. Table {tab:campaign} lists the volumes. Requests include warm-up and determinism probes, which are excluded from the classifier analyses."),
  table("Retained development campaign. All sessions are PARTIAL; source: `docs/evidence/campaign/campaign.json`.", ["Experiment", "Sessions", "Planned", "Completed", "Failed", "Evaluation rows", "Status"], campaignRows, [1.4, 1.1, 1.1, 1.3, 1, 1.6, 1.3], { align: ["left", "right", "right", "right", "right", "right", "left"], label: "campaign" }),
  P("The E0 classifiers performed at chance. The upstream's output does not depend on prompt topic, so no topic signal exists to be learned, and finding none is the expected sanity check: the pipeline did not manufacture leakage from artefacts such as request ordering or connection reuse. Figure {fig:e0} shows the E0 balanced accuracies with their bootstrap intervals."),
  figure("docs/evidence/campaign/e0-e2e/figures/e0_balanced_accuracy.png", "E0 topic balanced accuracy by regime and attacker (synthetic upstream; chance = 0.167). Generated by the E0 analysis.", 5.6, 7.6, "e0"),
  P("The measurements that *are* informative on synthetic traffic are the defense costs, because they depend on the shaper's mechanics rather than on topic. Table {tab:e1cost} summarizes E1 (48 traces per arm). D1 adds about 16.5% bandwidth with a tight interval. D3 is the most expensive arm, because it emits fixed-size records on every tick, including dummy records."),
  table("E1 defense cost on the synthetic upstream (NAT regime). Bandwidth overhead relative to D0, with a 95% family-bootstrap interval.", ["Arm", "n", "TTFT p95 (ms)", "ITL p99 (ms)", "Pad bytes", "Dummies", "Bandwidth overhead % [95% CI]"], e1Costs, [2.2, 0.6, 1.2, 1.2, 1.1, 1, 2.6], { align: ["left", "right", "right", "right", "right", "right", "left"], label: "e1cost" }),
  table("E1 primary contrasts (LR attacker, representation r1). Δ is the change in balanced accuracy versus D0. Holm-adjusted p-values.", ["Arm", "Attacker", "Balanced acc.", "Δ [95% CI]", "p (Holm)", "Reject H0"], e1Tests, [2.4, 1.2, 1.3, 2.6, 1.1, 1.1], { align: ["left", "left", "right", "left", "right", "left"] }),
  P("No contrast is significant, as expected when the baseline itself is at chance. Retained leakage (the fraction of baseline leakage that remains) is undefined in this campaign, because the baseline does not clear its noise floor; it is shown as n/a rather than imputed. Table {tab:e2} reports the E2 serving costs per concurrency level."),
  table("E2 serving cost by concurrency level and condition (synthetic upstream; `/si4` = stream interval 4).", ["Level", "Condition", "n", "TTFT p95 (ms)", "ITL p99 (ms)", "Mean wire bytes"], e2Rows, [1, 1.5, 0.8, 1.6, 1.6, 1.8], { align: ["left", "left", "right", "right", "right", "right"], label: "e2" }),
  figure("docs/evidence/campaign/e1-e2e/figures/e1_balanced_accuracy.png", "E1 balanced accuracy per arm for static and adaptive attackers (synthetic upstream).", 5.6),
);

// 7.4 runtime
add(
  H2("7.3 Linux runtime gate"),
  P(`The first Linux run (WSL2 kernel ${runtimeV1.environment.kernel}, uvloop, CPython ${runtimeV1.environment.python}) failed the gate. At 256 streams, timer p99 was ${fmt(med(v1Timer["256"]).p99_ms)} ms; the relay added ${fmt(med(v1Relay["256"]).added_p99_ms)} ms at p99, and ${med(v1Relay["256"]).errors} of 256 streams failed in every repetition.`),
  P("**Diagnosis.** The shaper's upstream `aiohttp.ClientSession` used the default `TCPConnector` limit of 100 connections. Its per-request `ClientTimeout(connect=5)` also counts time spent waiting for a free pooled connection. With 256 concurrent streams of about 10 s each, exactly 256 − 100 = 156 requests waited more than 5 s and received HTTP 502. The fix adds an explicit, configurable connector (`upstream_connections`) and applies the 5-second limit to the socket connect (`sock_connect`), not to the pool wait; a regression test drives more than 100 concurrent upstream streams. Two harness defects were also corrected. First, lateness was measured with uvloop's millisecond-quantized `loop.time()`, which is why the v1 values are exact multiples of 1 ms. Second, the clients, the upstream and the shaper shared one event loop, so client decryption load was charged to the shaper. Version 2 of the harness measures against a nanosecond monotonic clock and runs each role in its own process, as the deployment does."),
  ...table("Runtime gate results: median of three repetitions. v1 is the original single-process harness; v2 is the corrected harness with the connection-pool fix.", ["Gate", "Threshold", "v1 (failed run)", "v2", "v2 verdict"], gateRows(), [1.7, 2.2, 2.1, 2.1, 1], { label: "gates" }),
  ...(runtimeV2 ? [] : [P("*The v2 measurements were not available when this document was generated.*")]),
);
const runtimeFigure = path.join(HERE, "figures", "fig_runtime_gates.png");
if (fs.existsSync(runtimeFigure)) add(figure("docs/report/figures/fig_runtime_gates.png", "Runtime gate measurements against their thresholds (median repetition).", 6));
const runtimeNotes = exists("docs/report/runtime_notes.json") ? read("docs/report/runtime_notes.json") : null;
if (runtimeNotes) runtimeNotes.paragraphs.forEach((t) => add(P(t)));

// 7.5 console
const consoleShots = exists("docs/evidence/console-v2/manifest.json") ? read("docs/evidence/console-v2/manifest.json") : null;
add(H2("7.4 Research console"));
if (consoleShots) {
  add(P(consoleShots.summary));
  for (const shot of consoleShots.figures) add(figure(shot.path, shot.caption, shot.width ?? 6.2, shot.maxHeight ?? 7.2));
} else {
  add(P("Screenshots of the earlier console against the real stack (campaign data) are retained in `docs/evidence/campaign/screenshots/`."),
    figure("docs/evidence/campaign/screenshots/e1-e2e-results.png", "Console results view for E1 against the running API and PostgreSQL (1504×1128)."));
}

// 7.6 cloud
add(
  H2("7.5 Google Cloud state and deployment"),
  P("Project `inspired-campus-436516-k4` (display name `db-opt`) was inspected read-only on 12 October 2026, in the owner's signed-in Chrome and with the gcloud CLI. The screenshots are actual captures, cropped only to remove the account avatar. Billing is not linked (Figure {fig:billing}), so the Terraform deployment (L4 GPU server, client VM, registry and budget) cannot be applied without enabling billing. Even Free Tier resources require an active billing account [19]. Enabling billing is outside this project's constraints (at most ₹1 of spend; no billing changes; no paid GPU or VM resources), so **no CrossTalk component is deployed to Google Cloud**, and no cloud result is claimed."),
  P("The project contains two pre-existing resources. A VM, `cfl-box`, is listed in Compute Engine, and a Cloud Run service, `billing-killswitch`, last updated on 25 August 2026, is listed in Cloud Run. **Neither is part of CrossTalk.** Both predate this work and were neither created, modified nor invoked by it."),
  figure("docs/evidence/gcp-console/02-billing-not-linked.png", "Billing page: “This project has no billing account”.", 5.8, 7.6, "billing"),
  figure("docs/evidence/gcp-console/01-project-dashboard.png", "Project dashboard for `db-opt` (`inspired-campus-436516-k4`).", 5.8),
  figure("docs/evidence/gcp-console/04-cloud-run-overview.png", "Cloud Run overview. The listed `billing-killswitch` service is pre-existing and is not CrossTalk.", 5.8),
  figure("docs/evidence/gcp-console/03-compute-engine-vms.png", "Compute Engine VM list. `cfl-box` is a pre-existing VM unrelated to CrossTalk.", 5.8),
);

// 8 Limitations
add(
  H1("8. Discussion and limitations"),
  bullet("**No scientific leakage result.** Every campaign session used a synthetic upstream and synthetic TCP segmentation, so all sessions are PARTIAL and all reports NOT REPORTABLE. E0 accuracy at chance is a property of the mock, not evidence that real LLM streams are safe."),
  bullet("**Capture validation not run.** dumpcap/tshark capture with verified offload settings needs the Linux laboratory (Docker or native capture privileges); it was not executed on this host."),
  bullet("**No GPU serving.** vLLM with a pinned model revision has not been run. Model-dependent timing and token-length behaviour are therefore untested."),
  bullet("**Runtime host.** The runtime gate ran on WSL2 on a laptop, not on the intended GCE laboratory host; its results are indicative of the code but not of that host."),
  bullet("**Cloud.** Blocked by billing, as described in Section 7.5. The Compose worker service and the Terraform modules are validated statically only."),
  bullet("**Windows keylogging.** TLS keylog validation cannot run on the Windows worker because of the OpenSSL Applink limitation; it is disabled there with an explicit session note."),
  bullet("**Statistical power.** The development campaign used B = 200 bootstrap samples and one attacker seed, compared with the planned B = 2000 and five seeds."),
);

// 9 Conclusion
add(
  H1("9. Conclusion and future work"),
  P("CrossTalk now operates as a complete system. An experiment defined in the console is persisted, scheduled, collected through the real TLS shaper, reconstructed, validated, analysed and reported, and every result carries a machine-checked reportability label. The runtime investigation found a configuration fault, not a language limitation: the shaper's 256-stream failures came from the default aiohttp connection pool. It also found two measurement defects in the benchmark harness. All three are fixed and remeasured. What remains is the scientific campaign itself. It requires the Linux capture laboratory with dumpcap and tshark validation, vLLM on a GPU at pinned revisions, and a cloud deployment once a permitted billing arrangement exists. With those in place, the E0–E2 analyses can be repeated with the full bootstrap and seed budget."),
);

// References
const refs = [
  "R. Weiss, D. Ayzenshteyn, G. Amit, Y. Mirsky. “What Was Your Prompt? A Remote Keylogging Attack on AI Assistants.” USENIX Security, 2024.",
  "G. McDonald, J. Bar Or. “Whisper Leak: a side-channel attack on Large Language Models.” arXiv:2511.03675, 2025.",
  "S. Li et al. “From Length to Content: Token-Length Side-Channel Attacks on LLM API Merged Outputs.” USENIX Security, 2026.",
  "Zhang, Wu, Deng, Wang, Zhang. “NetEcho: From Real-World Streaming Side-Channels to Full LLM Conversation Recovery.” arXiv:2510.25472, 2025.",
  "N. Carlini, M. Nasr. “Remote Timing Attacks on Efficient Language Model Inference.” arXiv:2410.17175, 2024.",
  "M. Soleimani et al. “Wiretapping LLMs: Network Side-Channel Attacks on Interactive LLM Services.” IACR ePrint 2025/167.",
  "Cloudflare. “Mitigating a token-length side-channel attack in our AI products.” 14 March 2024.",
  "OpenAI. “Chat Completions streaming events (obfuscation, stream_options.include_obfuscation).” API reference.",
  "E. Rescorla. “The Transport Layer Security (TLS) Protocol Version 1.3.” RFC 8446, IETF, 2018.",
  "Python Software Foundation. CPython 3.13 source: Lib/asyncio/sslproto.py and the ssl module documentation.",
  "WHATWG. “HTML Living Standard — Server-sent events.”",
  "X. Cai, R. Nithyanand, T. Wang, R. Johnson, I. Goldberg. “A Systematic Approach to Developing and Evaluating Website Fingerprinting Defenses” (Tamaraw). ACM CCS, 2014.",
  "J. Gong, T. Wang. “Zero-delay Lightweight Defenses against Website Fingerprinting” (FRONT). USENIX Security, 2020.",
  "A. Reed, M. Reiter. “Optimally Hiding Object Sizes with Constrained Padding.” arXiv:2108.01753.",
  "vLLM project. vLLM documentation: OpenAI-compatible server, serve CLI (stream interval) and metrics.",
  "B. Efron, R. Tibshirani. An Introduction to the Bootstrap. Chapman & Hall, 1993.",
  "V. Saravanan, G. Berman, S. Sober. “Application of the hierarchical bootstrap to multi-level data in neuroscience.” NBDT 3(5), 2020.",
  "S. Holm. “A Simple Sequentially Rejective Multiple Test Procedure.” Scandinavian Journal of Statistics 6(2), 1979.",
  "Google Cloud. “Free cloud features and trial offer.” docs.cloud.google.com/free/docs/free-cloud-features.",
];
add(H1("References"));
refs.forEach((r, i) => add(new Paragraph({ spacing: { after: 90, line: 264 }, indent: { left: 520, hanging: 520 },
  children: [new TextRun({ text: `[${i + 1}]`, size: 19 }), new TextRun({ text: "\t" + r, size: 19 })], tabStops: [{ type: TabStopType.LEFT, position: 520 }] })));

// Appendix
add(
  pageBreak(),
  H1("Appendix A. Reproduction"),
  P("The commands below run the native (Docker-free) stack on Windows, as used for this report. Linux commands are equivalent; on Linux the worker uses uvloop."),
  ...code([
    "uv sync --locked --all-extras",
    "$env:CT_API_DATABASE_URL = 'postgresql+psycopg://crosstalk@127.0.0.1:55432/crosstalk'",
    "uv run alembic upgrade head",
    "# terminal 1 - API",
    "uv run uvicorn crosstalk.api.app:create_app --factory --host 127.0.0.1 --port 18080",
    "# terminal 2 - worker (CT_WORKER_DATABASE_URL, CT_WORKER_ARTIFACTS_ROOT set)",
    "uv run crosstalk-worker",
    "# terminal 3 - console",
    "cd apps/console; $env:CT_CONSOLE_API_URL='http://127.0.0.1:18080'; pnpm dev",
  ]),
  P("Verification commands:"),
  ...code([
    "uv run ruff check . ; uv run pyright ; uv run lint-imports",
    "$env:CT_TEST_DATABASE_URL = 'postgresql+psycopg://crosstalk@127.0.0.1:55432/postgres'",
    "uv run pytest -q",
    "cd apps/console; pnpm check; pnpm test:e2e",
    "# Linux/WSL2 runtime gate (see bench/RESULTS.md for the exact invocations)",
    "uv run python bench/runtime/run.py --duration 20 --output docs/evidence/runtime-linux-v2-<date>.json",
    "# this report",
    "uv run python docs/report/figures.py",
    "cd docs/report; npm ci; node build_report.mjs; powershell -File export_pdf.ps1",
  ]),
);

return children;
}
content();
LABELS = { ...NEXT };
figureNo = 0; tableNo = 0;
const children = content();

const doc = new Document({
  creator: "CrossTalk project",
  title: "CrossTalk — Technical Report",
  description: "Architecture, methodology and measured evidence for the CrossTalk laboratory",
  styles: {
    default: { document: { run: { font: BODY, size: 21 } } },
    paragraphStyles: [
      { id: "Heading1", name: "Heading 1", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { font: HEAD, size: 30, bold: true, color: ACCENT }, paragraph: { spacing: { before: 360, after: 160 }, outlineLevel: 0, keepNext: true } },
      { id: "Heading2", name: "Heading 2", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { font: HEAD, size: 25, bold: true, color: ACCENT }, paragraph: { spacing: { before: 260, after: 120 }, outlineLevel: 1, keepNext: true } },
      { id: "Heading3", name: "Heading 3", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { font: HEAD, size: 22, bold: true, color: "333333" }, paragraph: { spacing: { before: 200, after: 100 }, outlineLevel: 2, keepNext: true } },
    ],
  },
  numbering: { config: [
    { reference: "bullets", levels: [{ level: 0, format: LevelFormat.BULLET, text: "•", alignment: AlignmentType.LEFT, style: { paragraph: { indent: { left: 540, hanging: 270 } } } },
      { level: 1, format: LevelFormat.BULLET, text: "–", alignment: AlignmentType.LEFT, style: { paragraph: { indent: { left: 1000, hanging: 270 } } } }] },
    { reference: "numbers", levels: [{ level: 0, format: LevelFormat.DECIMAL, text: "%1.", alignment: AlignmentType.LEFT, style: { paragraph: { indent: { left: 540, hanging: 320 } } } }] },
  ] },
  sections: [{
    properties: { page: { size: { width: PAGE_W, height: PAGE_H }, margin: { top: MARGIN, bottom: MARGIN, left: MARGIN, right: MARGIN } }, titlePage: true },
    headers: { default: new Header({ children: [new Paragraph({ alignment: AlignmentType.RIGHT, border: { bottom: { style: BorderStyle.SINGLE, size: 4, color: "BFC5CC", space: 4 } },
      children: [new TextRun({ text: "CrossTalk — Technical Report", size: 17, color: GREY, font: HEAD })] })] }), first: new Header({ children: [new Paragraph({ children: [] })] }) },
    footers: { default: new Footer({ children: [new Paragraph({ alignment: AlignmentType.CENTER,
      children: [new TextRun({ children: ["Page ", PageNumber.CURRENT, " of ", PageNumber.TOTAL_PAGES], size: 17, color: GREY, font: HEAD })] })] }), first: new Footer({ children: [new Paragraph({ children: [] })] }) },
    children,
  }],
});

const out = path.join(HERE, "CrossTalk_Project_Report.docx");
fs.writeFileSync(out, await Packer.toBuffer(doc));
console.log(`${out} — ${figureNo} figures, ${tableNo} tables`);
