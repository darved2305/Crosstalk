import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import type { FrontierResults } from "../lib/schemas";
import { FrontierPlot } from "./frontier-plot";

const data: FrontierResults = {
  experiment_id: "01K00000000000000000000000", setting: "adaptive", cost: "bandwidth",
  rows: [-40, 20].map((cost, index) => ({
    session_id: null, experiment_id: "01K00000000000000000000000", concurrency: 1,
    condition: `a${index + 1}`, setting: "adaptive", model: "lr", representation: "r1",
    balanced_accuracy: 0.8, rho: index === 0 ? 1.3 : 2,
    rho_lo: index === 0 ? 1.2 : 1.8, rho_hi: index === 0 ? 1.4 : 2.2,
    bandwidth_overhead_pct: cost, ttft_p95_ms: 10, itl_p99_ms: 12,
  })),
};

it("keeps negative costs on the plot and distinguishes leakage above one", () => {
  const container = document.createElement("div");
  container.innerHTML = renderToStaticMarkup(createElement(FrontierPlot, {data}));
  const circles = [...container.querySelectorAll("svg.frontierPlot circle")];
  expect(circles).toHaveLength(2);
  for (const circle of circles) {
    expect(Number(circle.getAttribute("cx"))).toBeGreaterThanOrEqual(70);
    expect(Number(circle.getAttribute("cx"))).toBeLessThanOrEqual(780);
  }
  expect(circles[0]?.getAttribute("cy")).not.toBe(circles[1]?.getAttribute("cy"));
  expect(container.querySelectorAll(".whisker")).toHaveLength(2);
  expect(container.textContent).toContain("Bandwidth overhead (%)");
  expect(container.textContent).toContain("Retained leakage ρ (95% CI)");
  expect(container.querySelector("svg title")?.textContent).toContain("adaptive attacker");
});

it("explains undefined measured leakage without generating plotted points", () => {
  const container = document.createElement("div");
  container.innerHTML = renderToStaticMarkup(createElement(FrontierPlot, {
    data: {...data, rows:data.rows.map((row) => ({...row, rho:null}))},
  }));
  expect(container.textContent).toContain("Retained leakage ρ is undefined for all 2 rows");
  expect(container.querySelectorAll("svg.frontierPlot circle")).toHaveLength(0);
});

it("keeps the reference line for rho = 1 and the zero-cost line when costs are negative", () => {
  const container = document.createElement("div");
  container.innerHTML = renderToStaticMarkup(createElement(FrontierPlot, {data}));
  expect(container.textContent).toContain("ρ = 1");
  expect(container.querySelectorAll("line.reference")).toHaveLength(1);
});
