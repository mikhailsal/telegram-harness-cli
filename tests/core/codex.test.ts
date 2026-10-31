import { mkdtemp, mkdir, rm, symlink, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { expect, it } from "vitest";
import { nativeCodex, sandboxPolicy } from "../../src/core/codex.ts";
it.each(["codex", "bin"])(
  "locates native binaries through the %s vendor layout",
  async (layout) => {
    const root = await mkdtemp(join(tmpdir(), "thc-native-"));
    try {
      const native = join(root, "native");
      await writeFile(native, Buffer.from([0x7f, 0x45, 0x4c, 0x46]));
      expect(await nativeCodex(native, root)).toBe(native);
      const bin = join(root, "bin");
      const vendor = join(
        root,
        "vendor",
        `${process.arch === "arm64" ? "aarch64" : "x86_64"}-unknown-linux-musl`,
        layout,
      );
      await mkdir(bin, { recursive: true });
      await mkdir(vendor, { recursive: true });
      const wrapper = join(bin, "codex.js");
      await writeFile(wrapper, "#!/usr/bin/env node\n");
      await writeFile(join(vendor, "codex"), "not an executable");
      await expect(nativeCodex(wrapper, root)).rejects.toThrow(
        "Cannot locate native Codex executable",
      );
      await rm(join(vendor, "codex"));
      await symlink(native, join(vendor, "codex"));
      expect(await nativeCodex(wrapper, root)).toBe(native);
      await expect(nativeCodex(join(bin, "missing"), root)).rejects.toThrow();
    } finally {
      await rm(root, { recursive: true, force: true });
    }
  },
);
it("maps only supported sandbox choices", () => {
  expect(sandboxPolicy("read-only", "/work")).toMatchObject({
    type: "readOnly",
    networkAccess: false,
  });
  expect(sandboxPolicy("workspace-write", "/work")).toMatchObject({
    type: "workspaceWrite",
    writableRoots: ["/work"],
    networkAccess: false,
  });
  expect(sandboxPolicy("danger-full-access", "/work")).toEqual({
    type: "dangerFullAccess",
  });
  expect(() => sandboxPolicy("unexpected", "/work")).toThrow("Unsupported");
});
