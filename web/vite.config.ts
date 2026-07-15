import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// Dev flow: `python3 transcribr.py --serve` (port 8737, token "dev") in one
// terminal, `npm run dev` here in another. The proxy injects the token so
// the browser session exercises the same auth path as production.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  build: { outDir: "../webdist", emptyOutDir: true },
  server: {
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8737",
        headers: { "X-Transcribr-Token": "dev" },
      },
      "/audio": {
        target: "http://127.0.0.1:8737",
        headers: { "X-Transcribr-Token": "dev" },
      },
    },
  },
});
