import { useState, type FormEvent, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { Activity, Download, Eye, EyeOff } from "lucide-react";
import { Link, useSearchParams } from "react-router-dom";
import { api, apiText, ApiError, credentials, errorMessage } from "../lib/client";
import {
  emissionResults,
  evaluationResults,
  experimentDetail,
  experimentPage,
  frontierResults,
  traceResults,
  ulid,
} from "../lib/schemas";
import { Empty, State } from "./common";
import { shortId } from "./format";
import { FrontierPlot, FrontierTable } from "../components/frontier-plot";
import { MarkdownReport } from "../components/markdown-report";
import { TracePlot } from "../components/trace-plot";

const isUlid = (value: string) => ulid.safeParse(value).success;

/** Returns a setter that writes (or removes) search params without adding history noise. */
function useParamWriter() {
  const [params, setParams] = useSearchParams();
  return (values: Record<string, string | null>) => {
    const next = new URLSearchParams(params);
    for (const [key, value] of Object.entries(values))
      if (value) next.set(key, value);
      else next.delete(key);
    setParams(next);
  };
}

/**
 * Experiment chooser backed by the experiment list, with a paste field for IDs
 * that are not on the first page.
 */
function ExperimentPicker({
  value,
  onChange,
  children,
}: {
  value: string;
  onChange: (id: string) => void;
  children?: ReactNode;
}) {
  const list = useQuery({
    queryKey: ["experiments", null],
    queryFn: () => api("/v1/experiments?limit=50", experimentPage),
  });
  const [draft, setDraft] = useState("");
  const items = list.data?.items ?? [];
  const known = items.some((i) => i.id === value);
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (isUlid(draft)) {
      onChange(draft);
      setDraft("");
    }
  };
  return (
    <form className="filterPanel always" onSubmit={submit} role="search">
      <label>
        Experiment
        <select
          value={known ? value : value ? "__other" : ""}
          onChange={(e) => e.target.value !== "__other" && onChange(e.target.value)}
          aria-describedby={list.error ? "experiment-list-error" : undefined}
        >
          <option value="">
            {list.isLoading ? "Loading experiments…" : "Choose an experiment…"}
          </option>
          {value && !known && <option value="__other">{value} (pasted ID)</option>}
          {items.map((item) => (
            <option key={item.id} value={item.id}>
              {item.name} · {item.state} · {shortId(item.id)}
            </option>
          ))}
        </select>
      </label>
      <label>
        Or paste an experiment ID
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value.trim().toUpperCase())}
          placeholder="26-character ULID"
          spellCheck={false}
          aria-invalid={draft !== "" && !isUlid(draft)}
        />
      </label>
      <button className="outlineButton" disabled={!isUlid(draft)}>
        Load
      </button>
      {children}
      {list.error && (
        <p className="inlineError fullRow" id="experiment-list-error">
          Experiment list unavailable: {errorMessage(list.error)}
        </p>
      )}
    </form>
  );
}

function ProvenanceNotice({
  reportable,
  warning,
  fallback,
}: {
  reportable?: boolean | null;
  warning?: string;
  fallback: string;
}) {
  if (reportable === true) return null;
  return (
    <div className="notice warnNotice" role="status">
      {warning ?? fallback}
    </div>
  );
}

export function Results() {
  const [params] = useSearchParams();
  const write = useParamWriter();
  const experiment = params.get("experiment") ?? "";
  const regime = params.get("regime") ?? "all";
  const arm = params.get("arm") ?? "all";
  const setting = params.get("setting") ?? "all";
  const valid = isUlid(experiment);
  const query = useQuery({
    queryKey: ["results", experiment],
    queryFn: () => api(`/v1/experiments/${experiment}/results`, evaluationResults),
    enabled: valid,
  });
  const rows = (query.data?.rows ?? []).filter(
    (row) =>
      (regime === "all" || row.regime === regime) &&
      (arm === "all" || row.arm === arm) &&
      (setting === "all" || row.setting === setting),
  );
  const options = (field: "regime" | "arm" | "setting") =>
    [...new Set(query.data?.rows.map((row) => row[field]) ?? [])].sort();
  const filter = (key: string) => (e: React.ChangeEvent<HTMLSelectElement>) =>
    write({ [key]: e.target.value === "all" ? null : e.target.value });
  return (
    <>
      <div className="pageIntro">
        <p className="eyebrow">ATTACK EVIDENCE</p>
        <h1>Attack results</h1>
        <p>
          E0–E2 measurements, static and adaptive attackers, and 95% cluster-bootstrap
          confidence intervals.
        </p>
      </div>
      <ExperimentPicker
        value={experiment}
        onChange={(id) => write({ experiment: id, regime: null, arm: null, setting: null })}
      />
      {experiment && !valid && <State error={new Error("Experiment ID must be a ULID.")} />}
      <State
        loading={query.isLoading}
        error={query.error}
        loadingMessage="Loading results…"
        notFoundMessage="No result artifact exists yet for this experiment. Results appear after the analyze stage succeeds."
      >
        {query.data && (
          <>
            <ProvenanceNotice
              reportable={query.data.reportable}
              warning={query.data.warning}
              fallback="Reportability has not been verified."
            />
            <div className="filterPanel evidenceFilters">
              <label>
                Regime
                <select value={regime} onChange={filter("regime")}>
                  <option value="all">All regimes</option>
                  {options("regime").map((v) => (
                    <option key={v}>{v}</option>
                  ))}
                </select>
              </label>
              <label>
                Defense
                <select value={arm} onChange={filter("arm")}>
                  <option value="all">All defenses</option>
                  {options("arm").map((v) => (
                    <option key={v}>{v}</option>
                  ))}
                </select>
              </label>
              <label>
                Attacker
                <select value={setting} onChange={filter("setting")}>
                  <option value="all">All settings</option>
                  {options("setting").map((v) => (
                    <option key={v}>{v}</option>
                  ))}
                </select>
              </label>
              <p className="filterCount" aria-live="polite">
                {rows.length} of {query.data.rows.length} rows
              </p>
            </div>
            {rows.length ? (
              <div className="tableWrap" tabIndex={0} role="region" aria-label="Attack results table">
                <table>
                  <caption className="srOnly">
                    Attack results with point estimates and 95% confidence intervals
                  </caption>
                  <thead>
                    <tr>
                      <th scope="col">Regime</th>
                      <th scope="col">Defense</th>
                      <th scope="col">Setting</th>
                      <th scope="col">Task / metric</th>
                      <th scope="col">Model</th>
                      <th scope="col">Value · 95% CI</th>
                      <th scope="col">Samples</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((r, i) => (
                      <tr key={i}>
                        <td>
                          {r.regime}
                          <small>
                            {r.concurrency === null ? "Session aggregate" : `C${r.concurrency}`}
                          </small>
                        </td>
                        <td>
                          <span className="policyTag">{r.arm}</span>
                        </td>
                        <td>{r.setting}</td>
                        <td>
                          {r.task}
                          <small>{r.metric}</small>
                        </td>
                        <td>
                          {r.model}
                          <small>{r.representation}</small>
                        </td>
                        <td className="numeric">
                          {r.value === null ? "Undefined" : r.value.toFixed(3)}
                          <small>
                            {r.ci_lo === null || r.ci_hi === null
                              ? "CI unavailable"
                              : `[${r.ci_lo.toFixed(3)}, ${r.ci_hi.toFixed(3)}]`}
                          </small>
                        </td>
                        <td className="numeric">
                          {r.n}
                          <small>
                            {r.n_families} families · B={r.B}
                          </small>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <Empty>
                {query.data.rows.length
                  ? "No rows match these filters."
                  : "The result artifact has no rows. Measurements may still be pending."}
              </Empty>
            )}
          </>
        )}
      </State>
      {!experiment && <Empty>Choose an experiment to inspect measured results.</Empty>}
    </>
  );
}

const COSTS = [
  ["bandwidth", "Bandwidth overhead (%)"],
  ["ttft_p95", "TTFT p95 (ms)"],
  ["itl_p99", "ITL p99 (ms)"],
  ["cpu", "CPU"],
] as const;

export function Frontier() {
  const [params] = useSearchParams();
  const write = useParamWriter();
  const experiment = params.get("experiment") ?? "";
  const setting = params.get("setting") === "static" ? "static" : "adaptive";
  const costParam = params.get("cost") ?? "bandwidth";
  const cost = COSTS.some(([c]) => c === costParam) ? costParam : "bandwidth";
  const valid = isUlid(experiment);
  const query = useQuery({
    queryKey: ["frontier", experiment, setting, cost],
    queryFn: () =>
      api(
        `/v1/experiments/${experiment}/frontier?setting=${setting}&cost=${cost}`,
        frontierResults,
      ),
    enabled: valid,
  });
  return (
    <>
      <div className="pageIntro">
        <p className="eyebrow">PRIVACY / PERFORMANCE</p>
        <h1>Leakage vs. cost</h1>
        <p>
          Retained leakage ρ (with 95% CI whiskers) against measured overhead. Undefined
          values stay unplotted and are listed in the table.
        </p>
      </div>
      <ExperimentPicker value={experiment} onChange={(id) => write({ experiment: id })}>
        <label>
          Attacker
          <select
            value={setting}
            onChange={(e) =>
              write({ setting: e.target.value === "adaptive" ? null : e.target.value })
            }
          >
            <option value="adaptive">Adaptive</option>
            <option value="static">Static</option>
          </select>
        </label>
        <label>
          Cost axis
          <select
            value={cost}
            onChange={(e) =>
              write({ cost: e.target.value === "bandwidth" ? null : e.target.value })
            }
          >
            {COSTS.map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </label>
      </ExperimentPicker>
      {experiment && !valid && <State error={new Error("Experiment ID must be a ULID.")} />}
      <State
        loading={query.isLoading}
        error={query.error}
        loadingMessage="Loading frontier…"
        notFoundMessage="No frontier artifact exists for this experiment. Frontiers are produced by E2 analyses."
      >
        {query.data && (
          <div className="chartPanel">
            <ProvenanceNotice
              reportable={query.data.reportable}
              warning={query.data.warning}
              fallback="Reportability has not been verified."
            />
            <FrontierPlot data={query.data} />
            <div className="chartFoot">
              {query.data.rows.length} result rows · {query.data.setting} attacker
            </div>
            {query.data.rows.length > 0 && <FrontierTable data={query.data} />}
          </div>
        )}
      </State>
      {!experiment && <Empty>Choose an experiment to view its frontier.</Empty>}
    </>
  );
}

export function Reports() {
  const [params] = useSearchParams();
  const write = useParamWriter();
  const experiment = params.get("experiment") ?? "";
  const valid = isUlid(experiment);
  const report = useQuery({
    queryKey: ["report", experiment],
    queryFn: () => apiText(`/v1/experiments/${experiment}/report`),
    enabled: valid,
  });
  const download = (text: string) => {
    const blob = new Blob([text], { type: "text/markdown" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `crosstalk-${experiment}-report.md`;
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 0);
  };
  return (
    <>
      <div className="pageIntro">
        <p className="eyebrow">RESEARCH OUTPUT</p>
        <h1>Report</h1>
        <p>The generated report.md for an experiment, rendered as formatted markdown.</p>
      </div>
      <ExperimentPicker value={experiment} onChange={(id) => write({ experiment: id })} />
      {experiment && !valid && <State error={new Error("Experiment ID must be a ULID.")} />}
      <State
        loading={report.isLoading}
        error={report.error}
        loadingMessage="Loading report…"
        notFoundMessage="No report has been published for this experiment yet."
      >
        {report.data && (
          <article className="reportPanel" aria-label="Experiment report">
            <div className="reportToolbar">
              {/NOT REPORTABLE/.test(report.data) ? (
                <div className="notice warnNotice" role="status">
                  This report declares itself NOT REPORTABLE.
                </div>
              ) : (
                <span />
              )}
              <div className="reportActions">
                <Link className="outlineButton" to={`/experiments/${experiment}`}>
                  Experiment
                </Link>
                <button
                  type="button"
                  className="outlineButton"
                  onClick={() => download(report.data)}
                >
                  <Download size={16} aria-hidden="true" /> Download Markdown
                </button>
              </div>
            </div>
            <MarkdownReport markdown={report.data} />
          </article>
        )}
      </State>
      {!experiment && <Empty>Choose an experiment to load its report.</Empty>}
    </>
  );
}

const RECENT_KEY = "crosstalk.recentRequests";

function readRecent(session: string): string[] {
  try {
    const all = JSON.parse(localStorage.getItem(RECENT_KEY) ?? "{}") as Record<string, string[]>;
    return Array.isArray(all[session]) ? all[session].filter(isUlid) : [];
  } catch {
    return [];
  }
}

function rememberRequest(session: string, request: string) {
  try {
    const all = JSON.parse(localStorage.getItem(RECENT_KEY) ?? "{}") as Record<string, string[]>;
    all[session] = [request, ...(all[session] ?? []).filter((r) => r !== request)].slice(0, 10);
    localStorage.setItem(RECENT_KEY, JSON.stringify(all));
  } catch {
    // Browser storage is a convenience only.
  }
}

export function Traces() {
  const [params] = useSearchParams();
  const write = useParamWriter();
  const experiment = params.get("experiment") ?? "";
  const session = params.get("session") ?? "";
  const request = params.get("request") ?? "";
  const layer = params.get("layer") === "packets" ? "packets" : "records";
  const [sessionDraft, setSessionDraft] = useState("");
  const [requestDraft, setRequestDraft] = useState(request);
  const [oracle, setOracle] = useState(false);
  const [oracleDraft, setOracleDraft] = useState("");
  const detail = useQuery({
    queryKey: ["experiment", experiment],
    queryFn: () => api(`/v1/experiments/${experiment}`, experimentDetail),
    enabled: isUlid(experiment),
  });
  const valid = isUlid(session) && isUlid(request);
  const trace = useQuery({
    queryKey: ["trace", session, request, layer],
    queryFn: async () => {
      const result = await api(
        `/v1/sessions/${session}/traces/${request}?layer=${layer}`,
        traceResults,
      );
      rememberRequest(session, request);
      return result;
    },
    enabled: valid,
  });
  const emissions = useQuery({
    queryKey: ["oracle", session, request],
    queryFn: () =>
      api(`/v1/sessions/${session}/traces/${request}/emissions`, emissionResults, {}, true),
    enabled: valid && oracle && Boolean(credentials.get(true)),
    retry: false,
  });
  const sessions = detail.data?.sessions ?? [];
  const recent = isUlid(session) ? readRecent(session) : [];
  return (
    <>
      <div className="pageIntro">
        <p className="eyebrow">THREE-LAYER VIEW</p>
        <h1>Trace explorer</h1>
        <p>
          TLS records are primary observer evidence. Packets are secondary; emissions
          require oracle scope.
        </p>
      </div>
      <ExperimentPicker
        value={experiment}
        onChange={(id) => write({ experiment: id, session: null, request: null })}
      >
        <label>
          Session
          <select
            value={sessions.some((s) => s.id === session) ? session : ""}
            onChange={(e) => write({ session: e.target.value || null, request: null })}
            disabled={!sessions.length}
          >
            <option value="">
              {!experiment
                ? "Choose an experiment first"
                : detail.isLoading
                  ? "Loading sessions…"
                  : sessions.length
                    ? "Choose a session…"
                    : "No sessions"}
            </option>
            {sessions.map((s, i) => (
              <option key={s.id} value={s.id}>
                Session {i + 1} · {s.state} · {shortId(s.id)}
              </option>
            ))}
          </select>
        </label>
      </ExperimentPicker>
      <form
        className="filterPanel always"
        aria-label="Trace selection"
        onSubmit={(e) => {
          e.preventDefault();
          const nextSession = isUlid(sessionDraft) ? sessionDraft : session;
          write({ session: nextSession || null, request: requestDraft || null });
          setSessionDraft("");
        }}
      >
        {!sessions.some((s) => s.id === session) && (
          <label>
            Session ID
            <input
              value={sessionDraft || session}
              onChange={(e) => setSessionDraft(e.target.value.trim().toUpperCase())}
              placeholder="Paste a session ULID"
              spellCheck={false}
            />
          </label>
        )}
        <label>
          Request ID
          <input
            value={requestDraft}
            onChange={(e) => setRequestDraft(e.target.value.trim().toUpperCase())}
            placeholder="Paste a request ULID"
            list="recent-requests"
            spellCheck={false}
            aria-describedby="request-help"
            aria-invalid={requestDraft !== "" && !isUlid(requestDraft)}
          />
          <datalist id="recent-requests">
            {recent.map((r) => (
              <option key={r} value={r} />
            ))}
          </datalist>
        </label>
        <label>
          Observer layer
          <select value={layer} onChange={(e) => write({ layer: e.target.value === "packets" ? "packets" : null })}>
            <option value="records">TLS records</option>
            <option value="packets">Network packets</option>
          </select>
        </label>
        <button
          className="primary"
          disabled={!(isUlid(sessionDraft) || isUlid(session)) || !isUlid(requestDraft)}
        >
          Load trace
        </button>
        <p className="helper fullRow" id="request-help">
          The API does not list a session&apos;s request IDs yet. Paste one from the
          session&apos;s plan or flow map (for imported campaigns, see{" "}
          <code>docs/evidence/campaign/campaign.json</code>). Requests you open are
          remembered in this browser for quick reuse.
        </p>
      </form>
      {(session || request) && !valid && !trace.data && (
        <div className="notice" role="status">
          {isUlid(session) ? "Enter a request ID to load its trace." : "Choose or paste a session."}
        </div>
      )}
      <State loading={trace.isLoading} error={trace.error} loadingMessage="Loading trace…"
        notFoundMessage="No observer artifacts exist for this session yet.">
        {trace.data && (
          <>
            <ProvenanceNotice
              reportable={trace.data.reportable}
              warning={trace.data.warning}
              fallback="Session provenance has not been verified."
            />
            <div className="traceStats">
              <span>
                Session <strong className="mono">{shortId(trace.data.session_id)}</strong>
              </span>
              <span>
                Request <strong className="mono">{shortId(trace.data.request_id)}</strong>
              </span>
              <span>
                <strong>{trace.data.source_points.toLocaleString()}</strong>{" "}
                {trace.data.layer === "packets" ? "packets" : "TLS records"}
              </span>
              <span>
                <strong>{trace.data.points.length.toLocaleString()}</strong> rendered{" "}
                {trace.data.downsampled ? "bins" : "points"}
              </span>
              <span>{trace.data.downsampled ? "Server downsampled" : "Exact points"}</span>
            </div>
            <TracePlot trace={trace.data} />
            <div className="oraclePanel">
              <div>
                <p className="eyebrow">ORACLE · VALIDATION ONLY</p>
                <h2>Application emissions</h2>
                <p>Never attacker visible. The API checks oracle scope independently.</p>
              </div>
              <button
                type="button"
                className="outlineButton"
                onClick={() => setOracle(!oracle)}
                aria-expanded={oracle}
              >
                {oracle ? <EyeOff size={16} aria-hidden="true" /> : <Eye size={16} aria-hidden="true" />}{" "}
                {oracle ? "Hide oracle" : "Request oracle view"}
              </button>
            </div>
            {oracle && !credentials.get(true) && (
              <form
                className="oracleAuth"
                onSubmit={(e) => {
                  e.preventDefault();
                  credentials.set(oracleDraft, true);
                  setOracleDraft("");
                }}
              >
                <label>
                  Oracle token
                  <input
                    type="password"
                    minLength={32}
                    required
                    value={oracleDraft}
                    onChange={(e) => setOracleDraft(e.target.value)}
                    autoComplete="off"
                  />
                </label>
                <button className="primary">Authorize</button>
              </form>
            )}
            {oracle && emissions.isLoading && (
              <State loading loadingMessage="Loading emissions…" />
            )}
            {oracle && emissions.error && (
              <div className="notice error" role="alert">
                {emissions.error instanceof ApiError && emissions.error.status === 403
                  ? "Oracle access denied by the API."
                  : errorMessage(emissions.error)}
              </div>
            )}
            {oracle &&
              emissions.data &&
              (emissions.data.rows.length ? (
                <div className="tableWrap" tabIndex={0} role="region" aria-label="Oracle emissions">
                  <table>
                    <thead>
                      <tr>
                        <th scope="col">Seq</th>
                        <th scope="col">Time (ns)</th>
                        <th scope="col">Kind</th>
                        <th scope="col">Framed (B)</th>
                        <th scope="col">Content (B)</th>
                        <th scope="col">Padding (B)</th>
                        <th scope="col">Arm</th>
                      </tr>
                    </thead>
                    <tbody>
                      {emissions.data.rows.map((row) => (
                        <tr key={row.seq}>
                          <td>{row.seq}</td>
                          <td className="numeric">{row.t_emit_ns.toString()}</td>
                          <td>{row.kind}</td>
                          <td className="numeric">{row.framed_len}</td>
                          <td className="numeric">{row.content_bytes}</td>
                          <td className="numeric">{row.pad_bytes}</td>
                          <td>{row.arm}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <Empty>No emissions for this request.</Empty>
              ))}
          </>
        )}
      </State>
      {!session && !request && (
        <Empty>
          <Activity size={24} aria-hidden="true" /> Choose an experiment and session, then
          paste a request ID to inspect observer evidence.
        </Empty>
      )}
    </>
  );
}
