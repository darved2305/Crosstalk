import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { SESSION_STAGES } from "../lib/pipeline";
import { job } from "../lib/test-fixtures";
import { StagePipeline } from "./stage-pipeline";

afterEach(cleanup);

it("renders every session stage in order with state, attempts and pending placeholders", () => {
  render(
    <StagePipeline
      label="Session 1 stages"
      jobs={[job("plan", "succeeded"), job("collect", "running", { attempts: 2 })]}
      stages={SESSION_STAGES}
    />,
  );
  const list = screen.getByRole("list", { name: "Session 1 stages" });
  const items = within(list).getAllByRole("listitem");
  expect(items.map((i) => i.dataset.stage)).toEqual(["plan", "collect", "reconstruct", "validate"]);
  expect(items.map((i) => i.dataset.state)).toEqual(["succeeded", "running", "pending", "pending"]);
  expect(items[1]).toHaveProperty("textContent", expect.stringContaining("attempt 2/5"));
  expect(items[2]?.textContent).toContain("Not enqueued yet");
});

it("shows worker errors and retries only failed or dead jobs", () => {
  const onRetry = vi.fn();
  const dead = job("collect", "dead", { error: { type: "RuntimeError", message: "upstream refused" }, attempts: 5 });
  render(
    <StagePipeline
      label="stages"
      jobs={[job("plan", "succeeded"), dead]}
      stages={SESSION_STAGES}
      onRetry={onRetry}
    />,
  );
  expect(screen.getByText("RuntimeError: upstream refused")).toBeTruthy();
  const buttons = screen.getAllByRole("button");
  expect(buttons).toHaveLength(1);
  fireEvent.click(screen.getByRole("button", { name: "Retry collect" }));
  expect(onRetry).toHaveBeenCalledWith(dead);
});

it("disables the retry button while that job's retry is pending", () => {
  const failed = job("analyze", "failed");
  render(
    <StagePipeline label="stages" jobs={[failed]} stages={["analyze", "report"]} onRetry={() => {}} retryingId={failed.id} />,
  );
  const button = screen.getByRole("button", { name: "Retrying…" }) as HTMLButtonElement;
  expect(button.disabled).toBe(true);
});
