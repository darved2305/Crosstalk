import { afterEach, expect, it } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import { S1, event } from "../lib/test-fixtures";
import { ActivityTimeline } from "./activity-timeline";

afterEach(cleanup);

it("lists events newest first with scope, summary and validation reasons", () => {
  render(
    <ActivityTimeline
      sessionLabels={new Map([[S1, "Session 1"]])}
      events={[
        event("report_published"),
        event("session_validated", { status: "PARTIAL", reasons: ["Synthetic mock upstream, not a served model"] }, S1),
        event("session_collected", { planned: 36, completed: 36, failed: 0, capture: "recording-relay" }, S1),
      ]}
    />,
  );
  const items = within(screen.getByRole("list", { name: /Pipeline activity/ })).getAllByRole("listitem", {
    name: undefined,
  });
  const top = items.filter((li) => li.classList.contains("timelineItem"));
  expect(top.map((li) => li.querySelector("strong")?.textContent)).toEqual([
    "Report published",
    "Session validated",
    "Traffic collected",
  ]);
  expect(top[0]?.textContent).toContain("Experiment");
  expect(top[1]?.textContent).toContain("Session 1");
  expect(top[1]?.textContent).toContain("Synthetic mock upstream, not a served model");
  expect(top[2]?.textContent).toContain("36/36 requests completed");
});

it("explains the empty state", () => {
  render(<ActivityTimeline events={[]} />);
  expect(screen.getByText(/No pipeline activity yet/)).toBeTruthy();
});
