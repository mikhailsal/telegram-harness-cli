import { createHash } from "node:crypto";
import {
  closeSync,
  constants,
  fstatSync,
  openSync,
  readSync,
  realpathSync,
} from "node:fs";
import { basename, extname, isAbsolute, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const MAX_FILE = 20 * 1024 * 1024;
const MAX_PHOTO = 10_000_000;
export interface LocalMedia {
  kind: "photo" | "document";
  filePath: string;
  fileName: string;
  digest: string;
}
export interface MediaNode {
  type: string;
  url?: string | undefined;
  alt?: string | null | undefined;
  value?: string | undefined;
  children?: unknown[] | undefined;
  position?:
    | {
        start: { offset?: number | undefined };
        end: { offset?: number | undefined };
      }
    | undefined;
}
export interface MediaEdit {
  start: number;
  end: number;
  replacement: string;
  media: LocalMedia;
}
export class LocalMediaError extends Error {}
function permitted(root: string, path: string): boolean {
  const inside = relative(root, path);
  if (
    !inside ||
    inside === ".." ||
    inside.startsWith(`../`) ||
    isAbsolute(inside)
  )
    return false;
  const parts = inside.split("/");
  return parts[0] !== ".thc" || parts[1] === "files";
}
function localPath(url: string, root: string): string | undefined {
  if (url.startsWith("file:")) {
    try {
      return fileURLToPath(url);
    } catch {
      return;
    }
  }
  if (!url || /^(?:[a-z][a-z0-9+.-]*:|\/\/|#)/iu.test(url)) return;
  try {
    return resolve(root, decodeURIComponent(url));
  } catch {
    return;
  }
}
function readChecked(
  root: string,
  path: string,
): { path: string; bytes: Buffer } {
  const base = realpathSync(root);
  const fd = openSync(
    path,
    constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK,
  );
  try {
    const actual = realpathSync(`/proc/self/fd/${String(fd)}`);
    const info = fstatSync(fd);
    if (!permitted(base, actual) || !info.isFile() || info.size > MAX_FILE)
      throw new LocalMediaError("local-file-unavailable");
    const bytes = Buffer.alloc(info.size);
    let offset = 0;
    while (offset < bytes.length) {
      const count = readSync(fd, bytes, offset, bytes.length - offset, offset);
      if (!count) throw new LocalMediaError("local-file-changed");
      offset += count;
    }
    const final = fstatSync(fd);
    if (
      final.size !== info.size ||
      final.mtimeMs !== info.mtimeMs ||
      final.ctimeMs !== info.ctimeMs
    )
      throw new LocalMediaError("local-file-changed");
    return { path: actual, bytes };
  } finally {
    closeSync(fd);
  }
}
function digest(bytes: Uint8Array): string {
  return createHash("sha256").update(bytes).digest("hex");
}
export function mediaBytes(root: string, item: LocalMedia): Buffer {
  try {
    const result = readChecked(root, item.filePath);
    if (result.path !== item.filePath || digest(result.bytes) !== item.digest)
      throw new LocalMediaError("local-file-changed");
    return result.bytes;
  } catch (error) {
    if (error instanceof LocalMediaError) throw error;
    throw new LocalMediaError("local-file-unavailable");
  }
}
function nodeText(node: MediaNode): string {
  if (node.type === "image") return node.alt ?? "";
  if (node.value !== undefined) return node.value;
  return (node.children ?? [])
    .map((child) => nodeText(child as MediaNode))
    .join("");
}
export function mediaEdit(
  node: MediaNode,
  root: string,
): MediaEdit | undefined {
  const start = node.position?.start.offset,
    end = node.position?.end.offset;
  if (
    (node.type !== "link" && node.type !== "image") ||
    !node.url ||
    start === undefined ||
    end === undefined
  )
    return;
  const path = localPath(node.url, root);
  if (!path) return;
  try {
    const file = readChecked(root, path);
    const fileName = Array.from(basename(file.path), (char) => {
      const code = char.codePointAt(0) ?? 0;
      return code < 32 || code === 127 ? "_" : char;
    }).join("");
    const photo = isPhoto(fileName, file.bytes.length);
    return {
      start,
      end,
      replacement: nodeText(node) || fileName,
      media: {
        kind: photo ? "photo" : "document",
        filePath: file.path,
        fileName,
        digest: digest(file.bytes),
      },
    };
  } catch {
    return;
  }
}
function isPhoto(fileName: string, size: number): boolean {
  return (
    [".png", ".jpg", ".jpeg"].includes(extname(fileName).toLowerCase()) &&
    size <= MAX_PHOTO
  );
}
