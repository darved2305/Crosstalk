import Link from "next/link";

import { Empty } from "@/components/empty";
import { Status } from "@/components/status";
import { apiGet } from "@/lib/api";
import { experimentPage } from "@/lib/schemas";

export default async function RunsPage() {
  const data = await apiGet("/v1/experiments?limit=50", experimentPage);
  return (
    <section>
      <div className="sectionHead">
        <div><p className="eyebrow">CONTROL PLANE</p><h2>Experiments and runs</h2></div>
        <p>Durable stage state, validation outcomes, and artifact-backed results.</p>
      </div>
      {!data.items.length ? (
        <Empty>No experiments have been created. Start one through the authenticated API.</Empty>
      ) : (
        <div className="tableWrap">
          <table>
            <thead><tr><th>Experiment</th><th>State</th><th>Created</th><th>Config</th><th>Views</th></tr></thead>
            <tbody>
              {data.items.map((item) => (
                <tr key={item.id}>
                  <td><strong>{item.name}</strong><code>{item.id}</code></td>
                  <td><Status value={item.state} /></td>
                  <td>{new Date(item.created_at).toLocaleString()}</td>
                  <td><code>{item.config_hash.slice(0, 12)}</code></td>
                  <td className="links"><Link href={`/results?experiment=${item.id}`}>Results</Link><Link href={`/frontier?experiment=${item.id}`}>Frontier</Link></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
