import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { EXP, S1, detail, event, job } from "../lib/test-fixtures";
import { Experiment } from "./Experiment";

const running = detail({
  state: "running",
  sessions: [
    {
      id: S1,
      experiment_id: EXP,
      state: "COLLECTING",
      cancel_requested: false,
      jobs: [job("plan", "succeeded"), job("collect", "running")],
    },
  ],
});
const finished = detail({
  state: "partial",
  sessions: [
    {
      id: S1,
      experiment_id: EXP,
      state: "PARTIAL",
      cancel_requested: false,
      jobs: ["plan", "collect", "reconstruct", "validate"].map((k) => job(k, "succeeded")),
    },
  ],
  jobs: [job("analyze", "succeeded"), job("report", "succeeded")],
  events: [
    event("report_published"),
    event("session_validated", { status: "PARTIAL", reasons: ["Synthetic mock upstream, not a served model"] }, S1),
  ],
});

let detailCalls = 0;
beforeEach(() => {
  detailCalls = 0;
  sessionStorage.setItem("crosstalk.operatorToken", "t".repeat(32));
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/report"))
        return new Response("# E0 report\n\n> **DEVELOPMENT RESULT, NOT REPORTABLE.**", { status: 200 });
      detailCalls += 1;
      return Response.json(detailCalls === 1 ? running : finished);
    }),
  );
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it("polls while running, shows the report when done, then stops polling", async () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[`/experiments/${EXP}`]}>
        <Routes>
          <Route path="/experiments/:id" element={<Experiment />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  expect(await screen.findByText(/refreshing every 2 s/)).toBeTruthy();
  expect(await screen.findByTestId("experiment-report", {}, { timeout: 5000 })).toBeTruthy();
  expect(screen.getByRole("heading", { name: "NOT REPORTABLE" })).toBeTruthy();
  expect(screen.getByText(/Pipeline idle/)).toBeTruthy();
  const calls = detailCalls;
  expect(calls).toBe(2);
  await new Promise((resolve) => setTimeout(resolve, 2600));
  await waitFor(() => expect(detailCalls).toBe(calls));
}, 15_000);
