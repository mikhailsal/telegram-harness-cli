// Requests from the quality core: one JSON object per run, read from a file.

import { isAbsolute } from "node:path";

export const PROTOCOL = 1;

/** A request the helper cannot serve; the helper exits with status 2. */
export class RequestError extends Error {
  override name = "RequestError";
}

export type JsonObject = Readonly<Record<string, unknown>>;

export function isRecord(value: unknown): value is JsonObject {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isString(value: unknown): value is string {
  return typeof value === "string";
}

/** A field of a JSON object; undefined for anything else. */
export function field(value: unknown, key: string): unknown {
  return isRecord(value) ? value[key] : undefined;
}

/** The message of an error, or the value itself when something else was thrown. */
export function describe(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

/** Parse a request and check its protocol version. */
export function parseRequest(text: string): JsonObject {
  let value: unknown;
  try {
    value = JSON.parse(text);
  } catch (error) {
    throw new RequestError(`the request is not JSON: ${describe(error)}`);
  }
  if (!isRecord(value)) {
    throw new RequestError("the request is not a JSON object");
  }
  if (value.protocol !== PROTOCOL) {
    const found = JSON.stringify(value.protocol);
    throw new RequestError(`protocol ${found} is not ${String(PROTOCOL)}`);
  }
  return value;
}

function checkedPath(value: unknown, key: string): string {
  if (typeof value !== "string" || !isAbsolute(value)) {
    throw new RequestError(
      `"${key}" holds a value that is not an absolute path`,
    );
  }
  return value;
}

export function absolutePath(request: JsonObject, key: string): string {
  return checkedPath(request[key], key);
}

/** An absolute path, or null when the request says there is none. */
export function optionalPath(request: JsonObject, key: string): string | null {
  return request[key] === null ? null : absolutePath(request, key);
}

export function pathList(request: JsonObject, key: string): string[] {
  const value = request[key];
  if (!Array.isArray(value)) {
    throw new RequestError(`"${key}" is not a list of absolute paths`);
  }
  return value.map((item: unknown) => checkedPath(item, key));
}

/** A list of strings, or an empty list when the request omits it. */
export function optionalStrings(request: JsonObject, key: string): string[] {
  const value = request[key];
  if (value === undefined) {
    return [];
  }
  if (!Array.isArray(value) || !value.every(isString)) {
    throw new RequestError(`"${key}" is not a list of strings`);
  }
  return value;
}
