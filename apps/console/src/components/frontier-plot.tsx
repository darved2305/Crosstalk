import type { FrontierResults } from "@/lib/schemas";

const costField = {
  bandwidth: "bandwidth_overhead_pct",
  ttft_p95: "ttft_p95_ms",
  itl_p99: "itl_p99_ms",
} as const;

export function FrontierPlot({ data }: { data: FrontierResults }) {
  const field = costField[data.cost as keyof typeof costField];
  const rows = field ? data.rows.filter((row) => row.rho !== null && row[field] !== null) : [];
  if (!rows.length) return <div className="emptyState">{data.cost === "cpu" ? "The current frontier contract does not expose CPU cost values." : data.rows.length ? "Retained leakage is undefined for this selection. Consult the report's baseline and noise-floor checks." : "No frontier measurements for this selection."}</div>;
  const width = 800;
  const height = 380;
  const minX = Math.min(0, ...rows.map((row) => row[field] ?? 0));
  const maxX = Math.max(minX + 1, ...rows.map((row) => row[field] ?? 0));
  const minY = Math.min(0, ...rows.map((row) => row.rho_lo ?? row.rho ?? 0));
  const maxY = Math.max(1, ...rows.map((row) => row.rho_hi ?? row.rho ?? 0));
  const x = (value: number) => 60 + ((value - minX) / (maxX - minX)) * 660;
  const y = (value: number) => 320 - ((value - minY) / (maxY - minY)) * 280;
  return (
    <svg className="frontierPlot" viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Leakage versus cost frontier">
      <line x1="60" y1="20" x2="60" y2="330" className="axis" />
      <line x1="60" y1="330" x2="780" y2="330" className="axis" />
      {rows.map((row) => {
        const cx = x(row[field] ?? 0);
        const cy = y(row.rho ?? 0);
        return (
          <g key={`${row.condition}-${row.concurrency}`}>
            {row.rho_lo !== null && row.rho_hi !== null && (
              <line x1={cx} y1={y(row.rho_lo)} x2={cx} y2={y(row.rho_hi)} stroke="currentColor" />
            )}
            <circle cx={cx} cy={cy} r="7" />
            <text x={cx + 10} y={cy - 8}>{row.condition} · C{row.concurrency}</text>
          </g>
        );
      })}
      <text x="350" y="372">{data.cost}</text>
      <text x="60" y="350">{minX.toFixed(1)}</text>
      <text x="700" y="350">{maxX.toFixed(1)}</text>
      <text x="12" y="320">{minY.toFixed(2)}</text>
      <text x="12" y="40">{maxY.toFixed(2)}</text>
      <text x="8" y="24">ρ</text>
    </svg>
  );
}
