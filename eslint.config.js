import js from "@eslint/js";
import tseslint from "typescript-eslint";

export default tseslint.config(
  // This config lints the application component (`src`, `scripts`, `tests`).
  // `examples/web` and the quality helpers are their own components with their
  // own configs and dependencies, which `./quality check` lints from their
  // roots; build output, coverage and the gate's local `.quality/` area are
  // generated. Without these ignores a bare `npx eslint .` at the root walks
  // into them and fails to load their plugins.
  {
    ignores: [
      "examples/",
      "tools/",
      "dist/",
      "coverage/",
      ".quality/",
      "eslint.config.js",
    ],
  },
  js.configs.recommended,
  tseslint.configs.strictTypeChecked,
  tseslint.configs.stylisticTypeChecked,
  {
    languageOptions: {
      parserOptions: {
        projectService: true,
        tsconfigRootDir: import.meta.dirname,
      },
    },
  },
);
