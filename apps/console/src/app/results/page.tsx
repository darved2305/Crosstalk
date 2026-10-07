import { Empty } from "@/components/empty";
import { apiGet } from "@/lib/api";
import { evaluationResults, ulid } from "@/lib/schemas";

type Search = Promise<Record<string, string | string[] | undefined>>;

export default async function ResultsPage({ searchParams }: { searchParams: Search }) {
  const query = await searchParams;
  const experiment = typeof query.experiment === "string" ? query.experiment : "";
  const valid = ulid.safeParse(experiment).success;
  const data = valid ? await apiGet(`/v1/experiments/${experiment}/results`, evaluationResults) : null;
  const rows = data?.rows.filter((row) => row.metric === "balanced_accuracy") ?? [];
  return (
    <section>
      <div className="sectionHead"><div><p className="eyebrow">ATTACK EVIDENCE</p><h2>Static and adaptive results</h2></div><p>Adaptive is the headline. Intervals and reportability context stay visible.</p></div>
      <form className="filters compact"><label>Experiment ID<input name="experiment" defaultValue={experiment} required /></label><button>Load results</button></form>
      {!data ? <Empty>Enter an experiment ULID.</Empty> : !rows.length ? <Empty>No balanced-accuracy results exist for this experiment.</Empty> : (
        <div className="tableWrap"><table><thead><tr><th>Regime</th><th>Arm</th><th>Attacker</th><th>Model / representation</th><th>Balanced accuracy · 95% CI</th><th>n / families</th></tr></thead><tbody>{rows.map((row) => <tr key={`${row.regime}-${row.arm}-${row.setting}-${row.model}-${row.representation}`}><td>{row.regime}<small>C{row.concurrency}</small></td><td><strong>{row.arm}</strong></td><td><span className={`status ${row.setting === "adaptive" ? "adaptive" : "neutral"}`}>{row.setting}</span></td><td>{row.model}<small>{row.representation}</small></td><td className="numeric">{format(row.value)} <small>[{format(row.ci_lo)}, {format(row.ci_hi)}]</small></td><td>{row.n}<small>{row.n_families} families</small></td></tr>)}</tbody></table></div>
      )}
    </section>
  );
}

function format(value: number | null) { return value === null ? "not reportable" : value.toFixed(3); }
