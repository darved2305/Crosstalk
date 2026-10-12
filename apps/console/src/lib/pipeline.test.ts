import { describe, expect, it } from "vitest";
import {
  ACTIVE_POLL_MS,
  EXPERIMENT_STAGES,
  SESSION_STAGES,
  displayState,
  eventSummary,
  formatJobError,
  isLive,
  pollInterval,
  reportability,
  reportPublished,
  stageViews,
} from "./pipeline";
import { S1, S2, detail, event, job } from "./test-fixtures";

const d0 = detail();

describe("stageViews", () => {
  it("marks stages without jobs as pending, in pipeline order", () => {
    const views = stageViews([job("plan", "succeeded"), job("collect", "running")], SESSION_STAGES);
    expect(views.map((v) => [v.stage, v.state])).toEqual([
      ["plan", "succeeded"],
      ["collect", "running"],
      ["reconstruct", "pending"],
      ["validate", "pending"],
    ]);
  });

  it("uses the newest job of a kind and keeps unexpected kinds visible", () => {
    const old = job("analyze", "dead");
    const retried = job("analyze", "succeeded");
    const views = stageViews([retried, old, job("import_development_evidence", "succeeded")], EXPERIMENT_STAGES);
    expect(views[0]?.job?.id).toBe(retried.id);
    expect(views.at(-1)).toMatchObject({ stage: "import_development_evidence", expected: false });
  });
});

describe("polling stop logic", () => {
  it("polls every 2 s while the experiment is running", () => {
    const d = detail({
      state: "running",
      sessions: [{ id: S1, experiment_id: d0.id, state: "PLANNED", cancel_requested: false, jobs: [job("plan", "queued")] }],
    });
    expect(pollInterval(d)).toBe(ACTIVE_POLL_MS);
    expect(ACTIVE_POLL_MS).toBe(2000);
  });

  it("does not poll a created experiment that has never run (API reports it as running)", () => {
    const d = detail({ state: "running" });
    expect(pollInterval(d)).toBe(false);
    expect(displayState(d)).toBe("not started");
  });

  it("keeps polling a terminal-looking experiment that still has queued jobs", () => {
    const d = detail({ state: "partial", jobs: [job("report", "queued")] });
    expect(isLive(d)).toBe(true);
    expect(pollInterval(d)).toBe(ACTIVE_POLL_MS);
  });

  it.each(["completed", "partial", "failed", "cancelled"] as const)(
    "stops polling once %s with no active jobs",
    (state) => {
      const d = detail({
        state,
        sessions: [
          { id: S1, experiment_id: d0.id, state: "PARTIAL", cancel_requested: false, jobs: [job("validate", "succeeded")] },
        ],
        jobs: [job("analyze", "succeeded"), job("report", "succeeded")],
      });
      expect(pollInterval(d)).toBe(false);
    },
  );

  it("does not poll before data arrives", () => {
    expect(pollInterval(undefined)).toBe(false);
  });
});

describe("reportability", () => {
  it("collects unique reasons from the latest validation of each session", () => {
    const d = detail({
      state: "partial",
      sessions: [
        { id: S1, experiment_id: d0.id, state: "PARTIAL", cancel_requested: false, jobs: [] },
        { id: S2, experiment_id: d0.id, state: "PARTIAL", cancel_requested: false, jobs: [] },
      ],
      events: [
        event("session_validated", { status: "PARTIAL", reasons: ["Synthetic mock upstream, not a served model"] }, S2),
        event("session_validated", { status: "PARTIAL", reasons: ["Synthetic mock upstream, not a served model", "No dumpcap capture metadata"] }, S1),
        event("session_validated", { status: "VALID", reasons: [] }, S1),
      ],
    });
    const verdict = reportability(d);
    expect(verdict.reportable).toBe(false);
    expect(verdict.reasons).toEqual([
      "Synthetic mock upstream, not a served model",
      "No dumpcap capture metadata",
    ]);
    expect(verdict.invalidSessions).toHaveLength(2);
  });

  it("is undecided while sessions are still collecting", () => {
    const d = detail({
      sessions: [{ id: S1, experiment_id: d0.id, state: "COLLECTING", cancel_requested: false, jobs: [] }],
    });
    expect(reportability(d).reportable).toBeNull();
  });

  it("is reportable only when every session is VALID", () => {
    const d = detail({
      state: "completed",
      sessions: [{ id: S1, experiment_id: d0.id, state: "VALID", cancel_requested: false, jobs: [] }],
      events: [event("session_validated", { status: "VALID", reasons: [] }, S1)],
    });
    expect(reportability(d).reportable).toBe(true);
  });
});

describe("event and error formatting", () => {
  it("summarizes reconstruction counts without inventing fields", () => {
    expect(eventSummary(event("session_reconstructed", { flows: 36, records: 1536, packets: 1356 }))).toBe(
      "36 flows · 1,536 TLS records · 1,356 packets",
    );
    expect(eventSummary(event("session_reconstructed", {}))).toBe("");
  });

  it("formats worker error payloads", () => {
    expect(formatJobError({ type: "RuntimeError", message: "relay closed" })).toBe("RuntimeError: relay closed");
    expect(formatJobError({ type: "unknown_stage", stage: "x" })).toBe('unknown_stage {"stage":"x"}');
    expect(formatJobError(null)).toBeNull();
  });

  it("detects a published report from events or the report job", () => {
    expect(reportPublished(detail({ events: [event("report_published")] }))).toBe(true);
    expect(reportPublished(detail({ jobs: [job("report", "succeeded")] }))).toBe(true);
    expect(reportPublished(detail({ jobs: [job("report", "running")] }))).toBe(false);
  });
});
