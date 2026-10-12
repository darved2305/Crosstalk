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

  it("accepts FastAPI timestamps with a numeric timezone offset", () => {
    const parsed = experimentPage.parse({
      items: [
        {
          id,
          name: "postgres-row",
          config_hash: "b".repeat(64),
          state: "running",
          created_at: "2026-10-12T13:28:11.842693+05:30",
          updated_at: "2026-10-12T13:28:11+05:30",
        },
      ],
      next_cursor: null,
    });
    expect(parsed.items[0]?.created_at).toContain("+05:30");
  });

  it("rejects malformed timestamps", () => {
    expect(() =>
      experimentPage.parse({
        items: [
          {
            id,
            name: "bad-time",
            config_hash: "c".repeat(64),
            state: "running",
            created_at: "yesterday",
            updated_at: "2026-10-12T13:28:11Z",
          },
        ],
      }),
    ).toThrow();
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

  it("preserves nanosecond precision from actual epoch timestamps", () => {
    const timestamp = "1791794233000000123";
    const parsed = traceResults.parse({
      session_id: id, request_id: id, layer: "records", source_points: 1,
      downsampled: false,
      points: [{t_start_ns: timestamp, t_end_ns: "1791794233000000124", min_value: 1, max_value: 2, count: 1}],
    });
    expect(parsed.points[0]?.t_start_ns.toString()).toBe(timestamp);
    expect(parsed.points[0]!.t_end_ns - parsed.points[0]!.t_start_ns).toBe(1n);
  });
});
