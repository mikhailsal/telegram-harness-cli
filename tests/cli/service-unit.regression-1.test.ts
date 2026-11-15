import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, beforeEach, expect, it } from "vitest";
import { configureUnit } from "../../src/cli/service-unit.js";

// Regression: systemd 249 rejected the unit because WorkingDirectory= was quoted
// ("path is not absolute"), so thc init failed when starting the service.
// Value: protects=WorkingDirectory= holds a bare absolute path systemd accepts;
// fails_when=the path is quoted or % is not escaped; why_new=no test read that
// line; seam=none; no_claim=systemd itself is not run (unit text only)

let home: string;

beforeEach(() => {
  home = mkdtempSync(join(tmpdir(), "thc-unit-"));
});
afterEach(() => {
  rmSync(home, { recursive: true, force: true });
});

function workingDirectory(directory: string): Promise<string> {
  return configureUnit(home, directory, "1.0.0", undefined).then((name) => {
    const unit = readFileSync(
      join(home, ".config", "systemd", "user", name),
      "utf8",
    );
    return (
      unit.split("\n").find((line) => line.startsWith("WorkingDirectory=")) ??
      ""
    );
  });
}

it.each([
  ["/home/user/project", "WorkingDirectory=/home/user/project"],
  ["/home/user/my project", "WorkingDirectory=/home/user/my project"],
  ["/home/user/100%", "WorkingDirectory=/home/user/100%%"],
])("writes %s as a bare WorkingDirectory path", async (directory, line) => {
  expect(await workingDirectory(directory)).toBe(line);
});

it("refuses a working directory containing a newline", async () => {
  await expect(workingDirectory("/home/user/a\nb")).rejects.toThrow(
    "Invalid path for a systemd unit",
  );
});
