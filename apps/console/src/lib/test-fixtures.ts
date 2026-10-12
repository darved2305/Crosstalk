// Test-only builders for API-shaped objects. Never imported by production code.
import type { ExperimentDetail, Job, PipelineEvent } from "./schemas";

export const EXP = "01K00000000000000000000000";
export const S1 = "01K00000000000000000000010";
export const S2 = "01K00000000000000000000011";

let counter = 0;
const nextId = () => `01K000000000000000000${String(10000 + counter++).padStart(5, "0")}`;

export function job(kind: string, state: string, extra: Partial<Job> = {}): Job {
  return {
    id: nextId(),
    kind,
    queue: ["analyze", "report"].includes(kind) ? "evaluate" : "collect",
    state,
    attempts: state === "queued" ? 0 : 1,
    max_attempts: 5,
    error: null,
    updated_at: "2026-10-12T15:16:19.125788+05:30",
    ...extra,
  };
}

export function event(
  kind: string,
  data: Record<string, unknown> = {},
  session_id: string | null = null,
): PipelineEvent {
  return { id: nextId(), kind, session_id, data, created_at: "2026-10-12T15:16:31.546168+05:30" };
}

export function detail(overrides: Partial<ExperimentDetail> = {}): ExperimentDetail {
  return {
    id: EXP,
    name: "fixture-e0",
    config_hash: "a".repeat(64),
    state: "running",
    created_at: "2026-10-12T15:16:18.108375+05:30",
    updated_at: "2026-10-12T15:16:18.108375+05:30",
    config: { kind: "e0", policy_set: [{ arm: "a0", kind: "passthrough" }] },
    sessions: [],
    jobs: [],
    events: [],
    ...overrides,
  };
}
