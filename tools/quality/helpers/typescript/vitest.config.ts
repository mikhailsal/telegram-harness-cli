import { defineConfig } from "vitest/config";

// Type checking a fixture project loads the standard library declarations.
export default defineConfig({ test: { testTimeout: 60_000 } });
