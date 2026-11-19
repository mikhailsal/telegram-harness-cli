import { expect, it } from "vitest";
import type { CliDependencies } from "../../src/cli/context.js";
import { isStopping } from "../../src/cli/service-unit.js";

// Regression: init could not tell that systemd was still stopping the unit, so
// it waited for systemctl in silence.
// Value: protects=isStopping reads ActiveState and is true only for
// "deactivating"; fails_when=another state counts as stopping, or a failed
// query throws instead of meaning "not stopping", or the query runs without
// a timeout; why_new=no test covered the unit state query; seam=none;
// no_claim=systemd is not run (exec is faked)

function deps(exec: CliDependencies["exec"]): CliDependencies {
  return { env: {}, exec } as unknown as CliDependencies;
}

it("recognizes a unit that systemd is still stopping", async () => {
  const calls: string[] = [];
  const timeouts: (number | undefined)[] = [];
  const stopping = await isStopping(
    "/srv/agent",
    deps((file, args, options) => {
      calls.push([file, ...args].join(" "));
      timeouts.push(options?.timeout);
      return Promise.resolve({ stdout: "deactivating\n", stderr: "" });
    }),
  );
  expect(stopping).toBe(true);
  expect(calls).toHaveLength(1);
  expect(calls[0]).toMatch(
    /^systemctl --user show thc-[0-9a-f]{24}\.service --property ActiveState --value$/,
  );
  // The query is bounded, so a stalled systemctl cannot hold init.
  expect(timeouts[0]).toBeGreaterThan(0);
});

it("treats every other state and a failed query as not stopping", async () => {
  for (const state of ["active", "inactive", "failed", "activating", ""])
    expect(
      await isStopping(
        "/srv/agent",
        deps(() => Promise.resolve({ stdout: `${state}\n`, stderr: "" })),
      ),
    ).toBe(false);
  expect(
    await isStopping(
      "/srv/agent",
      deps(() => Promise.reject(new Error("systemd user bus unavailable"))),
    ),
  ).toBe(false);
});
