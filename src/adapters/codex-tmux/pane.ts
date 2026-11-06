export type PaneState =
  | { kind: "empty" }
  | { kind: "composer"; lines: string[] }
  | { kind: "busy" }
  | { kind: "unknown" };

export function parsePane(pane: string): PaneState {
  const lines = pane.split("\n").map((line) => line.replace(/\s+$/u, ""));
  const frame = currentFrame(lines);
  if (!frame) return { kind: "unknown" };
  const { start, footer } = frame;
  const previous = lines.slice(0, start).filter(Boolean).at(-1) ?? "";
  if (lines[start]?.startsWith("› /")) return { kind: "unknown" };
  if (isBusyStatus(previous)) return { kind: "busy" };
  return composerState(lines, start, footer);
}

function isBusyStatus(line: string): boolean {
  // Codex changes the heading while reasoning; the timer and key hint identify it.
  return /^[•◦] \S.* \((?:\d+h \d{2}m \d{2}s|\d+m \d{2}s|\d+s) • esc to interrupt\)(?: · .+)?$/u.test(
    line,
  );
}
function currentFrame(
  lines: string[],
): { start: number; footer: number } | undefined {
  const footer = lines.findLastIndex(
    (line) =>
      /^ {2}.+ · /u.test(line) &&
      !/^ {2}\? for shortcuts/u.test(line) &&
      !line.includes("enter select · esc back"),
  );
  if (
    footer < 0 ||
    lines
      .slice(footer + 1)
      .some((line) => line !== "" && !/^ {2}\? for shortcuts/u.test(line))
  )
    return undefined;
  const start = lines.findLastIndex(
    (line, index) => index < footer && line.startsWith("› "),
  );
  return start < 0 ? undefined : { start, footer };
}

function composerState(
  lines: string[],
  start: number,
  footer: number,
): PaneState {
  const composer = lines.slice(start, footer);
  while (composer.at(-1) === "") composer.pop();
  const first = composer[0]?.slice(2);
  if (first === undefined) return { kind: "unknown" };
  if (first === "Ask Codex to do anything" && composer.length === 1)
    return { kind: "empty" };
  if (composer.slice(1).some((line) => line !== "" && !line.startsWith("  ")))
    return { kind: "unknown" };
  return {
    kind: "composer",
    lines: [first, ...composer.slice(1).map((line) => line.slice(2))],
  };
}

export function isExpectedPaste(state: PaneState, text: string): boolean {
  if (state.kind !== "composer") return false;
  let count = 0;
  for (const character of text) if (character) count++;
  if (count > 1000)
    return (
      state.lines.length === 1 &&
      state.lines[0] === `[Pasted Content ${String(count)} chars]`
    );
  // Rendered wrapping and whitespace are ambiguous. Only accept an exact display.
  return state.lines.join("\n") === text;
}

export function isCleanBusyPane(pane: string): boolean {
  const lines = pane.split("\n").map((line) => line.trimEnd());
  const frame = currentFrame(lines);
  if (!frame || lines[frame.start] !== "› Ask Codex to do anything")
    return false;
  if (lines.slice(frame.start + 1, frame.footer).some(Boolean)) return false;
  const previous = lines.slice(0, frame.start).filter(Boolean).at(-1) ?? "";
  return isBusyStatus(previous);
}
