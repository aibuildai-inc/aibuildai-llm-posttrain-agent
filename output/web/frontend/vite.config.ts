import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Dev mode proxies /api to the local FastAPI app so development needs no
// CORS; the built app is served by that same app, same origin, so the
// production page needs no proxy at all.
export default defineConfig({
  plugins: [react()],
  esbuild: { charset: "ascii" },
  server: {
    proxy: {
      "/api": "http://127.0.0.1:8000",
    },
  },
  build: {
    outDir: "dist",
  },
});
