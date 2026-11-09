import { mkdtemp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, expect, it } from "vitest";
import { run, type CliDependencies } from "../../src/cli/main.js";

const roots: string[] = [];
afterEach(async () => {
  for (const root of roots.splice(0))
    await rm(root, { recursive: true, force: true });
});

it("leaves an active initialized project and its cursors alone on bare init", async () => {
  const root = await mkdtemp(join(tmpdir(), "thc-rerun-"));
  roots.push(root);
  await mkdir(join(root, ".thc"));
  const config = JSON.stringify({
    token: { env: "TOKEN" },
    allowlist: [123],
    sandbox: "read-only",
  });
  await writeFile(join(root, ".thc", "config.json"), config);
  const calls: string[] = [];
  let output = "";
  const deps: Partial<CliDependencies> = {
    cwd: root,
    home: join(root, "home"),
    env: { TOKEN: "123456:abcdef" },
    isTTY: false,
    stdout: (value) => {
      output += value;
    },
    stderr: () => {
      /* Ignore diagnostic output. */
    },
    exec: (_file, args) => {
      calls.push(args.join(" "));
      return Promise.resolve({ stdout: "active\n", stderr: "" });
    },
  };
  expect(await run(["init"], deps)).toBe(0);
  expect(await readFile(join(root, ".thc", "config.json"), "utf8")).toBe(
    config,
  );
  expect(calls).toHaveLength(1);
  expect(calls[0]).toContain("is-active");
  expect(output).toContain("Already initialized and running");
});
