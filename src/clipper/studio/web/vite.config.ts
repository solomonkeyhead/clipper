import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { resolve } from "node:path";
import { defineConfig } from "vite";

// Built into ../static, which the FastAPI server serves. `npm run dev` proxies
// the API to a running `clipper studio`.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: { alias: { "@": resolve(__dirname, "src") } },
  build: {
    outDir: resolve(__dirname, "../static"),
    emptyOutDir: true,
    chunkSizeWarningLimit: 700,
  },
  server: {
    port: 5178,
    proxy: {
      "/api": { target: "http://127.0.0.1:8765", changeOrigin: false },
      "/media": "http://127.0.0.1:8765",
      "/thumb": "http://127.0.0.1:8765",
    },
  },
});
