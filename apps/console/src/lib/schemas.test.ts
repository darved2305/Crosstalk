import { describe, expect, it } from "vitest";

import { experimentPage, traceResults } from "./schemas";

const id = "01K00000000000000000000000";

describe("API runtime contracts", () => {
  it("accepts empty experiment pages", () => {
    expect(experimentPage.parse({ items: [], next_cursor: null }).items).toEqual([]);
  });

  it("preserves invalid and partial states", () => {
    const parsed = experimentPage.parse({
      items: [
        {
          id,
          name: "fixture",
          config_hash: "a".repeat(64),
          state: "partial",
          created_at: "2026-10-07T00:00:00Z",
          updated_at: "2026-10-07T00:00:00Z",
        },
      ],
    });
    expect(parsed.items[0]?.state).toBe("partial");
  });

  it("validates real-shaped downsampled traces", () => {
    const parsed = traceResults.parse({
      session_id: id,
      request_id: id,
      layer: "records",
      source_points: 25_000,
      downsampled: true,
      points: [{ t_start_ns: 1, t_end_ns: 10, min_value: 100, max_value: 250, count: 3 }],
    });
    expect(parsed.downsampled).toBe(true);
    expect(parsed.points[0]?.count).toBe(3);
  });

  it("rejects malformed identifiers", () => {
    expect(() => traceResults.parse({ session_id: "../escape" })).toThrow();
  });
});
