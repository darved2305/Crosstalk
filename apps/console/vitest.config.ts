import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    environment: "jsdom",
    include: ["src/**/*.test.ts", "src/**/*.test.tsx"],
    pool: "threads",
    maxWorkers: 1,
    // One reused worker: per-file worker startup takes ~10 s on this host and
    // intermittently hit the pool start timeout. Tests clean up their own DOM and stubs.
    isolate: false,
  },
});
