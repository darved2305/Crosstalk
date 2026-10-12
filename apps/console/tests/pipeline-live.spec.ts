import { expect, test, type Page } from "@playwright/test";
import { mkdir, readFile } from "node:fs/promises";
import { resolve } from "node:path";

/*
 * Opt-in real-stack pipeline check. Requires a live console (CT_E2E_BASE_URL,
 * e.g. http://127.0.0.1:3010) proxying to an API with a running production
 * worker, and CT_E2E_OPERATOR_TOKEN. No request is intercepted.
 *
 * Optional CT_E2E_SCREENSHOT_DIR captures evidence screenshots (the token is
 * injected into sessionStorage and never typed or rendered).
 */
const token = process.env.CT_E2E_OPERATOR_TOKEN ?? "";
const baseURL = process.env.CT_E2E_BASE_URL ?? "";
const shots = process.env.CT_E2E_SCREENSHOT_DIR;

async function shot(page: Page, name: string, fullPage = false) {
  if (!shots) return;
  await mkdir(shots, { recursive: true });
  await page.screenshot({ path: resolve(shots, `${name}.png`), fullPage });
}

test.describe("live pipeline", () => {
  test.skip(!token || !baseURL, "Set CT_E2E_OPERATOR_TOKEN and CT_E2E_BASE_URL for the live pipeline check.");
  test.beforeEach(async ({ page }) => {
    await page.addInitScript((value) => sessionStorage.setItem("crosstalk.operatorToken", value), token);
  });

  test("create, run and finish an E0 experiment through the UI", async ({ page }) => {
    test.setTimeout(240_000);
    await page.setViewportSize({ width: 1504, height: 1128 });
    const name = `console-e2e-${Date.now()}`;

    await page.goto("/new");
    await page.getByLabel("Experiment name").fill(name);
    await page.getByRole("radio", { name: /E0 calibration/ }).check();
    await page.getByRole("spinbutton", { name: "Requests per class" }).fill("6");
    await page.getByRole("spinbutton", { name: "Sessions" }).fill("2");
    await page.getByRole("spinbutton", { name: "Concurrency" }).fill("4");
    await page.getByRole("spinbutton", { name: "First seed" }).fill("41");
    await page.getByRole("button", { name: "Create experiment" }).click();

    await expect(page).toHaveURL(/\/experiments\/[0-9A-HJKMNP-TV-Z]{26}$/);
    const id = page.url().split("/").at(-1)!;
    await expect(page.getByRole("heading", { level: 1, name })).toBeVisible();
    await expect(page.getByText("No sessions yet")).toBeVisible();

    const start = page.getByRole("button", { name: "Start run" });
    const runRequest = page.waitForRequest((r) => r.url().endsWith(`/v1/experiments/${id}/runs`));
    await start.click();
    expect((await runRequest).headers()["idempotency-key"]).toMatch(/^[0-9a-f-]{36}$/);
    await expect(page.getByText("Run started: 2 session(s) enqueued.")).toBeVisible();
    await expect(page.getByText(/refreshing every 2 s/)).toBeVisible();
    await expect(start).toBeDisabled();

    // Mid-pipeline: the worker is executing a session stage.
    await expect(page.locator('.pipelineStep[data-state="running"]').first()).toBeVisible({ timeout: 60_000 });
    await shot(page, "02-experiment-running-desktop");

    // The report appears without any manual reload.
    await expect(page.getByTestId("experiment-report")).toBeVisible({ timeout: 180_000 });
    await expect(page.getByText(/Pipeline idle/)).toBeVisible();
    await expect(page.getByRole("heading", { name: "NOT REPORTABLE" })).toBeVisible();
    await expect(page.locator(".reportBanner")).toContainText("Synthetic mock upstream");
    await expect(page.getByTestId("experiment-report")).toContainText("NOT REPORTABLE");

    for (const session of [1, 2]) {
      const steps = page.getByRole("list", { name: `Session ${session} stages` }).getByRole("listitem");
      await expect(steps).toHaveCount(4);
      for (let i = 0; i < 4; i += 1) await expect(steps.nth(i)).toHaveAttribute("data-state", "succeeded");
    }
    const experimentSteps = page.getByRole("list", { name: "Experiment stages" }).getByRole("listitem");
    await expect(experimentSteps.nth(0)).toHaveAttribute("data-state", "succeeded");
    await expect(experimentSteps.nth(1)).toHaveAttribute("data-state", "succeeded");
    const activity = page.getByRole("list", { name: /Pipeline activity/ });
    await expect(activity).toContainText("Report published");
    await expect(activity).toContainText("Session validated");
    await expect(page.getByRole("button", { name: "Cancel", exact: true })).toBeDisabled();

    // Polling stops once terminal: no further detail requests for several seconds.
    let polls = 0;
    page.on("request", (r) => {
      if (r.url().endsWith(`/v1/experiments/${id}`)) polls += 1;
    });
    await page.waitForTimeout(5_000);
    expect(polls).toBe(0);
    await shot(page, "03-experiment-completed-desktop");
    await shot(page, "03b-experiment-completed-desktop-full", true);

    await page.goto(`/reports?experiment=${id}`);
    await expect(page.getByRole("heading", { level: 1, name: /E0 report/ })).toBeVisible();
    await expect(page.locator(".markdownReport table").first()).toBeVisible();
    await expect(page.getByRole("status")).toContainText("NOT REPORTABLE");
    await shot(page, "04-report-desktop");

    await page.goto(`/results?experiment=${id}`);
    await expect(page.locator("tbody tr").first()).toBeVisible();
    await expect(page.getByRole("status")).toContainText("NOT REPORTABLE");
    await shot(page, "05-results-desktop");

    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto(`/experiments/${id}`);
    await expect(page.getByRole("heading", { name: "NOT REPORTABLE" })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(0);
    await shot(page, "08-experiment-mobile");
    await shot(page, "08b-experiment-mobile-full", true);
  });

  test("evidence views render real retained data", async ({ page }) => {
    test.skip(!shots, "Evidence capture runs only with CT_E2E_SCREENSHOT_DIR.");
    const campaign = JSON.parse(
      await readFile(resolve(process.cwd(), "../../docs/evidence/campaign/campaign.json"), "utf8"),
    ) as { experiments: { id: string; name: string; sessions: { id: string; request_id: string }[] }[] };
    const e2 = campaign.experiments.find((e) => e.name === "e2-e2e")!;
    await page.setViewportSize({ width: 1504, height: 1128 });

    await page.goto("/");
    await expect(page.locator(".experimentCard").first()).toBeVisible();
    await expect(page.getByText("Loading policies")).toHaveCount(0, { timeout: 30_000 });
    await shot(page, "01-experiment-list-desktop");

    await page.goto(`/frontier?experiment=${e2.id}&cost=ttft_p95`);
    await expect(page.locator(".chartFoot")).toContainText("result rows");
    await shot(page, "06-frontier-desktop", true);

    const session = e2.sessions[0]!;
    await page.goto(`/traces?experiment=${e2.id}&session=${session.id}&request=${session.request_id}`);
    await expect(page.getByRole("img", { name: "records signed-byte timeline" })).toBeVisible();
    await shot(page, "07-trace-explorer-desktop", true);

    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/");
    await expect(page.locator(".experimentCard").first()).toBeVisible();
    await expect(page.getByText("Loading policies")).toHaveCount(0, { timeout: 30_000 });
    await shot(page, "09-experiment-list-mobile");
  });
});
