import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { loadEnv } from "vite";
import { defineConfig } from "vitest/config";

import { atlas_dev_launch_stack_plugin } from "./vite-plugins/atlas-dev-launch";

export default defineConfig(({ mode }) => {
  /** Shell wins over `frontend/.env*` so CI and scripts stay predictable. */
  const fileEnv = loadEnv(mode, process.cwd(), "");
  const atlasApiHost =
    process.env.ATLAS_API_HOST || fileEnv.ATLAS_API_HOST || "127.0.0.1";
  const atlasApiPort =
    process.env.ATLAS_API_PORT || fileEnv.ATLAS_API_PORT || "8787";
  const atlasBackendHttp = `http://${atlasApiHost}:${atlasApiPort}`;
  const atlasBackendWs = `ws://${atlasApiHost}:${atlasApiPort}`;

  return {
    plugins: [react(), tailwindcss(), atlas_dev_launch_stack_plugin()],
    server: {
      host: "0.0.0.0",
      port: 5173,
      proxy: {
        "/api": {
          target: atlasBackendHttp,
          changeOrigin: true,
        },
        "/ws": {
          target: atlasBackendWs,
          changeOrigin: true,
          ws: true,
        },
      },
    },
    test: {
      environment: "node",
      include: ["src/**/*.test.ts", "src/**/*.test.tsx"],
    },
  };
});
