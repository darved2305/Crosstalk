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
  await expect(page.getByText("Sessions & jobs")).toBeVisible();
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
    page.getByText("Enter an experiment ID to inspect measured results."),
  ).toBeVisible();
  await page.getByRole("button", { name: "Disconnect" }).click();
  await expect(page.getByLabel("Operator token")).toBeVisible();
});

test("experiment form sends a valid defense configuration", async ({
  page,
}) => {
  await page.getByRole("link", { name: "New Experiment" }).click();
  await page.getByLabel("Experiment name").fill("wire-check");
  await page.getByLabel("Requests").fill("8");
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
