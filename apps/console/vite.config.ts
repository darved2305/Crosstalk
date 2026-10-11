import { defineConfig } from "vite";

export default defineConfig({
  server: {
    port: 3000,
    proxy: {
      "/v1": {
        target: process.env.CT_CONSOLE_API_URL ?? "http://127.0.0.1:8080",
        changeOrigin: true,
      },
    },
  },
  preview: { port: 3000 },
});
