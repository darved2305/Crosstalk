import { Empty } from "@/components/empty";
import { FrontierPlot } from "@/components/frontier-plot";
import { apiGet } from "@/lib/api";
import { frontierResults, ulid } from "@/lib/schemas";

type Search = Promise<Record<string, string | string[] | undefined>>;

export default async function FrontierPage({ searchParams }: { searchParams: Search }) {
  const query = await searchParams;
  const experiment = typeof query.experiment === "string" ? query.experiment : "";
  const setting = query.setting === "static" ? "static" : "adaptive";
  const allowedCosts = new Set(["bandwidth", "ttft_p95", "itl_p99", "cpu"]);
  const cost = typeof query.cost === "string" && allowedCosts.has(query.cost) ? query.cost : "bandwidth";
  const data = ulid.safeParse(experiment).success ? await apiGet(`/v1/experiments/${experiment}/frontier?setting=${setting}&cost=${cost}`, frontierResults) : null;
  return (
    <section>
      <div className="sectionHead"><div><p className="eyebrow">PRIVACY / PERFORMANCE</p><h2>Leakage-versus-cost frontier</h2></div><p>Retained leakage ρ is undefined when D0 cannot be distinguished from chance.</p></div>
      <form className="filters"><label>Experiment ID<input name="experiment" defaultValue={experiment} required /></label><label>Attacker<select name="setting" defaultValue={setting}><option>adaptive</option><option>static</option></select></label><label>Cost axis<select name="cost" defaultValue={cost}><option value="bandwidth">Bandwidth</option><option value="ttft_p95">TTFT p95</option><option value="itl_p99">ITL p99</option><option value="cpu">CPU</option></select></label><button>Update</button></form>
      {!data ? <Empty>Enter an experiment ULID.</Empty> : <FrontierPlot data={data} />}
    </section>
  );
}
