import { defineConfig, devices } from "@playwright/test";

// CT_E2E_BASE_URL points the opt-in real-stack specs at an already running console
// (for example http://127.0.0.1:3010 proxying to a live API). Without it, the
// fixture specs start their own dev server and intercept every /v1 request.
const externalBaseURL = process.env.CT_E2E_BASE_URL;

export default defineConfig({
  testDir: "./tests",
  timeout: 90_000,
  use: {
    ...devices["Desktop Chrome"],
    baseURL: externalBaseURL ?? "http://127.0.0.1:3000",
    screenshot: "only-on-failure",
    launchOptions: process.env.CT_PLAYWRIGHT_CHROMIUM
      ? { executablePath: process.env.CT_PLAYWRIGHT_CHROMIUM }
      : undefined,
  },
  webServer: externalBaseURL
    ? undefined
    : {
        command: "pnpm dev",
        url: "http://127.0.0.1:3000",
        reuseExistingServer: !process.env.CI,
        timeout: 60_000,
      },
  reporter: "list",
});
