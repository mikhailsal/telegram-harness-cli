import {
  mkdtemp,
  mkdir,
  rm,
  symlink,
  truncate,
  writeFile,
} from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { expect, it } from "vitest";
import { renderOutgoing } from "../../src/channels/render.js";

it("extracts only explicit visible regular files inside the workspace", async () => {
  const base = await mkdtemp(join(tmpdir(), "thc-local-media-"));
  const root = join(base, "work");
  await mkdir(join(root, ".thc", "files"), { recursive: true });
  const good = join(root, "image.png"),
    outside = join(base, "secret.txt");
  await writeFile(good, "image");
  await writeFile(outside, "secret");
  await writeFile(join(root, ".thc", "secret.txt"), "private");
  await writeFile(join(root, ".thc", "files", "input.txt"), "allowed");
  await symlink(outside, join(root, "link.txt"));
  try {
    const input = `[image](${good}) [download](.thc/files/input.txt) [secret](.thc/secret.txt) [escape](../secret.txt) [symlink](link.txt) [remote](https://example.com/file.png)\n\n\`[code](${good})\`\n\n<internal-note>[hidden](${good})</internal-note>`;
    const rendered = renderOutgoing(input, ["internal-note"], root);
    const media = rendered.chunks.filter(
      (chunk) => chunk.kind === "photo" || chunk.kind === "document",
    );
    expect(media.map((chunk) => [chunk.kind, chunk.fileName])).toEqual([
      ["photo", "image.png"],
      ["document", "input.txt"],
    ]);
    expect(rendered.chunks[0]?.text).toContain("image download");
    expect(rendered.chunks[0]?.text).toContain("https://example.com/file.png");
    expect(rendered.visibleText).not.toContain("[hidden]");
  } finally {
    await rm(base, { recursive: true, force: true });
  }
});
it("does not read oversized files or infer media from code or unclosed hidden text", async () => {
  const root = await mkdtemp(join(tmpdir(), "thc-big-media-"));
  const big = join(root, "big.png"),
    small = join(root, "small.png");
  await writeFile(big, "");
  await truncate(big, 20 * 1024 * 1024 + 1);
  await writeFile(small, "photo");
  try {
    const result = renderOutgoing(
      `[large](${big}) \`[example](${small})\` <internal-note>[hidden](${small})`,
      ["internal-note"],
      root,
    );
    expect(result.chunks.every((chunk) => chunk.kind === "message")).toBe(true);
    expect(result.visibleText).not.toContain("hidden");
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});
it("accepts a file URL for an in-workspace image and keeps remote URLs as links", async () => {
  const root = await mkdtemp(join(tmpdir(), "thc-file-url-"));
  const image = join(root, "space name.png");
  await writeFile(image, "photo");
  try {
    const { pathToFileURL } = await import("node:url");
    const result = renderOutgoing(
      `![picture](${pathToFileURL(image).href}) [remote](https://example.com/x.png)`,
      [],
      root,
    );
    expect(result.chunks.map((chunk) => chunk.kind)).toEqual([
      "message",
      "photo",
    ]);
    expect(result.chunks[0]?.plain).toBe("picture remote");
    expect(result.chunks[0]?.text).toContain("https://example.com/x.png");
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});
it("preserves local attachment labels in regular HTML without linking the filesystem path", async () => {
  const root = await mkdtemp(join(tmpdir(), "thc-regular-media-"));
  const path = join(root, "image.png");
  await writeFile(path, "photo");
  try {
    const result = renderOutgoing(
      `**Done** [picture](${path})`,
      [],
      root,
      "regular",
    );
    expect(result.chunks.map((chunk) => chunk.kind)).toEqual([
      "message",
      "photo",
    ]);
    expect(result.chunks[0]?.text).toBe("<b>Done</b> picture");
    expect(result.chunks[0]?.text).not.toContain(root);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});
