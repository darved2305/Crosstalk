import type { ExperimentDetail, Job, PipelineEvent } from "./schemas";

export const SESSION_STAGES = ["plan", "collect", "reconstruct", "validate"] as const;
export const EXPERIMENT_STAGES = ["analyze", "report"] as const;

/** Poll interval while the worker still owns jobs for an experiment. */
export const ACTIVE_POLL_MS = 2_000;

const ACTIVE_JOB_STATES = new Set(["queued", "running"]);
const RETRYABLE_JOB_STATES = new Set(["failed", "dead"]);

export type StageView = {
  stage: string;
  /** The most recent job for this stage, or null while the worker has not enqueued it. */
  job: Job | null;
  /** Job state, or "pending" when the stage has no job yet. */
  state: string;
  /** Whether the stage belongs to the standard pipeline (false for legacy/imported kinds). */
  expected: boolean;
};

/** ULIDs sort lexicographically by creation time. */
const newest = (a: Job, b: Job) => (a.id < b.id ? b : a);

/**
 * Maps the jobs the API reports onto the expected ordered stage list. Stages the
 * worker has not enqueued yet are "pending"; unexpected job kinds (for example
 * imported evidence) are appended so nothing the API reports is hidden.
 */
export function stageViews(jobs: readonly Job[], stages: readonly string[]): StageView[] {
  const byKind = new Map<string, Job>();
  for (const job of jobs) {
    const current = byKind.get(job.kind);
    byKind.set(job.kind, current ? newest(current, job) : job);
  }
  const views: StageView[] = stages.map((stage) => {
    const job = byKind.get(stage) ?? null;
    return { stage, job, state: job?.state ?? "pending", expected: true };
  });
  for (const [kind, job] of byKind)
    if (!stages.includes(kind))
      views.push({ stage: kind, job, state: job.state, expected: false });
  return views;
}

export function allJobs(detail: ExperimentDetail): Job[] {
  return [...detail.sessions.flatMap((s) => s.jobs), ...detail.jobs];
}

export function hasActiveJobs(detail: ExperimentDetail): boolean {
  return allJobs(detail).some((job) => ACTIVE_JOB_STATES.has(job.state));
}

/**
 * The API reports a freshly created experiment as "running" before any run has
 * been started. Treat an experiment with no sessions and no jobs as not started.
 */
export function notStarted(detail: ExperimentDetail): boolean {
  return detail.sessions.length === 0 && detail.jobs.length === 0;
}

/** Display state: "not started" instead of the API's "running" for unrun experiments. */
export function displayState(detail: ExperimentDetail): string {
  return detail.state === "running" && notStarted(detail) ? "not started" : detail.state;
}

/** True while the experiment can still change without operator action. */
export function isLive(detail: ExperimentDetail | undefined): boolean {
  if (!detail) return false;
  if (hasActiveJobs(detail)) return true;
  return detail.state === "running" && !notStarted(detail);
}

/** TanStack Query refetchInterval: poll while running, stop once terminal. */
export function pollInterval(detail: ExperimentDetail | undefined): number | false {
  return isLive(detail) ? ACTIVE_POLL_MS : false;
}

export const isRetryable = (job: Job) => RETRYABLE_JOB_STATES.has(job.state);

export function formatJobError(error: Job["error"]): string | null {
  if (!error) return null;
  const { type, message, ...rest } = error as {
    type?: unknown;
    message?: unknown;
    [key: string]: unknown;
  };
  const head = [type, message].filter((v) => typeof v === "string" && v).join(": ");
  const extra = Object.keys(rest).length ? JSON.stringify(rest) : "";
  return [head, extra].filter(Boolean).join(" ") || JSON.stringify(error);
}

export type SessionValidation = {
  sessionId: string;
  status: string;
  reasons: string[];
};

/** Latest session_validated event per session (events arrive newest first). */
export function sessionValidations(events: readonly PipelineEvent[]): SessionValidation[] {
  const seen = new Map<string, SessionValidation>();
  for (const event of events) {
    if (event.kind !== "session_validated" || !event.session_id) continue;
    if (seen.has(event.session_id)) continue;
    const status = typeof event.data.status === "string" ? event.data.status : "UNKNOWN";
    const reasons = Array.isArray(event.data.reasons)
      ? event.data.reasons.filter((r): r is string => typeof r === "string")
      : [];
    seen.set(event.session_id, { sessionId: event.session_id, status, reasons });
  }
  return [...seen.values()];
}

export type Reportability = {
  /** null while no session has been validated yet. */
  reportable: boolean | null;
  reasons: string[];
  invalidSessions: string[];
};

/**
 * Derives reportability from validation events and session states. Any session
 * that is not VALID makes the experiment NOT REPORTABLE.
 */
export function reportability(detail: ExperimentDetail): Reportability {
  const validations = sessionValidations(detail.events);
  const reasons = [...new Set(validations.filter((v) => v.status !== "VALID").flatMap((v) => v.reasons))];
  const invalid = new Set(validations.filter((v) => v.status !== "VALID").map((v) => v.sessionId));
  for (const session of detail.sessions)
    if (["PARTIAL", "INVALID", "FAILED"].includes(session.state)) invalid.add(session.id);
  if (invalid.size || detail.state === "partial")
    return {
      reportable: false,
      reasons: reasons.length
        ? reasons
        : ["One or more sessions did not pass validation (see session states)."],
      invalidSessions: [...invalid],
    };
  const done = detail.sessions.length > 0 && detail.sessions.every((s) => s.state === "VALID");
  return { reportable: done ? true : null, reasons: [], invalidSessions: [] };
}

export function reportPublished(detail: ExperimentDetail): boolean {
  return (
    detail.events.some((e) => e.kind === "report_published") ||
    detail.jobs.some((j) => j.kind === "report" && j.state === "succeeded")
  );
}

const EVENT_LABELS: Record<string, string> = {
  session_planned: "Session planned",
  session_collected: "Traffic collected",
  session_reconstructed: "Traces reconstructed",
  session_validated: "Session validated",
  session_cancelled: "Session cancelled",
  analysis_started: "Analysis started",
  analysis_finished: "Analysis finished",
  report_published: "Report published",
};

export function eventLabel(kind: string): string {
  return EVENT_LABELS[kind] ?? kind.replaceAll("_", " ");
}

const num = (value: unknown) => (typeof value === "number" ? value.toLocaleString() : null);

/** A short, factual summary of the event payload; never invents values. */
export function eventSummary(event: PipelineEvent): string {
  const d = event.data;
  switch (event.kind) {
    case "session_planned":
      return [d.workload && `workload ${String(d.workload)}`, d.model && `model ${String(d.model)}`]
        .filter(Boolean)
        .join(" · ");
    case "session_collected":
      return [
        num(d.completed) !== null && `${num(d.completed)}/${num(d.planned) ?? "?"} requests completed`,
        num(d.failed) !== null && `${num(d.failed)} failed`,
        d.capture && `capture ${String(d.capture)}`,
        d.lab && `lab ${String(d.lab)}`,
      ]
        .filter(Boolean)
        .join(" · ");
    case "session_reconstructed":
      return [
        num(d.flows) !== null && `${num(d.flows)} flows`,
        num(d.records) !== null && `${num(d.records)} TLS records`,
        num(d.packets) !== null && `${num(d.packets)} packets`,
        num(d.incomplete_flows) !== null && `${num(d.incomplete_flows)} incomplete`,
      ]
        .filter(Boolean)
        .join(" · ");
    case "session_validated":
      return typeof d.status === "string" ? `status ${d.status}` : "";
    case "analysis_started":
      return typeof d.kind === "string" ? `${d.kind.toUpperCase()} analysis` : "";
    default: {
      const scalars = Object.entries(d).filter(
        ([, v]) => ["string", "number", "boolean"].includes(typeof v),
      );
      return scalars.map(([k, v]) => `${k.replaceAll("_", " ")} ${String(v)}`).join(" · ");
    }
  }
}

export function eventNotes(event: PipelineEvent): string[] {
  const notes = event.data.notes;
  return Array.isArray(notes) ? notes.filter((n): n is string => typeof n === "string") : [];
}
