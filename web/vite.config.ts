import react from "@vitejs/plugin-react";
import { viteSingleFile } from "vite-plugin-singlefile";
import { defineConfig } from "vitest/config";

// One self-contained HTML file, written into the Python package so the server never
// needs Node (spec 10). `audit-view.html` is the entry, so the output keeps its name.
export default defineConfig({
  plugins: [react(), viteSingleFile()],
  build: {
    outDir: "../src/tekmerion/ui",
    emptyOutDir: false,
    rollupOptions: { input: "audit-view.html" },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["src/test/setup.ts"],
  },
});
