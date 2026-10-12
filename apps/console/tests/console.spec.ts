import { expect, test } from "@playwright/test";

const ids = [
  "01K00000000000000000000000",
  "01K00000000000000000000001",
  "01K00000000000000000000002",
  "01K00000000000000000000003",
];
const names = [
  "E0 Calibration",
  "E1 Defense Evaluation",
  "E2 Serving Load",
  "Wire Validation",
];
const now = "2026-10-11T12:00:00Z";
const summaries = ids.map((id, i) => ({
  id,
  name: names[i],
  config_hash: "a".repeat(64),
  state: i === 0 ? "completed" : "running",
  created_at: now,
  updated_at: now,
}));
const details = summaries.map((summary, i) => ({
  ...summary,
  config: {
    policy_set: [
      { arm: "a0", kind: "passthrough" },
      { arm: "a1", kind: "bucket_pad" },
    ],
  },
  sessions: [
    {
      id: `01K0000000000000000000001${i}`,
      experiment_id: summary.id,
      state: i === 0 ? "VALID" : "RUNNING",
      cancel_requested: false,
      jobs: [
        {
          id: `01K0000000000000000000002${i}`,
          kind: "collect",
          queue: "collect",
          state: "succeeded",
          attempts: 1,
          max_attempts: 3,
          error: null,
        },
        {
          id: `01K0000000000000000000003${i}`,
          kind: "attack",
          queue: "attack",
          state: i === 0 ? "succeeded" : "queued",
          attempts: 0,
          max_attempts: 3,
          error: null,
        },
      ],
    },
  ],
}));

test.beforeEach(async ({ page }) => {
  await page.route("**/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (route.request().headers().authorization !== `Bearer ${"t".repeat(32)}`)
      return route.fulfill({ status: 401, json: { detail: "Invalid token" } });
    if (path === "/v1/experiments" && route.request().method() === "GET")
      return route.fulfill({ json: { items: summaries, next_cursor: null } });
    if (path === "/v1/experiments" && route.request().method() === "POST")
      return route.fulfill({
        status: 201,
        json: { id: ids[0], name: "wire-check", config_hash: "a".repeat(64) },
      });
    const detail = details.find(
      (item) => path === `/v1/experiments/${item.id}`,
    );
    if (detail) return route.fulfill({ json: detail });
    if (path.endsWith("/runs"))
      return route.fulfill({
        status: 202,
        json: {
          experiment_id: ids[0],
          session_ids: [details[0]!.sessions[0]!.id],
          replayed: false,
        },
      });
    if (path.endsWith("/cancel"))
      return route.fulfill({ json: { id: ids[0], state: "cancelled" } });
    if (path.endsWith("/results"))
      return route.fulfill({ json: { experiment_id: ids[0], rows: [] } });
    if (path.endsWith("/frontier"))
      return route.fulfill({
        json: {
          experiment_id: ids[0],
          setting: "adaptive",
          cost: "bandwidth",
          rows: [],
        },
      });
    return route.fulfill({ status: 404, json: { detail: "Artifact pending" } });
  });
  await page.goto("/", { waitUntil: "domcontentloaded" });
  await page.getByLabel("Operator token").fill("t".repeat(32));
  await page.getByRole("button", { name: "Connect" }).click();
  await expect(page.getByText("E0 Calibration")).toBeVisible();
});

test("dashboard matches card layout and controls at reference viewport", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1504, height: 1128 });
  await expect(page.locator(".experimentCard")).toHaveCount(4);
  await page.screenshot({
    path: "test-results/dashboard-1504x1128.png",
    fullPage: true,
  });
  await page.getByRole("button", { name: "Filters" }).click();
  await page.getByPlaceholder("Name or experiment ID").fill("Serving");
  await expect(page.locator(".experimentCard")).toHaveCount(1);
  await page.getByPlaceholder("Name or experiment ID").fill("");
  await page.getByRole("button", { name: "List view" }).click();
  await expect(page.locator("tbody tr")).toHaveCount(4);
  await page
    .locator("tbody tr")
    .first()
    .getByRole("link", { name: "Open" })
    .click();
  await expect(page.getByRole("heading", { name: "Session pipelines" })).toBeVisible();
  await page.getByRole("button", { name: "Start run" }).click();
  await expect(page.getByText("1 session(s) enqueued.")).toBeVisible();
});

test("mobile navigation, empty evidence, and disconnected state", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({
    path: "test-results/dashboard-390x844.png",
    fullPage: true,
  });
  await page.getByRole("link", { name: "Attack results" }).click();
  await expect(
    page.getByText("Choose an experiment to inspect measured results."),
  ).toBeVisible();
  await page.getByRole("button", { name: "Disconnect" }).click();
  await expect(page.getByLabel("Operator token")).toBeVisible();
});

test("experiment form sends a valid defense configuration", async ({
  page,
}) => {
  await page.getByRole("link", { name: "New Experiment" }).click();
  await page.getByLabel("Experiment name").fill("wire-check");
  await page.getByRole("spinbutton", { name: "Requests" }).fill("8");
  await page.getByLabel("D1 · Bucket padding").check();
  const request = page.waitForRequest(
    (req) => req.url().endsWith("/v1/experiments") && req.method() === "POST",
  );
  await page.getByRole("button", { name: "Create experiment" }).click();
  const body = (await request).postDataJSON() as {
    name: string;
    sessions: { requests: number; arms: string[] }[];
    policy_set: { arm: string }[];
  };
  expect(body.name).toBe("wire-check");
  expect(body.sessions[0]?.requests).toBe(8);
  expect(body.sessions[0]?.arms).toEqual(expect.arrayContaining(["a0", "a1"]));
  expect(body.policy_set.map((p) => p.arm)).toEqual(
    expect.arrayContaining(["a0", "a1"]),
  );
  await expect(
    page.getByRole("heading", { name: "E0 Calibration" }),
  ).toBeVisible();
});

const pipelineId = "01K00000000000000000000009";
const pipelineSession = "01K00000000000000000000019";
const at = "2026-10-12T15:16:31+05:30";
const pipelineJob = (id: string, kind: string, state: string, error: unknown = null) => ({
  id, kind, queue: kind === "analyze" || kind === "report" ? "evaluate" : "collect",
  state, attempts: state === "queued" ? 0 : 1, max_attempts: 5, error, updated_at: at,
});

test("experiment page shows stage pipeline, activity, reportability, retry and cancel confirmation", async ({ page }) => {
  let cancelled = 0;
  let retried = "";
  await page.route(`**/v1/experiments/${pipelineId}`, (route) =>
    route.fulfill({
      json: {
        id: pipelineId, name: "fixture-pipeline", config_hash: "b".repeat(64), state: "running",
        created_at: now, updated_at: now, config: { kind: "e0" },
        sessions: [{
          id: pipelineSession, experiment_id: pipelineId, state: "COLLECTING", cancel_requested: false,
          jobs: [
            pipelineJob("01K00000000000000000000029", "plan", "succeeded"),
            pipelineJob("01K00000000000000000000039", "collect", "dead", { type: "RuntimeError", message: "upstream refused" }),
          ],
        }],
        jobs: [],
        events: [
          { id: "01K00000000000000000000049", kind: "session_validated", session_id: pipelineSession,
            data: { status: "PARTIAL", reasons: ["Synthetic mock upstream, not a served model"] }, created_at: at },
          { id: "01K00000000000000000000059", kind: "session_planned", session_id: pipelineSession,
            data: { workload: "workloads/v1", model: "mock" }, created_at: at },
        ],
      },
    }),
  );
  await page.route(`**/v1/experiments/${pipelineId}/cancel`, (route) => {
    cancelled += 1;
    return route.fulfill({ json: { id: pipelineId, state: "cancelled" } });
  });
  await page.route("**/v1/jobs/*/retry", (route) => {
    retried = new URL(route.request().url()).pathname;
    return route.fulfill({ status: 409, contentType: "application/problem+json",
      json: { type: "https://crosstalk.dev/problems/409", title: "Conflict", status: 409,
        detail: "Only failed or dead jobs can be retried" } });
  });
  await page.goto(`/experiments/${pipelineId}`);
  const stages = page.getByRole("list", { name: "Session 1 stages" }).getByRole("listitem");
  await expect(stages).toHaveCount(4);
  await expect(stages.nth(1)).toHaveAttribute("data-state", "dead");
  await expect(stages.nth(2)).toContainText("Not enqueued yet");
  await expect(page.getByText("RuntimeError: upstream refused")).toBeVisible();
  await expect(page.getByRole("heading", { name: "NOT REPORTABLE" })).toBeVisible();
  await expect(page.getByRole("list", { name: /Pipeline activity/ })).toContainText("Session validated");
  await expect(page.getByText(/refreshing every 2 s/)).toBeVisible();

  await page.getByRole("button", { name: "Retry collect" }).click();
  await expect(page.getByRole("alert")).toContainText("Conflict (409): Only failed or dead jobs can be retried");
  expect(retried).toBe("/v1/jobs/01K00000000000000000000039/retry");

  await page.getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(page.getByRole("alertdialog")).toBeVisible();
  await page.getByRole("button", { name: "Keep running" }).click();
  expect(cancelled).toBe(0);
  await page.getByRole("button", { name: "Cancel", exact: true }).click();
  await page.getByRole("button", { name: "Confirm cancel" }).click();
  await expect(page.getByText("Cancellation requested; experiment is cancelled.")).toBeVisible();
  expect(cancelled).toBe(1);
});

test("report page renders markdown tables without raw HTML", async ({ page }) => {
  await page.route(`**/v1/experiments/${ids[0]}/report`, (route) =>
    route.fulfill({
      contentType: "text/markdown",
      body: [
        "# E0 report",
        "",
        "> **DEVELOPMENT RESULT, NOT REPORTABLE.**",
        "",
        "| Regime | Traces |",
        "|---|---|",
        "| NAT | 72 |",
        "",
        "<script>window.injected = true</script>",
      ].join(String.fromCharCode(10)),
    }),
  );
  await page.goto(`/reports?experiment=${ids[0]}`);
  await expect(page.getByRole("heading", { name: "E0 report" })).toBeVisible();
  await expect(page.locator(".markdownReport table td").first()).toHaveText("NAT");
  await expect(page.getByRole("status")).toContainText("NOT REPORTABLE");
  expect(await page.evaluate(() => (window as unknown as { injected?: boolean }).injected)).toBeUndefined();
  await expect(page.getByRole("button", { name: "Download Markdown" })).toBeEnabled();
});

test("dashboard filters are deep-linkable and pages never scroll horizontally", async ({ page }) => {
  await page.goto("/?q=Serving&view=list");
  await expect(page.locator("tbody tr")).toHaveCount(1);
  await expect(page.getByPlaceholder("Name or experiment ID")).toHaveValue("Serving");
  for (const size of [
    { width: 1504, height: 1128 },
    { width: 1280, height: 800 },
    { width: 768, height: 1024 },
    { width: 390, height: 844 },
  ]) {
    await page.setViewportSize(size);
    for (const path of ["/", `/experiments/${ids[1]}`, "/traces", "/new"]) {
      await page.goto(path);
      await page.waitForLoadState("networkidle");
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
      expect(overflow, `${path} at ${size.width}px`).toBeLessThanOrEqual(0);
    }
  }
});

test("keyboard users can skip to content and see focus", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByText("E0 Calibration")).toBeVisible();
  await page.keyboard.press("Tab");
  const skip = page.getByRole("link", { name: "Skip to content" });
  await expect(skip).toBeFocused();
  const outline = await skip.evaluate((el) => getComputedStyle(el).outlineStyle);
  expect(outline).not.toBe("none");
});

test("E0 calibration form sends block-randomized sessions with the quick analysis preset", async ({ page }) => {
  await page.goto("/new");
  await page.getByLabel("Experiment name").fill("e0-quick");
  await page.getByRole("radio", { name: /E0 calibration/ }).check();
  await expect(page.getByText("D1 · Bucket padding")).toHaveCount(0);
  await page.getByRole("spinbutton", { name: "Requests per class" }).fill("6");
  await page.getByRole("spinbutton", { name: "Concurrency" }).fill("4");
  await page.getByRole("spinbutton", { name: "First seed" }).fill("41");
  const request = page.waitForRequest((req) => req.url().endsWith("/v1/experiments") && req.method() === "POST");
  await page.getByRole("button", { name: "Create experiment" }).click();
  const body = (await request).postDataJSON() as {
    kind: string; analysis: { models: string[] };
    sessions: { seed: number; per_class: number; workload: string; concurrency: number }[];
  };
  expect(body.kind).toBe("e0");
  expect(body.sessions.map((s) => s.seed)).toEqual([41, 42]);
  expect(body.sessions[0]).toMatchObject({ per_class: 6, workload: "workloads/v1", concurrency: 4 });
  expect(body.analysis.models).toEqual(["chance", "lr"]);
});
