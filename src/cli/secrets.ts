import { redact } from "../core/config.js";

export function redactSecrets(value: string, env: NodeJS.ProcessEnv): string {
  let safe = redact(value).replace(
    /(?:https?:\/\/[^\s]*)?\b(?:bot)?\d+:[A-Za-z0-9_-]+/g,
    "<redacted>",
  );
  for (const candidate of Object.values(env)) {
    if (candidate && /^\d+:[A-Za-z0-9_-]+$/.test(candidate))
      safe = safe.replaceAll(candidate, "<redacted>");
  }
  return safe;
}
