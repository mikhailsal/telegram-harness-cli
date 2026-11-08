import { randomUUID } from "node:crypto";
import { mkdir, readFile, rename, unlink, writeFile } from "node:fs/promises";
import { dirname } from "node:path";
import type { ZodType } from "zod";

export function isMissing(error: unknown): boolean {
  return error instanceof Error && "code" in error && error.code === "ENOENT";
}

export async function atomicJson(path: string, value: unknown): Promise<void> {
  await mkdir(dirname(path), { recursive: true, mode: 0o700 });
  const temporary = `${path}.${randomUUID()}.tmp`;
  try {
    await writeFile(temporary, JSON.stringify(value), {
      mode: 0o600,
      flag: "wx",
    });
    await rename(temporary, path);
  } finally {
    await unlink(temporary).catch((error: unknown) => {
      if (!isMissing(error)) throw error;
    });
  }
}

export async function readJson<T>(
  path: string,
  schema: ZodType<T>,
  fallback: T,
): Promise<T> {
  try {
    const value: unknown = JSON.parse(await readFile(path, "utf8"));
    return schema.parse(value);
  } catch (error) {
    if (isMissing(error)) return fallback;
    throw error;
  }
}

export class StateFile<T> {
  private readonly path: string;
  private readonly schema: ZodType<T>;
  private readonly fallback: T;
  private queue: Promise<void> = Promise.resolve();
  constructor(path: string, schema: ZodType<T>, fallback: T) {
    this.path = path;
    this.schema = schema;
    this.fallback = fallback;
  }
  async read(): Promise<T> {
    await this.queue;
    return readJson(this.path, this.schema, this.fallback);
  }
  update(mutate: (current: T) => T): Promise<T> {
    const operation = this.queue.then(async () => {
      const next = this.schema.parse(
        mutate(await readJson(this.path, this.schema, this.fallback)),
      );
      await atomicJson(this.path, next);
      return next;
    });
    this.queue = operation.then(
      () => undefined,
      () => undefined,
    );
    return operation;
  }
}
