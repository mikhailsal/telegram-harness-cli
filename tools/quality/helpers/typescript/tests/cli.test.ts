import { writeFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, expect, test, vi } from "vitest";
import { run } from "../src/cli.ts";
import { HELPER, json, project } from "./fixtures.ts";

interface Outcome {
  readonly status: number;
  readonly stdout: string;
  readonly stderr: string;
}

async function invoke(argv: readonly string[]): Promise<Outcome> {
  let stdout = "";
  let stderr = "";
  const status = await run(argv, {
    write: (text) => {
      stdout += text;
    },
    warn: (text) => {
      stderr += text;
    },
  });
  return { status, stdout, stderr };
}

function request(value: unknown): string {
  const root = project({});
  const path = join(root, "request.json");
  writeFileSync(path, typeof value === "string" ? value : json(value));
  return path;
}

const ARGV = process.argv;

afterEach(() => {
  process.argv = ARGV;
  vi.restoreAllMocks();
  vi.resetModules();
  process.exitCode = 0;
});

test("a response is one JSON line with the protocol first", async () => {
  const path = request({
    protocol: 1,
    typescript: HELPER,
    tsconfig: null,
    files: [],
  });
  const outcome = await invoke(["structure", path]);
  expect(outcome).toEqual({
    status: 0,
    stdout: '{"protocol":1,"files":[]}\n',
    stderr: "",
  });
});

test("wrong arguments are a bad request", async () => {
  for (const argv of [[], ["structure"], ["structure", "a", "b"]]) {
    const outcome = await invoke(argv);
    expect(outcome.status).toBe(2);
    expect(outcome.stdout).toBe("");
    expect(outcome.stderr).toMatch(
      /^quality-typescript-helper: usage: main.ts/,
    );
  }
  const unknown = await invoke(["lint", "request.json"]);
  expect(unknown.status).toBe(2);
  expect(unknown.stderr).toMatch(/unknown command "lint"/);
});

test("an unreadable or invalid request is a bad request", async () => {
  const cases = [
    join(HELPER, "missing.json"),
    request("{"),
    request({ protocol: 0 }),
    request({ protocol: 1, typescript: "relative", tsconfig: null, files: [] }),
    request({
      protocol: 1,
      typescript: project({}),
      tsconfig: null,
      files: [],
    }),
  ];
  for (const path of cases) {
    const outcome = await invoke(["structure", path]);
    expect(outcome.status).toBe(2);
    expect(outcome.stderr).toMatch(/^quality-typescript-helper: \S.*\n$/);
  }
});

test("an unexpected failure exits with 1 and one line on stderr", async () => {
  const root = project({});
  const path = request({
    protocol: 1,
    eslint: HELPER,
    cwd: root,
    files: [join(root, "a.ts")],
  });
  const outcome = await invoke(["eslint-config", path]);
  expect(outcome).toEqual({
    status: 1,
    stdout: "",
    stderr: "quality-typescript-helper: Could not find config file.\n",
  });
});

test("the entry point runs the command line and sets the exit status", async () => {
  const stdout = vi
    .spyOn(process.stdout, "write")
    .mockImplementation(() => true);
  const stderr = vi
    .spyOn(process.stderr, "write")
    .mockImplementation(() => true);
  const path = request({
    protocol: 1,
    typescript: HELPER,
    tsconfig: null,
    files: [],
  });
  process.argv = ["node", "main.ts", "types", path];
  await import("../src/main.ts");
  expect(process.exitCode).toBe(2);
  expect(stderr).toHaveBeenCalledOnce();
  vi.resetModules();
  process.argv = ["node", "main.ts", "structure", path];
  await import("../src/main.ts");
  expect(process.exitCode).toBe(0);
  expect(stdout).toHaveBeenCalledWith('{"protocol":1,"files":[]}\n');
});
