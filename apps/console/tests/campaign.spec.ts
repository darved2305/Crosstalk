import { expect, test } from "@playwright/test";
import { readFile, mkdir } from "node:fs/promises";
import { resolve } from "node:path";

const token = process.env.CT_E2E_OPERATOR_TOKEN ?? "";
test("retained measured campaign renders through the real API", async ({ page, request }) => {
  test.skip(!token, "Set CT_E2E_OPERATOR_TOKEN after importing the retained campaign.");
  const evidence = resolve(process.cwd(), "../../docs/evidence/campaign");
  const campaign = JSON.parse(await readFile(resolve(evidence, "campaign.json"), "utf8")) as {
    experiments: { id: string; name: string; evaluations: number; sessions: {id: string; request_id: string}[] }[];
  };
  const images = resolve(evidence, "screenshots");
  await mkdir(images, { recursive: true });
  await page.addInitScript((value) => sessionStorage.setItem("crosstalk.operatorToken", value), token);
  await page.setViewportSize({width: 1504, height: 1128});
  for (const experiment of campaign.experiments) {
    const response = await request.get(`http://127.0.0.1:18080/v1/experiments/${experiment.id}/results`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    expect(response.status()).toBe(200);
    const result = await response.json() as {rows: unknown[]; reportable: boolean};
    expect(result.rows).toHaveLength(experiment.evaluations);
    expect(result.reportable).toBe(false);
    await page.goto(`/results?experiment=${experiment.id}`);
    await expect(page.locator("tbody tr")).toHaveCount(experiment.evaluations);
    await expect(page.getByRole("status")).toContainText("NOT REPORTABLE");
    await page.screenshot({ path: resolve(images, `${experiment.name}-results.png`) });
    await page.goto(`/reports?experiment=${experiment.id}`);
    await expect(page.locator(".reportPanel pre")).toContainText("DEVELOPMENT RESULT, NOT REPORTABLE");
    await page.screenshot({path: resolve(images, `${experiment.name}-report.png`)});
  }
  const e2 = campaign.experiments.find((e) => e.name === "e2-e2e")!;
  await page.goto(`/frontier?experiment=${e2.id}`);
  await expect(page.getByRole("status")).toContainText("NOT REPORTABLE");
  await expect(page.locator(".chartFoot")).toContainText("8 result rows");
  await page.screenshot({path: resolve(images, "e2-frontier.png")});
  const session = e2.sessions[0]!;
  await page.goto(`/traces?session=${session.id}&request=${session.request_id}`);
  await expect(page.getByRole("status")).toContainText("synthetic TCP packet segmentation");
  await expect(page.getByRole("img", {name: "records signed-byte timeline"})).toBeVisible();
  await page.screenshot({path: resolve(images, "tls-trace.png")});
  const denied = await request.get(`http://127.0.0.1:18080/v1/sessions/${session.id}/traces/${session.request_id}/emissions`, {
    headers: {Authorization: `Bearer ${token}`},
  });
  expect(denied.status()).toBe(403);
  await page.goto(`/traces?session=${session.id}&request=${session.request_id}&layer=packets`);
  await expect(page.getByRole("img", {name:"packets signed-byte timeline"})).toBeVisible();
  await page.screenshot({path:resolve(images,"packet-trace.png")});
  await page.setViewportSize({width:390, height:844});
  await page.goto(`/results?experiment=${e2.id}`);
  await expect(page.locator("tbody tr")).toHaveCount(e2.evaluations);
  await page.screenshot({path:resolve(images, "mobile-results.png")});
});

test("real control API mutations are durable and idempotent", async ({ request }) => {
  test.skip(!token, "Set CT_E2E_OPERATOR_TOKEN for real API mutations.");
  const headers = {Authorization: `Bearer ${token}`};
  const name = `browser-validation-${Date.now()}`;
  const created = await request.post("http://127.0.0.1:18080/v1/experiments", {
    headers, data: {name, sessions:[{experiment_id:name, requests:1, concurrency:1, seed:0, model:"mock"}]},
  });
  expect(created.status()).toBe(201);
  const experiment = await created.json() as {id:string};
  const url = `http://127.0.0.1:18080/v1/experiments/${experiment.id}`;
  const runHeaders = {...headers, "Idempotency-Key":"browser-verification"};
  const first = await request.post(`${url}/runs`, {headers:runHeaders});
  const replay = await request.post(`${url}/runs`, {headers:runHeaders});
  expect(first.status()).toBe(202);
  const a = await first.json() as {session_ids:string[]; replayed:boolean};
  const b = await replay.json() as {session_ids:string[]; replayed:boolean};
  expect(b.session_ids).toEqual(a.session_ids);
  expect(a.replayed).toBe(false);
  expect(b.replayed).toBe(true);
  const cancelled = await request.post(`${url}/cancel`, {headers});
  expect(cancelled.status()).toBe(200);
  const detail = await request.get(url, {headers});
  const body = await detail.json() as {state:string; sessions:{state:string; jobs:{state:string}[]}[]};
  expect(body.state).toBe("cancelled");
  expect(body.sessions[0]?.state).toBe("CANCELLED");
  expect(body.sessions[0]?.jobs[0]?.state).toBe("cancelled");
  const conflict = await request.post(`${url}/runs`, {headers:{...headers,"Idempotency-Key":"after-cancel"}});
  expect(conflict.status()).toBe(409);
  const invalid = await request.get("http://127.0.0.1:18080/v1/experiments/invalid",{headers});
  expect(invalid.status()).toBe(422);
});
