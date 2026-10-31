import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    include: ["tests/**/*.{test,spec}.?(c|m)[jt]s?(x)"],
    globalSetup: ["tests/core/setup-build.ts"],
    testTimeout: 20_000,
    coverage: {
      provider: "v8",
      include: ["src/**/*.{ts,mts}", "scripts/**/*.{ts,mts}"],
      reporter: ["text", "json", "lcov"],
    },
  },
});
