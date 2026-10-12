import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import { TracePlot } from "./trace-plot";

it("renders exact single-time measurements as visible marks with precise relative time", () => {
  const base = 1791794233000000123n;
  const container = document.createElement("div");
  container.innerHTML = renderToStaticMarkup(createElement(TracePlot, {trace:{
    session_id:"01K00000000000000000000000", request_id:"01K00000000000000000000001",
    layer:"records", source_points:2, downsampled:false,
    points:[0n, 1n].map((offset) => ({
      t_start_ns:base + offset, t_end_ns:base + offset,
      min_value:100, max_value:100, count:1,
    })),
  }}));
  const circles = [...container.querySelectorAll("circle")];
  expect(circles).toHaveLength(2);
  expect(circles[0]?.getAttribute("cx")).not.toBe(circles[1]?.getAttribute("cx"));
  expect(Number(circles[0]?.getAttribute("r"))).toBeGreaterThan(0);
});

it("encodes direction by sign with labelled axes and a legend", () => {
  const base = 1_000_000_000n;
  const container = document.createElement("div");
  container.innerHTML = renderToStaticMarkup(createElement(TracePlot, {trace:{
    session_id:"01K00000000000000000000000", request_id:"01K00000000000000000000001",
    layer:"records", source_points:3, downsampled:false,
    points:[
      {t_start_ns:base, t_end_ns:base, min_value:-517, max_value:-517, count:1},
      {t_start_ns:base + 2_000_000n, t_end_ns:base + 2_000_000n, min_value:1200, max_value:1200, count:1},
    ],
  }}));
  const stems = [...container.querySelectorAll("line.stem")];
  expect(stems).toHaveLength(2);
  const midline = (18 + (320 - 52)) / 2;
  expect(Number(stems[0]?.getAttribute("y2"))).toBeGreaterThan(midline);
  expect(Number(stems[1]?.getAttribute("y2"))).toBeLessThan(midline);
  expect(container.textContent).toContain("Signed TLS record bytes");
  expect(container.textContent).toContain("Time since first record (ms)");
  expect(container.textContent).toContain("Server → client (+), 1 points");
  expect(container.textContent).toContain("Client → server (−), 1 points");
});

it("draws per-direction extremes for downsampled bins", () => {
  const container = document.createElement("div");
  container.innerHTML = renderToStaticMarkup(createElement(TracePlot, {trace:{
    session_id:"01K00000000000000000000000", request_id:"01K00000000000000000000001",
    layer:"packets", source_points:30000, downsampled:true,
    points:[{t_start_ns:10n, t_end_ns:5_000_000n, min_value:-40, max_value:1448, count:15}],
  }}));
  expect(container.querySelectorAll("line.stem")).toHaveLength(2);
  expect(container.querySelectorAll("circle")).toHaveLength(0);
  expect(container.textContent).toContain("largest value per direction");
});
