/** Rounded axis ticks that always include the data range (about `count` steps). */
export function niceTicks(min: number, max: number, count = 5): number[] {
  if (!Number.isFinite(min) || !Number.isFinite(max)) return [0, 1];
  if (max === min) {
    const pad = Math.abs(min) || 1;
    return niceTicks(min - pad, max + pad, count);
  }
  const raw = (max - min) / count;
  const power = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * power).find((s) => s >= raw) ?? 10 * power;
  const start = Math.floor(min / step) * step;
  const end = Math.ceil(max / step) * step;
  const decimals = Math.max(0, -Math.floor(Math.log10(step)) + (step / power === 2.5 ? 1 : 0));
  const ticks: number[] = [];
  for (let v = start; v <= end + step / 2; v += step) ticks.push(Number(v.toFixed(decimals)));
  return ticks;
}
