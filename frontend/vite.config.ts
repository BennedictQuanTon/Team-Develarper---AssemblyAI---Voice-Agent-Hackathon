import { defineConfig } from "vite";
import { resolve } from "path";

const backend = "http://127.0.0.1:8000";

export default defineConfig({
  root: resolve(__dirname, "."),
  base: "./",
  build: {
    outDir: "dist",
    emptyOutDir: true,
    target: "es2022",
  },
  server: {
    // Reachable from table devices on the same network, not just this machine.
    host: "0.0.0.0",
    port: 5173,
    proxy: {
      "/ws": {
        target: "ws://127.0.0.1:8000",
        ws: true,
      },
      "/api": { target: backend },
      "/menu": { target: backend },
      "/floor": { target: backend },
      "/metrics": { target: backend },
      "/ready": { target: backend },
      "/health": { target: backend },
    },
  },
});
