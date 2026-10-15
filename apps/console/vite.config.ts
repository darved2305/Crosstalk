import { defineConfig } from "vite";

export default defineConfig({
  server: {
    port: 3000,
    proxy: {
      "/v1": {
        target: process.env.CT_CONSOLE_API_URL ?? "http://127.0.0.1:8080",
        changeOrigin: true,
        // Mirrors nginx.conf.template: the console sends its token in a header that
        // fronting proxies leave alone; the API reads the standard one.
        configure: (proxy) => {
          proxy.on("proxyReq", (request, incoming) => {
            const token = incoming.headers["x-crosstalk-authorization"];
            if (typeof token !== "string") return;
            request.setHeader("authorization", token);
            request.removeHeader("x-crosstalk-authorization");
          });
        },
      },
    },
  },
  preview: { port: 3000 },
});
