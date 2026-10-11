import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./tests",
  timeout: 90_000,
  use: {
    ...devices["Desktop Chrome"],
    baseURL: "http://127.0.0.1:3000",
    screenshot: "only-on-failure",
    launchOptions: process.env.CT_PLAYWRIGHT_CHROMIUM
      ? { executablePath: process.env.CT_PLAYWRIGHT_CHROMIUM }
      : undefined,
  },
  webServer: {
    command: "pnpm dev",
    url: "http://127.0.0.1:3000",
    reuseExistingServer: !process.env.CI,
    timeout: 60_000,
  },
  reporter: "list",
});
