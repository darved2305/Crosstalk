import type { FrontierResults } from "../lib/schemas";
import { niceTicks } from "./ticks";

type Row = FrontierResults["rows"][number];

const COST = {
  bandwidth: { field: "bandwidth_overhead_pct", label: "Bandwidth overhead (%)", unit: "%" },
  ttft_p95: { field: "ttft_p95_ms", label: "Time to first token, p95 (ms)", unit: "ms" },
  itl_p99: { field: "itl_p99_ms", label: "Inter-token latency, p99 (ms)", unit: "ms" },
} as const;

/** Categorical series colours in fixed order (validated light palette). */
const SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100"];
const MARKERS = ["circle", "square", "diamond", "triangle"] as const;

const fmt = (value: number | null, digits = 2) => (value === null ? "—" : value.toFixed(digits));

function Marker({ shape, x, y, color }: { shape: (typeof MARKERS)[number]; x: number; y: number; color: string }) {
  const common = { fill: color, stroke: "#fff", strokeWidth: 2 };
  if (shape === "square") return <rect x={x - 6} y={y - 6} width="12" height="12" rx="2" {...common} />;
  if (shape === "diamond") return <path d={`M${x} ${y - 8}L${x + 8} ${y}L${x} ${y + 8}L${x - 8} ${y}Z`} {...common} />;
  if (shape === "triangle") return <path d={`M${x} ${y - 8}L${x + 8} ${y + 6}L${x - 8} ${y + 6}Z`} {...common} />;
  return <circle cx={x} cy={y} r="7" {...common} />;
}

export function FrontierPlot({ data }: { data: FrontierResults }) {
  const cost = COST[data.cost as keyof typeof COST];
  const field = cost?.field;
  const rows = field ? data.rows.filter((row) => row.rho !== null && row[field] !== null) : [];
  if (!rows.length)
    return (
      <div className="emptyState" role="note">
        {!cost
          ? "The current frontier contract does not expose CPU cost values; choose another cost axis."
          : data.rows.length
            ? `Retained leakage ρ is undefined for all ${data.rows.length} rows in this selection, so nothing is plotted. The measured costs are listed below; consult the report's baseline and noise-floor checks.`
            : "No frontier measurements for this selection."}
      </div>
    );
  const width = 800;
  const height = 400;
  const left = 70;
  const right = 780;
  const top = 24;
  const bottom = 330;
  const xs = rows.map((row) => row[field] as number);
  const ys = rows.flatMap((row) => [row.rho, row.rho_lo, row.rho_hi].filter((v): v is number => v !== null));
  const xTicks = niceTicks(Math.min(0, ...xs), Math.max(...xs, Math.min(0, ...xs) + 1));
  const yTicks = niceTicks(Math.min(0, ...ys), Math.max(1, ...ys));
  const [minX, maxX] = [xTicks[0]!, xTicks.at(-1)!];
  const [minY, maxY] = [yTicks[0]!, yTicks.at(-1)!];
  const x = (value: number) => left + ((value - minX) / (maxX - minX)) * (right - left);
  const y = (value: number) => bottom - ((value - minY) / (maxY - minY)) * (bottom - top);
  const levels = [...new Set(rows.map((r) => r.concurrency))].sort((a, b) => a - b);
  const style = (row: Row) => {
    const i = Math.min(levels.indexOf(row.concurrency), SERIES.length - 1);
    return { color: SERIES[i]!, shape: MARKERS[i]! };
  };
  const plotted = rows.length;
  const skipped = data.rows.length - plotted;
  return (
    <figure className="chartFigure">
      <svg
        className="frontierPlot"
        viewBox={`0 0 ${width} ${height}`}
        role="img"
        aria-labelledby="frontier-title frontier-desc"
      >
        <title id="frontier-title">{`Retained leakage ρ versus ${cost.label}, ${data.setting} attacker`}</title>
        <desc id="frontier-desc">
          {plotted} conditions plotted with 95% confidence whiskers
          {skipped ? `; ${skipped} rows with undefined values are not plotted` : ""}. Lower and
          further left is better.
        </desc>
        {yTicks.map((t) => (
          <g key={`y${t}`} className="tick">
            <line x1={left} x2={right} y1={y(t)} y2={y(t)} className="grid" />
            <text x={left - 8} y={y(t) + 4} textAnchor="end">
              {t}
            </text>
          </g>
        ))}
        {xTicks.map((t) => (
          <g key={`x${t}`} className="tick">
            <line x1={x(t)} x2={x(t)} y1={bottom} y2={bottom + 5} className="axis" />
            <text x={x(t)} y={bottom + 20} textAnchor="middle">
              {t}
            </text>
          </g>
        ))}
        {minY <= 1 && maxY >= 1 && (
          <g className="reference">
            <line x1={left} x2={right} y1={y(1)} y2={y(1)} />
            <text x={right} y={y(1) - 6} textAnchor="end">
              ρ = 1 · no leakage reduction
            </text>
          </g>
        )}
        {minX < 0 && (
          <line className="reference" x1={x(0)} x2={x(0)} y1={top} y2={bottom} />
        )}
        <line x1={left} y1={top} x2={left} y2={bottom} className="axis" />
        <line x1={left} y1={bottom} x2={right} y2={bottom} className="axis" />
        {rows.map((row, index) => {
          const cx = x(row[field] as number);
          const cy = y(row.rho as number);
          const { color, shape } = style(row);
          return (
            <g key={`${row.condition}-${row.concurrency}-${row.model}-${index}`} className="point">
              <title>
                {`${row.condition} · C${row.concurrency} · ${row.model}/${row.representation}\nρ = ${fmt(row.rho)} [${fmt(row.rho_lo)}, ${fmt(row.rho_hi)}]\n${cost.label}: ${fmt(row[field], 1)}`}
              </title>
              {row.rho_lo !== null && row.rho_hi !== null && (
                <g className="whisker" stroke={color}>
                  <line x1={cx} x2={cx} y1={y(row.rho_lo)} y2={y(row.rho_hi)} />
                  <line x1={cx - 5} x2={cx + 5} y1={y(row.rho_lo)} y2={y(row.rho_lo)} />
                  <line x1={cx - 5} x2={cx + 5} y1={y(row.rho_hi)} y2={y(row.rho_hi)} />
                </g>
              )}
              <Marker shape={shape} x={cx} y={cy} color={color} />
              <text x={cx + 11} y={cy - 9} className="pointLabel">
                {row.condition}
              </text>
            </g>
          );
        })}
        <text x={(left + right) / 2} y={height - 18} textAnchor="middle" className="axisLabel">
          {cost.label}
        </text>
        <text
          transform={`translate(18 ${(top + bottom) / 2}) rotate(-90)`}
          textAnchor="middle"
          className="axisLabel"
        >
          Retained leakage ρ (95% CI)
        </text>
      </svg>
      <figcaption className="legend">
        {levels.map((level, i) => (
          <span key={level}>
            <svg width="16" height="16" aria-hidden="true">
              <Marker shape={MARKERS[Math.min(i, 3)]!} x={8} y={8} color={SERIES[Math.min(i, 3)]!} />
            </svg>
            Concurrency C{level}
          </span>
        ))}
        {skipped > 0 && <span>{skipped} undefined rows not plotted</span>}
      </figcaption>
    </figure>
  );
}

/** Every frontier row, including undefined values, as an accessible table view. */
export function FrontierTable({ data }: { data: FrontierResults }) {
  return (
    <div className="tableWrap frontierTable" tabIndex={0} role="region" aria-label="Frontier rows">
      <table>
        <caption className="srOnly">Frontier measurements; dashes mark undefined values</caption>
        <thead>
          <tr>
            <th scope="col">Condition</th>
            <th scope="col">Model</th>
            <th scope="col">Balanced acc.</th>
            <th scope="col">
              <span className="noCase">ρ</span> · 95% CI
            </th>
            <th scope="col">Bandwidth (%)</th>
            <th scope="col">TTFT p95 (ms)</th>
            <th scope="col">ITL p99 (ms)</th>
          </tr>
        </thead>
        <tbody>
          {data.rows.map((row, i) => (
            <tr key={i}>
              <td>
                {row.condition}
                <small>C{row.concurrency}</small>
              </td>
              <td>
                {row.model}
                <small>{row.representation}</small>
              </td>
              <td className="numeric">{fmt(row.balanced_accuracy, 3)}</td>
              <td className="numeric">
                {row.rho === null ? "Undefined" : fmt(row.rho, 3)}
                <small>
                  {row.rho_lo === null || row.rho_hi === null
                    ? "CI unavailable"
                    : `[${fmt(row.rho_lo, 3)}, ${fmt(row.rho_hi, 3)}]`}
                </small>
              </td>
              <td className="numeric">{fmt(row.bandwidth_overhead_pct, 1)}</td>
              <td className="numeric">{fmt(row.ttft_p95_ms, 1)}</td>
              <td className="numeric">{fmt(row.itl_p99_ms, 1)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
