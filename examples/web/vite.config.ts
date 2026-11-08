import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Every asset is a file of the build, never a data: URL, so the gate checks
// that each reference resolves.
export default defineConfig({
  build: { assetsInlineLimit: 0 },
  plugins: [react()],
});
