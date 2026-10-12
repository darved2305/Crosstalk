import { expect, test } from "@playwright/test";
import { mkdir } from "node:fs/promises";
import { resolve } from "node:path";

const operatorToken = process.env.CT_E2E_OPERATOR_TOKEN ?? "";
const experimentId = process.env.CT_E2E_EXPERIMENT_ID ?? "";

test("real PostgreSQL API and console work together", async ({ page, request }) => {
  test.skip(
    !operatorToken || !experimentId,
    "Set CT_E2E_OPERATOR_TOKEN and CT_E2E_EXPERIMENT_ID for the real-stack check.",
  );

  const readiness = await request.get("http://127.0.0.1:18080/readyz");
  expect(readiness.status()).toBe(200);
  expect(await readiness.json()).toEqual({ status: "ready" });

  const unauthorized = await request.get(
    "http://127.0.0.1:18080/v1/experiments",
  );
  expect(unauthorized.status()).toBe(401);
  expect(unauthorized.headers()["content-type"]).toContain(
    "application/problem+json",
  );

  await page.addInitScript((token) => {
    sessionStorage.setItem("crosstalk.operatorToken", token);
  }, operatorToken);
  await page.setViewportSize({ width: 1504, height: 1128 });
  await page.goto("/", { waitUntil: "networkidle" });
  await expect(page.getByRole("heading", { name: "Experiments" })).toBeVisible();
  const experiment = page.getByRole("link", {
    name: "encrypted-traffic-validation",
  });
  await expect(experiment.first()).toBeVisible();

  const evidence = resolve(process.cwd(), "../../docs/evidence");
  await mkdir(evidence, { recursive: true });
  await page.screenshot({
    path: resolve(evidence, "local-stack-desktop.png"),
    fullPage: true,
  });

  await page.goto(`/experiments/${experimentId}`, { waitUntil: "networkidle" });
  await expect(page.getByText("Sessions & jobs")).toBeVisible();
  await expect(page.locator(".sessionPanel")).toHaveCount(2);
  await expect(page.locator(".stageItem")).toHaveCount(2);

  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({
    path: resolve(evidence, "local-stack-mobile.png"),
    fullPage: true,
  });
});
