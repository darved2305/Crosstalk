import Link from "next/link";

import { Empty } from "@/components/empty";
import { TracePlot } from "@/components/trace-plot";
import { apiGet, ApiError } from "@/lib/api";
import { emissionResults, traceResults, ulid } from "@/lib/schemas";

type Search = Promise<Record<string, string | string[] | undefined>>;

export default async function TracesPage({ searchParams }: { searchParams: Search }) {
  const query = await searchParams;
  const session = typeof query.session === "string" ? query.session : "";
  const request = typeof query.request === "string" ? query.request : "";
  const layer = query.layer === "packets" ? "packets" : "records";
  const oracle = query.oracle === "1";
  const valid = ulid.safeParse(session).success && ulid.safeParse(request).success;
  const trace = valid
    ? await apiGet(`/v1/sessions/${session}/traces/${request}?layer=${layer}`, traceResults)
    : null;
  let emissions: Awaited<ReturnType<typeof loadEmissions>> | null = null;
  if (valid && oracle) emissions = await loadEmissions(session, request);
  return (
    <section>
      <div className="sectionHead"><div><p className="eyebrow">THREE-LAYER VIEW</p><h2>Trace explorer</h2></div><p>TLS records are the primary observer view; packets are secondary.</p></div>
      <form className="filters">
        <label>Session ID<input name="session" defaultValue={session} required /></label>
        <label>Request ID<input name="request" defaultValue={request} required /></label>
        <label>Observer layer<select name="layer" defaultValue={layer}><option value="records">TLS records</option><option value="packets">Network packets</option></select></label>
        <button type="submit">Load trace</button>
      </form>
      {!trace ? <Empty>Enter a session and request ULID to inspect observer evidence.</Empty> : (
        <>
          <div className="metricStrip"><span><strong>{trace.source_points.toLocaleString()}</strong> source points</span><span><strong>{trace.points.length.toLocaleString()}</strong> rendered bins</span><span>{trace.downsampled ? "Server-downsampled" : "Exact points"}</span></div>
          <TracePlot trace={trace} />
          <div className="oraclePanel">
            <div><p className="eyebrow">ORACLE · VALIDATION ONLY</p><h3>Application emissions</h3><p>Never attacker-visible. Access requires backend oracle scope.</p></div>
            <Link className="button secondary" href={`/traces?session=${session}&request=${request}&layer=${layer}&oracle=${oracle ? "0" : "1"}`}>{oracle ? "Hide oracle" : "Request oracle view"}</Link>
          </div>
          {oracle && emissions?.kind === "denied" && <div className="warning">Oracle access was denied by the API. A UI query parameter cannot grant scope.</div>}
          {oracle && emissions?.kind === "ok" && <div className="tableWrap"><table><thead><tr><th>Seq</th><th>Time (ns)</th><th>Kind</th><th>Framed</th><th>Content</th><th>Padding</th></tr></thead><tbody>{emissions.rows.map((row) => <tr key={row.seq}><td>{row.seq}</td><td>{row.t_emit_ns}</td><td>{row.kind}</td><td>{row.framed_len}</td><td>{row.content_bytes}</td><td>{row.pad_bytes}</td></tr>)}</tbody></table></div>}
        </>
      )}
    </section>
  );
}

async function loadEmissions(session: string, request: string) {
  try {
    const data = await apiGet(`/v1/sessions/${session}/traces/${request}/emissions`, emissionResults, true);
    return { kind: "ok" as const, rows: data.rows };
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) return { kind: "denied" as const };
    throw error;
  }
}
