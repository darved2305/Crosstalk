const terminal = new Set(["completed", "VALID", "succeeded"]);
const bad = new Set(["failed", "dead", "INVALID", "PARTIAL", "partial"]);

export function Status({ value }: { value: string }) {
  const tone = terminal.has(value) ? "good" : bad.has(value) ? "bad" : "neutral";
  return <span className={`status ${tone}`}>{value}</span>;
}
