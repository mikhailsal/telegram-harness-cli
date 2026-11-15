import { spawnSync } from "node:child_process";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";
import { expect, it } from "vitest";

const driver = String.raw`
import json, os, pty, select, signal, sys, termios, time
node, module, action, mode = sys.argv[1:]
program = '''
const { prompt } = await import(process.argv[1]);
try { await prompt("TOKEN: ", process.argv[2] !== "visible"); console.log("ACCEPTED"); }
catch { console.log("CANCELLED"); }
'''
pid, fd = pty.fork()
if pid == 0:
    os.execv(node, [node, '--input-type=module', '-e', program, module, mode])
data = b''
sent = False
status = None
start = time.monotonic()
while time.monotonic() - start < 10:
    ready, _, _ = select.select([fd], [], [], 0.1)
    if ready:
        try: data += os.read(fd, 4096)
        except OSError: pass
        if b'TOKEN: ' in data and not sent:
            os.write(fd, {'input': b'fixture-secret\n', 'interrupt': b'\x03', 'eof': b'\x04'}[action])
            sent = True
    done, code = os.waitpid(pid, os.WNOHANG)
    if done:
        status = os.waitstatus_to_exitcode(code)
        break
if status is None:
    os.kill(pid, signal.SIGKILL)
    os.waitpid(pid, 0)
    raise SystemExit('PTY child timed out')
restored = termios.tcgetattr(fd)[3]
print(json.dumps({'status': status, 'sent': sent, 'output': data.decode(errors='replace'),
                  'terminalRestored': (restored & (termios.ECHO | termios.ICANON)) == (termios.ECHO | termios.ICANON)}))
`;

function exercise(
  action: string,
  mode = "secret",
): {
  status: number;
  sent: boolean;
  output: string;
  terminalRestored: boolean;
} {
  const module = pathToFileURL(resolve("dist/cli/onboarding.js")).href;
  const child = spawnSync(
    "python3",
    ["-c", driver, process.execPath, module, action, mode],
    {
      encoding: "utf8",
      timeout: 15_000,
    },
  );
  expect(child.status, child.stderr).toBe(0);
  return JSON.parse(child.stdout) as {
    status: number;
    sent: boolean;
    output: string;
    terminalRestored: boolean;
  };
}

it("hides immediately entered secrets and restores the terminal", () => {
  const result = exercise("input");
  expect(result).toMatchObject({
    status: 0,
    sent: true,
    terminalRestored: true,
  });
  expect(result.output).toContain("ACCEPTED");
  expect(result.output).not.toContain("fixture-secret");
});

it.each(["interrupt", "eof"])(
  "restores the terminal when input is cancelled with %s",
  (action) => {
    const result = exercise(action);
    expect(result).toMatchObject({
      status: 0,
      sent: true,
      terminalRestored: true,
    });
    expect(result.output).toContain("CANCELLED");
  },
);

// Regression: ISSUE-001 — visible prompt labels were erased by readline
// Found by /qa on 2026-11-15
// Report: .gstack/qa-reports/qa-report-thc-init-2026-11-15.md
it("keeps a visible prompt label on screen after readline redraws", () => {
  const result = exercise("input", "visible");
  expect(result).toMatchObject({ status: 0, sent: true });
  const screen = result.output.split("\u001b[0J").at(-1) ?? "";
  expect(screen).toContain("TOKEN: ");
  expect(result.output).toContain("ACCEPTED");
});
