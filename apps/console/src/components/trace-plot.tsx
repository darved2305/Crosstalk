import type { TraceResults } from "../lib/schemas";
import { niceTicks } from "./ticks";

/** Server→client (response) bytes are positive, client→server (request) bytes negative. */
const DOWN = "#2a78d6";
const UP = "#eb6834";

export function TracePlot({ trace }: { trace: TraceResults }) {
  const unit = trace.layer === "packets" ? "TCP payload bytes" : "TLS record bytes";
  if (!trace.points.length)
    return (
      <div className="emptyState" role="note">
        No observer {trace.layer} for this request.
      </div>
    );
  const width = 900;
  const height = 320;
  const left = 72;
  const right = width - 20;
  const top = 18;
  const bottom = height - 52;
  const times = trace.points.flatMap((p) => [p.t_start_ns, p.t_end_ns]);
  const minTime = times.reduce((a, b) => (a < b ? a : b));
  const maxTime = times.reduce((a, b) => (a > b ? a : b));
  // BigInt subtraction keeps nanosecond precision before converting the small offset.
  const spanMs = Number(maxTime - minTime) / 1e6;
  const xTicks = niceTicks(0, Math.max(spanMs, 1e-3), 6);
  const xMax = xTicks.at(-1)!;
  const amplitude = Math.max(1, ...trace.points.flatMap((p) => [Math.abs(p.min_value), Math.abs(p.max_value)]));
  const yHalf = niceTicks(0, amplitude, 3);
  const yMax = yHalf.at(-1)!;
  const yTicks = [...yHalf.slice(1).map((t) => -t).reverse(), ...yHalf];
  const mid = (top + bottom) / 2;
  const x = (ns: bigint) => left + (Number(ns - minTime) / 1e6 / xMax) * (right - left);
  const y = (value: number) => mid - (value / yMax) * (mid - top);
  const up = trace.points.filter((p) => p.min_value < 0).length;
  const down = trace.points.filter((p) => p.max_value > 0).length;
  return (
    <figure className="chartFigure traceFigure">
      <svg
        className="tracePlot"
        viewBox={`0 0 ${width} ${height}`}
        role="img"
        aria-label={`${trace.layer} signed-byte timeline`}
        aria-describedby="trace-desc"
      >
        <desc id="trace-desc">
          {trace.points.length} {trace.downsampled ? "bins" : "points"} over {spanMs.toFixed(3)} ms.
          Positive values are server-to-client {unit}; negative values are client-to-server.
          {trace.downsampled ? " Each bar shows the largest value per direction within a bin." : ""}
        </desc>
        {yTicks.map((t) => (
          <g key={`y${t}`} className="tick">
            <line x1={left} x2={right} y1={y(t)} y2={y(t)} className={t === 0 ? "axis" : "grid"} />
            <text x={left - 8} y={y(t) + 4} textAnchor="end">
              {t > 0 ? `+${t.toLocaleString()}` : t.toLocaleString()}
            </text>
          </g>
        ))}
        {xTicks.map((t) => {
          const px = left + (t / xMax) * (right - left);
          return (
            <g key={`x${t}`} className="tick">
              <line x1={px} x2={px} y1={bottom} y2={bottom + 5} className="axis" />
              <text x={px} y={bottom + 19} textAnchor="middle">
                {t}
              </text>
            </g>
          );
        })}
        <line x1={left} x2={left} y1={top} y2={bottom} className="axis" />
        <line x1={left} x2={right} y1={bottom} y2={bottom} className="axis" />
        {trace.points.map((point, index) => {
          const px = x(point.t_start_ns);
          const pxEnd = x(point.t_end_ns);
          const cx = (px + pxEnd) / 2;
          return (
            <g key={`${point.t_start_ns}-${index}`}>
              {point.max_value > 0 && (
                <line x1={cx} x2={cx} y1={y(0)} y2={y(point.max_value)} stroke={DOWN} className="stem" />
              )}
              {point.min_value < 0 && (
                <line x1={cx} x2={cx} y1={y(0)} y2={y(point.min_value)} stroke={UP} className="stem" />
              )}
              {point.count === 1 && (
                <circle cx={cx} cy={y(point.max_value)} r="2.5" fill={point.max_value >= 0 ? DOWN : UP} />
              )}
            </g>
          );
        })}
        <text x={(left + right) / 2} y={height - 10} textAnchor="middle" className="axisLabel">
          Time since first {trace.layer === "packets" ? "packet" : "record"} (ms)
        </text>
        <text transform={`translate(16 ${mid}) rotate(-90)`} textAnchor="middle" className="axisLabel">
          Signed {unit}
        </text>
      </svg>
      <figcaption className="legend">
        <span>
          <i style={{ background: DOWN }} aria-hidden="true" /> Server → client (+), {down}{" "}
          {trace.downsampled ? "bins" : "points"}
        </span>
        <span>
          <i style={{ background: UP }} aria-hidden="true" /> Client → server (−), {up}{" "}
          {trace.downsampled ? "bins" : "points"}
        </span>
        <span>Span {spanMs.toFixed(3)} ms</span>
      </figcaption>
    </figure>
  );
}
