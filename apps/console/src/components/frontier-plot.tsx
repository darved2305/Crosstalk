import type { FrontierResults } from "@/lib/schemas";

const costField = {
  bandwidth: "bandwidth_overhead_pct",
  ttft_p95: "ttft_p95_ms",
  itl_p99: "itl_p99_ms",
} as const;

export function FrontierPlot({ data }: { data: FrontierResults }) {
  const field = costField[data.cost as keyof typeof costField];
  const rows = field ? data.rows.filter((row) => row.rho !== null && row[field] !== null) : [];
  if (!rows.length) return <div className="emptyState">{data.cost === "cpu" ? "The current frontier contract does not expose CPU cost values." : "No reportable frontier points for this selection."}</div>;
  const width = 800;
  const height = 380;
  const maxX = Math.max(1, ...rows.map((row) => row[field] ?? 0));
  return (
    <svg className="frontierPlot" viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Leakage versus cost frontier">
      <line x1="60" y1="20" x2="60" y2="330" className="axis" />
      <line x1="60" y1="330" x2="780" y2="330" className="axis" />
      {rows.map((row) => {
        const cx = 60 + ((row[field] ?? 0) / maxX) * 700;
        const cy = 330 - Math.max(0, Math.min(1.1, row.rho ?? 0)) * 280;
        return (
          <g key={`${row.condition}-${row.concurrency}`}>
            <circle cx={cx} cy={cy} r="7" />
            <text x={cx + 10} y={cy - 8}>{row.condition} · C{row.concurrency}</text>
          </g>
        );
      })}
      <text x="350" y="372">{data.cost}</text>
      <text x="8" y="24">ρ</text>
    </svg>
  );
}
