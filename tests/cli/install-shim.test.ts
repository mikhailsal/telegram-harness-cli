import {
  mkdtempSync,
  mkdirSync,
  rmSync,
  symlinkSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { afterEach, beforeEach, expect, it } from "vitest";
import { install } from "../../src/cli/install.js";

let root: string;
let home: string;
let directory: string;
let pkg: string;
beforeEach(() => {
  root = mkdtempSync(join(tmpdir(), "thc-shim-"));
  home = join(root, "home");
  directory = join(root, "agent");
  pkg = join(root, "package");
  for (const path of [
    join(directory, ".thc"),
    join(pkg, "dist", "cli"),
    join(pkg, "skills"),
    join(pkg, "node_modules"),
  ])
    mkdirSync(path, { recursive: true });
  writeFileSync(join(pkg, "package.json"), "{}");
  writeFileSync(join(pkg, "package-lock.json"), "{}");
  writeFileSync(join(pkg, "dist", "cli", "main.js"), "export {};");
});
afterEach(() => {
  rmSync(root, { recursive: true, force: true });
});

it("keeps the private Node and Codex reachable after the caller's PATH", async () => {
  await install(
    {
      home,
      directory,
      packageRoot: pkg,
      exec: () => Promise.resolve({ stdout: "", stderr: "" }),
    },
    "2.0.0",
  );
  const share = join(home, ".local", "share", "thc");
  writeFileSync(
    join(share, "2.0.0", "dist", "cli", "main.js"),
    "console.log(process.env.PATH)",
  );
  const tools = (data: string, name: string) =>
    join(data, "thc", "toolchains", name, "bin");
  for (const name of ["codex-0.165.0", "codex-0.166.0", "node-v24.22.0"])
    mkdirSync(tools(join(home, ".local", "share"), name), { recursive: true });
  mkdirSync(tools(join(root, "xdg"), "codex-0.170.0"), { recursive: true });
  const { execFileSync } = await import("node:child_process");
  const shim = join(home, ".local", "bin", "thc");
  const path = (env: Record<string, string>) =>
    execFileSync(shim, ["status"], {
      cwd: root,
      env: { HOME: home, ...env },
      encoding: "utf8",
    }).trim();
  const node = dirname(process.execPath);
  const defaults = join(home, ".local", "share");
  const newestFirst = [
    tools(defaults, "codex-0.166.0"),
    tools(defaults, "codex-0.165.0"),
  ];
  expect(path({ PATH: "/synthetic/bin" })).toBe(
    ["/synthetic/bin", node, ...newestFirst].join(":"),
  );
  expect(path({ PATH: `/synthetic/bin:${node}` })).toBe(
    ["/synthetic/bin", node, ...newestFirst].join(":"),
  );
  expect(
    path({ PATH: "/synthetic/bin", XDG_DATA_HOME: join(root, "xdg") }),
  ).toBe(
    ["/synthetic/bin", node, tools(join(root, "xdg"), "codex-0.170.0")].join(
      ":",
    ),
  );
});

it("puts the private Node ahead of an older Node, which Codex would otherwise launch on", async () => {
  const bin = join(
    home,
    ".local",
    "share",
    "thc",
    "toolchains",
    "node-v24.22.0",
    "bin",
  );
  mkdirSync(bin, { recursive: true });
  symlinkSync(process.execPath, join(bin, "node"));
  await install(
    {
      home,
      directory,
      packageRoot: pkg,
      exec: () => Promise.resolve({ stdout: "", stderr: "" }),
      execPath: join(bin, "node"),
    },
    "2.0.0",
  );
  writeFileSync(
    join(home, ".local", "share", "thc", "2.0.0", "dist", "cli", "main.js"),
    "console.log(process.env.PATH)",
  );
  const { execFileSync } = await import("node:child_process");
  const path = (value: string) =>
    execFileSync(join(home, ".local", "bin", "thc"), ["status"], {
      cwd: root,
      env: { HOME: home, PATH: value },
      encoding: "utf8",
    }).trim();
  expect(path("/old/node/bin:/usr/bin")).toBe(
    [bin, "/old/node/bin", "/usr/bin"].join(":"),
  );
  // Already present further back: moved to the front, never repeated.
  expect(path(`/old/node/bin:${bin}:/usr/bin`)).toBe(
    [bin, "/old/node/bin", "/usr/bin"].join(":"),
  );
});
