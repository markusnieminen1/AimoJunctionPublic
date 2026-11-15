/// <reference types="vitest" />
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// One proxy rule covers the API, /api/docs, /api/openapi.json and /api/mcp (AGENTS.md rule 11).
// API_PROXY_TARGET is a server-side-only override (read by Vite's Node process, never exposed to
// client code because it has no VITE_ prefix). Used by scripts/integration-backend.sh to point a
// dev server at an isolated backend instance. Default: the shared backend on :8001.
const apiTarget = process.env.API_PROXY_TARGET || "http://127.0.0.1:8001";
const proxy = {
  "/api": { target: apiTarget, changeOrigin: false },
};

export default defineConfig({
  plugins: [react()],
  server: { host: "127.0.0.1", port: 8000, strictPort: true, allowedHosts: true, fs: { allow: [".", ".."] }, proxy },
  preview: { host: "127.0.0.1", port: 8000, strictPort: true, allowedHosts: true, proxy },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
    css: false,
  },
});
