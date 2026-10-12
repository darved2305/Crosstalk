import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Activity, Eye, EyeOff } from "lucide-react";
import { useSearchParams } from "react-router-dom";
import { api, apiText, ApiError, credentials } from "../lib/client";
import {
  emissionResults,
  evaluationResults,
  frontierResults,
  traceResults,
  ulid,
} from "../lib/schemas";
import { Empty, State } from "./common";
import { FrontierPlot } from "../components/frontier-plot";
import { TracePlot } from "../components/trace-plot";

function ExperimentSelector({
  value,
  onChange,
}: {
  value: string;
  onChange: (value: string) => void;
}) {
  return (
    <label>
      Experiment ID
      <input
        value={value}
        onChange={(e) => onChange(e.target.value.trim().toUpperCase())}
        placeholder="01…"
      />
    </label>
  );
}

export function Results() {
  const [params, setParams] = useSearchParams();
  const experiment = params.get("experiment") ?? "";
  const [draft, setDraft] = useState(experiment);
  const [regime, setRegime] = useState("all");
  const [arm, setArm] = useState("all");
  const [setting, setSetting] = useState("all");
  const valid = ulid.safeParse(experiment).success;
  const query = useQuery({
    queryKey: ["results", experiment],
    queryFn: () =>
      api(`/v1/experiments/${experiment}/results`, evaluationResults),
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
  return (
    <>
      <div className="pageIntro">
        <p className="eyebrow">ATTACK EVIDENCE</p>
        <h1>Attack results</h1>
        <p>
          E0–E2 measurements, static and adaptive attackers, and confidence
          intervals.
        </p>
      </div>
      <form
        className="filterPanel always"
        onSubmit={(e) => {
          e.preventDefault();
          setParams({ experiment: draft });
        }}
      >
        <ExperimentSelector value={draft} onChange={setDraft} />
        <button className="primary" disabled={!ulid.safeParse(draft).success}>
          Load results
        </button>
      </form>
      {experiment && !valid && (
        <State error={new Error("Experiment ID must be a ULID.")} />
      )}
      <State
        loading={query.isLoading}
        error={query.error}
        notFoundMessage="No result artifact exists yet for this experiment."
      >
        {query.data && (
          <>
            {query.data.reportable !== true && (
              <div className="notice" role="status">{query.data.warning ?? "Reportability has not been verified."}</div>
            )}
            <div className="filterPanel evidenceFilters">
              <label>
                Regime
                <select
                  value={regime}
                  onChange={(e) => setRegime(e.target.value)}
                >
                  <option value="all">All regimes</option>
                  {options("regime").map((v) => (
                    <option key={v}>{v}</option>
                  ))}
                </select>
              </label>
              <label>
                Defense
                <select value={arm} onChange={(e) => setArm(e.target.value)}>
                  <option value="all">All defenses</option>
                  {options("arm").map((v) => (
                    <option key={v}>{v}</option>
                  ))}
                </select>
              </label>
              <label>
                Attacker
                <select
                  value={setting}
                  onChange={(e) => setSetting(e.target.value)}
                >
                  <option value="all">All settings</option>
                  {options("setting").map((v) => (
                    <option key={v}>{v}</option>
                  ))}
                </select>
              </label>
            </div>
            {rows.length ? (
              <div className="tableWrap">
                <table>
                  <thead>
                    <tr>
                      <th>Regime</th>
                      <th>Defense</th>
                      <th>Setting</th>
                      <th>Task / metric</th>
                      <th>Model</th>
                      <th>Value · 95% CI</th>
                      <th>Samples</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((r, i) => (
                      <tr key={i}>
                        <td>
                          {r.regime}
                          <small>{r.concurrency === null ? "Session aggregate" : `C${r.concurrency}`}</small>
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
                          {r.value === null
                            ? "Not reportable"
                            : r.value.toFixed(3)}
                          <small>
                            {r.ci_lo === null || r.ci_hi === null
                              ? "CI unavailable"
                              : `[${r.ci_lo.toFixed(3)}, ${r.ci_hi.toFixed(3)}]`}
                          </small>
                        </td>
                        <td>
                          {r.n}
                          <small>{r.n_families} families</small>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <Empty>
                No matching attack results. Measurements may still be pending.
              </Empty>
            )}
          </>
        )}
      </State>
      {!experiment && (
        <Empty>Enter an experiment ID to inspect measured results.</Empty>
      )}
    </>
  );
}

export function Frontier() {
  const [params, setParams] = useSearchParams();
  const experiment = params.get("experiment") ?? "";
  const [draft, setDraft] = useState(experiment);
  const [setting, setSetting] = useState(
    params.get("setting") === "static" ? "static" : "adaptive",
  );
  const [cost, setCost] = useState(params.get("cost") ?? "bandwidth");
  const valid = ulid.safeParse(experiment).success;
  const query = useQuery({
    queryKey: [
      "frontier",
      experiment,
      params.get("setting"),
      params.get("cost"),
    ],
    queryFn: () =>
      api(
        `/v1/experiments/${experiment}/frontier?setting=${encodeURIComponent(params.get("setting") ?? "adaptive")}&cost=${encodeURIComponent(params.get("cost") ?? "bandwidth")}`,
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
          Retained leakage ρ against measured overhead. Undefined values remain
          unplotted.
        </p>
      </div>
      <form
        className="filterPanel always"
        onSubmit={(e) => {
          e.preventDefault();
          setParams({ experiment: draft, setting, cost });
        }}
      >
        <ExperimentSelector value={draft} onChange={setDraft} />
        <label>
          Attacker
          <select value={setting} onChange={(e) => setSetting(e.target.value)}>
            <option value="adaptive">Adaptive</option>
            <option value="static">Static</option>
          </select>
        </label>
        <label>
          Cost axis
          <select value={cost} onChange={(e) => setCost(e.target.value)}>
            <option value="bandwidth">Bandwidth</option>
            <option value="ttft_p95">TTFT p95</option>
            <option value="itl_p99">ITL p99</option>
            <option value="cpu">CPU</option>
          </select>
        </label>
        <button className="primary" disabled={!ulid.safeParse(draft).success}>
          Update
        </button>
      </form>
      {experiment && !valid && (
        <State error={new Error("Experiment ID must be a ULID.")} />
      )}
      <State loading={query.isLoading} error={query.error}>
        {query.data && (
          <div className="chartPanel">
            {query.data.reportable !== true && (
              <div className="notice" role="status">{query.data.warning ?? "Reportability has not been verified."}</div>
            )}
            <FrontierPlot data={query.data} />
            <div className="chartFoot">
              {query.data.rows.length} result rows · {query.data.setting}{" "}
              attacker
            </div>
          </div>
        )}
      </State>
      {!experiment && (
        <Empty>Enter an experiment ID to view its frontier.</Empty>
      )}
    </>
  );
}

export function Reports() {
  const [params, setParams] = useSearchParams();
  const experiment = params.get("experiment") ?? "";
  const [draft, setDraft] = useState(experiment);
  const valid = ulid.safeParse(experiment).success;
  const report = useQuery({
    queryKey: ["report", experiment],
    queryFn: () => apiText(`/v1/experiments/${experiment}/report`),
    enabled: valid,
  });
  return (
    <>
      <div className="pageIntro">
        <p className="eyebrow">RESEARCH OUTPUT</p>
        <h1>Report</h1>
        <p>Read the generated report artifact for an experiment.</p>
      </div>
      <form
        className="filterPanel always"
        onSubmit={(e) => {
          e.preventDefault();
          setParams({ experiment: draft });
        }}
      >
        <ExperimentSelector value={draft} onChange={setDraft} />
        <button className="primary" disabled={!ulid.safeParse(draft).success}>
          Load report
        </button>
      </form>
      {experiment && !valid && (
        <State error={new Error("Experiment ID must be a ULID.")} />
      )}
      <State loading={report.isLoading} error={report.error}>
        {report.data && (
          <div className="reportPanel">
            <pre>{report.data}</pre>
            <button
              className="outlineButton"
              onClick={() => {
                const blob = new Blob([report.data], { type: "text/markdown" });
                const url = URL.createObjectURL(blob);
                const link = document.createElement("a");
                link.href = url;
                link.download = `crosstalk-${experiment}-report.md`;
                link.click();
                URL.revokeObjectURL(url);
              }}
            >
              Download Markdown
            </button>
          </div>
        )}
      </State>
      {!experiment && <Empty>Enter an experiment ID to load its report.</Empty>}
    </>
  );
}

export function Traces() {
  const [params, setParams] = useSearchParams();
  const session = params.get("session") ?? "";
  const request = params.get("request") ?? "";
  const [sessionDraft, setSessionDraft] = useState(session);
  const [requestDraft, setRequestDraft] = useState(request);
  const [layer, setLayer] = useState(
    params.get("layer") === "packets" ? "packets" : "records",
  );
  const [oracle, setOracle] = useState(false);
  const [oracleDraft, setOracleDraft] = useState("");
  const valid =
    ulid.safeParse(session).success && ulid.safeParse(request).success;
  const trace = useQuery({
    queryKey: ["trace", session, request, params.get("layer")],
    queryFn: () =>
      api(
        `/v1/sessions/${session}/traces/${request}?layer=${params.get("layer") === "packets" ? "packets" : "records"}`,
        traceResults,
      ),
    enabled: valid,
  });
  const emissions = useQuery({
    queryKey: ["oracle", session, request],
    queryFn: () =>
      api(
        `/v1/sessions/${session}/traces/${request}/emissions`,
        emissionResults,
        {},
        true,
      ),
    enabled: valid && oracle && Boolean(credentials.get(true)),
    retry: false,
  });
  return (
    <>
      <div className="pageIntro">
        <p className="eyebrow">THREE-LAYER VIEW</p>
        <h1>Trace explorer</h1>
        <p>
          TLS records are primary observer evidence. Packets are secondary;
          emissions require oracle scope.
        </p>
      </div>
      <form
        className="filterPanel always"
        onSubmit={(e) => {
          e.preventDefault();
          setParams({ session: sessionDraft, request: requestDraft, layer });
        }}
      >
        <label>
          Session ID
          <input
            value={sessionDraft}
            onChange={(e) =>
              setSessionDraft(e.target.value.trim().toUpperCase())
            }
            placeholder="01…"
          />
        </label>
        <label>
          Request ID
          <input
            value={requestDraft}
            onChange={(e) =>
              setRequestDraft(e.target.value.trim().toUpperCase())
            }
            placeholder="01…"
          />
        </label>
        <label>
          Observer layer
          <select
            value={layer}
            onChange={(e) => setLayer(e.target.value as "records" | "packets")}
          >
            <option value="records">TLS records</option>
            <option value="packets">Network packets</option>
          </select>
        </label>
        <button
          className="primary"
          disabled={
            !ulid.safeParse(sessionDraft).success ||
            !ulid.safeParse(requestDraft).success
          }
        >
          Load trace
        </button>
      </form>
      {(session || request) && !valid && (
        <State error={new Error("Session and request IDs must be ULIDs.")} />
      )}
      <State loading={trace.isLoading} error={trace.error}>
        {trace.data && (
          <>
            {trace.data.reportable !== true && (
              <div className="notice" role="status">{trace.data.warning ?? "Session provenance has not been verified."}</div>
            )}
            <div className="traceStats">
              <span>
                <strong>{trace.data.source_points.toLocaleString()}</strong>{" "}
                source points
              </span>
              <span>
                <strong>{trace.data.points.length.toLocaleString()}</strong>{" "}
                rendered bins
              </span>
              <span>
                {trace.data.downsampled ? "Server downsampled" : "Exact points"}
              </span>
            </div>
            <TracePlot trace={trace.data} />
            <div className="oraclePanel">
              <div>
                <p className="eyebrow">ORACLE · VALIDATION ONLY</p>
                <h2>Application emissions</h2>
                <p>
                  Never attacker visible. The API checks oracle scope
                  independently.
                </p>
              </div>
              <button
                className="outlineButton"
                onClick={() => setOracle(!oracle)}
              >
                {oracle ? <EyeOff size={16} /> : <Eye size={16} />}{" "}
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
            {oracle && emissions.error && (
              <div className="notice error">
                {emissions.error instanceof ApiError &&
                emissions.error.status === 403
                  ? "Oracle access denied by the API."
                  : emissions.error.message}
              </div>
            )}
            {oracle &&
              emissions.data &&
              (emissions.data.rows.length ? (
                <div className="tableWrap">
                  <table>
                    <thead>
                      <tr>
                        <th>Seq</th>
                        <th>Time (ns)</th>
                        <th>Kind</th>
                        <th>Framed</th>
                        <th>Content</th>
                        <th>Padding</th>
                      </tr>
                    </thead>
                    <tbody>
                      {emissions.data.rows.map((row) => (
                        <tr key={row.seq}>
                          <td>{row.seq}</td>
                          <td>{row.t_emit_ns.toString()}</td>
                          <td>{row.kind}</td>
                          <td>{row.framed_len}</td>
                          <td>{row.content_bytes}</td>
                          <td>{row.pad_bytes}</td>
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
          <Activity size={24} /> Enter a session and request ID to inspect
          observer evidence.
        </Empty>
      )}
    </>
  );
}
