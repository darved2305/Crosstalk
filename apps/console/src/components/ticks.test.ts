import { expect, it } from "vitest";
import { niceTicks } from "./ticks";

it("covers the data range with round steps, including negatives", () => {
  const ticks = niceTicks(-37.9, 157.2);
  expect(ticks[0]).toBeLessThanOrEqual(-37.9);
  expect(ticks.at(-1)).toBeGreaterThanOrEqual(157.2);
  expect(ticks).toContain(0);
});

it("handles a flat range", () => {
  expect(niceTicks(5, 5).length).toBeGreaterThan(1);
});
