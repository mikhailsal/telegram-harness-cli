import { createHash } from "node:crypto";
import { createWriteStream, unlinkSync } from "node:fs";
import { mkdir, rename, rm, stat } from "node:fs/promises";
import { basename, dirname, join } from "node:path";
import { Readable, Transform } from "node:stream";
import { pipeline } from "node:stream/promises";
import type { Store } from "../store/index.js";
import type { ChannelAttachment } from "./types.js";
import type { TelegramChannel } from "./telegram.js";

const LIMIT = 20 * 1024 * 1024;
export function safeFileName(name: string): string {
  let result = basename(name.replaceAll("\\", "/"))
    .split("")
    .map((char) => {
      const code = char.charCodeAt(0);
      return code < 32 || code === 127 || char === "/" ? "_" : char;
    })
    .join("")
    .replace(/^\.+$/, "file");
  if (!result) result = "file";
  while (Buffer.byteLength(result) > 100)
    result = Array.from(result).slice(0, -1).join("");
  return result;
}
export class AttachmentManager {
  private readonly channel: TelegramChannel;
  private readonly store: Store;
  private readonly directory: string;
  private readonly token: string;
  private readonly root: string;
  private readonly fetcher: typeof fetch;
  constructor(
    channel: TelegramChannel,
    store: Store,
    directory: string,
    token: string,
    root = "https://api.telegram.org",
    fetcher: typeof fetch = fetch,
  ) {
    this.channel = channel;
    this.store = store;
    this.directory = directory;
    this.token = token;
    this.root = root;
    this.fetcher = fetcher;
  }
  async download(
    updateId: number,
    file: ChannelAttachment,
    date: Date,
  ): Promise<string> {
    if (file.fileSize !== undefined && file.fileSize > LIMIT)
      throw new Error("attachment-too-large");
    const old = this.store.getAttachment(updateId, file.fileUniqueId);
    if (
      old &&
      (await stat(old.path).then(
        () => true,
        () => false,
      ))
    )
      return old.path;
    const key = createHash("sha256")
      .update(`${String(updateId)}:${file.fileUniqueId}`)
      .digest("hex")
      .slice(0, 16);
    const folder = join(
      this.directory,
      "files",
      date.toISOString().slice(0, 10),
    );
    const name = safeFileName(
      file.fileName ??
        `${file.kind}${file.kind === "photo" ? ".jpg" : file.kind === "voice" ? ".ogg" : ""}`,
    );
    let shortened = name;
    while (Buffer.byteLength(shortened) > 83)
      shortened = Array.from(shortened).slice(0, -1).join("");
    const path = old?.path ?? join(folder, `${key}-${shortened}`);
    await mkdir(dirname(path), { recursive: true });
    await this.downloadFile(file, path);
    this.store.putAttachment({
      updateId,
      fileUniqueId: file.fileUniqueId,
      fileKey: key,
      path,
      createdAt: date.toISOString(),
      turnId: null,
    });
    return path;
  }
  private async downloadFile(
    file: ChannelAttachment,
    path: string,
  ): Promise<void> {
    let last: unknown;
    for (let attempt = 0; attempt < 3; attempt++) {
      try {
        const remote = await this.channel.bot.api.getFile(file.fileId);
        if (!remote.file_path) throw new Error("missing-file-path");
        const response = await this.fetcher(
          `${this.root}/file/bot${this.token}/${remote.file_path}`,
          { signal: this.channel.downloadSignal() },
        );
        if (!response.ok || !response.body) throw new Error("download-failed");
        let size = 0;
        const limit = new Transform({
          transform(chunk: Buffer, _encoding, callback) {
            size += chunk.length;
            callback(
              size > LIMIT ? new Error("attachment-too-large") : null,
              chunk,
            );
          },
        });
        await pipeline(
          Readable.fromWeb(
            response.body as unknown as Parameters<typeof Readable.fromWeb>[0],
          ),
          limit,
          createWriteStream(`${path}.part`, { mode: 0o600 }),
        );
        await rename(`${path}.part`, path);
        return;
      } catch (error) {
        last = error;
        await rm(`${path}.part`, { force: true });
      }
    }
    throw last;
  }
  retainSince(cutoff: Date): Promise<void> {
    for (const file of this.store.listAttachments()) {
      if (file.createdAt >= cutoff.toISOString()) continue;
      try {
        this.store.removeUnretainedAttachment(
          file.updateId,
          file.fileUniqueId,
          file.path,
          () => {
            try {
              unlinkSync(file.path);
            } catch (error) {
              if (!(
                error instanceof Error &&
                "code" in error &&
                error.code === "ENOENT"
              ))
                throw error;
            }
          },
        );
      } catch {
        /* Keep the row so the next retention pass retries. */
      }
    }
    return Promise.resolve();
  }
}
