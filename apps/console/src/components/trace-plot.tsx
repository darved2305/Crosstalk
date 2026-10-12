import type { TraceResults } from "@/lib/schemas";

export function TracePlot({ trace }: { trace: TraceResults }) {
  if (!trace.points.length) return <div className="emptyState">No observer points for this request.</div>;
  const width = 900;
  const height = 280;
  const times = trace.points.flatMap((point) => [point.t_start_ns, point.t_end_ns]);
  const values = trace.points.flatMap((point) => [point.min_value, point.max_value]);
  const minTime = times.reduce((a, b) => a < b ? a : b);
  const maxTime = times.reduce((a, b) => a > b ? a : b);
  const span = Math.max(1, Number(maxTime - minTime));
  const amplitude = Math.max(1, ...values.map(Math.abs));
  const x = (time: bigint) => 24 + (Number(time - minTime) / span) * (width - 48);
  const y = (value: number) => height / 2 - (value / amplitude) * (height / 2 - 24);
  return (
    <svg className="tracePlot" viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`${trace.layer} signed-byte timeline`}>
      <line x1="24" x2={width - 24} y1={height / 2} y2={height / 2} className="axis" />
      {trace.points.map((point, index) => (
        <g key={`${point.t_start_ns}-${index}`}>
        <line
          x1={x(point.t_start_ns)} x2={x(point.t_start_ns)}
          y1={height / 2} y2={y(point.min_value)}
          className={point.max_value >= 0 ? "server" : "client"}
          opacity="0.35"
        />
        <line
          x1={x(point.t_start_ns)}
          x2={x(point.t_end_ns)}
          y1={y(point.min_value)}
          y2={y(point.max_value)}
          className={point.max_value >= 0 ? "server" : "client"}
        />
        <circle cx={x(point.t_start_ns)} cy={y(point.min_value)} r="2.5"
          fill={point.max_value >= 0 ? "#07864c" : "#e98124"} />
        </g>
      ))}
      <text x="24" y={height - 5} fontSize="10">0 ms</text>
      <text x={width - 100} y={height - 5} fontSize="10">{(span / 1_000_000).toFixed(2)} ms</text>
    </svg>
  );
}
