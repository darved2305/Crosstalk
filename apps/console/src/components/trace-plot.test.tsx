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
