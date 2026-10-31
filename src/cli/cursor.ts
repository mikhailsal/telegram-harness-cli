import {
  createCipheriv,
  createDecipheriv,
  createHash,
  randomBytes,
} from "node:crypto";
import { usage } from "./errors.js";

function key(directory: string): Buffer {
  return createHash("sha256")
    .update(`thc-history-cursor\0${directory}`)
    .digest();
}
export function encodeCursor(value: string, directory: string): string {
  const iv = randomBytes(12);
  const cipher = createCipheriv("aes-256-gcm", key(directory), iv);
  const body = Buffer.concat([cipher.update(value, "utf8"), cipher.final()]);
  return Buffer.concat([iv, cipher.getAuthTag(), body]).toString("base64url");
}
export function decodeCursor(value: string, directory: string): string {
  try {
    const bytes = Buffer.from(value, "base64url");
    if (bytes.length < 29) throw new Error("short cursor");
    const decipher = createDecipheriv(
      "aes-256-gcm",
      key(directory),
      bytes.subarray(0, 12),
    );
    decipher.setAuthTag(bytes.subarray(12, 28));
    return Buffer.concat([
      decipher.update(bytes.subarray(28)),
      decipher.final(),
    ]).toString("utf8");
  } catch {
    throw usage("Invalid continuation token");
  }
}
