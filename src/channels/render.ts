import { fromMarkdown } from "mdast-util-from-markdown";
import { gfmFromMarkdown } from "mdast-util-gfm";
import { gfm } from "micromark-extension-gfm";

export interface RenderChunk {
  kind: "message" | "file";
  text: string;
  plain: string;
}
export interface RenderResult {
  chunks: RenderChunk[];
  visibleText: string;
  unclosedTag: boolean;
}
interface Span {
  start: number;
  end: number;
}
interface Node {
  type: string;
  value?: string | undefined;
  position?:
    | {
        start: { offset?: number | undefined };
        end: { offset?: number | undefined };
      }
    | undefined;
  children?: unknown[] | undefined;
}
interface HiddenState {
  begin: number;
  stack: string[];
  hidden: Span[];
}
function applyTag(
  state: HiddenState,
  name: string,
  tag: string,
  offset: number,
): void {
  if (tag.startsWith("</")) {
    if (state.stack.at(-1) !== name) return;
    state.stack.pop();
    if (!state.stack.length)
      state.hidden.push({ start: state.begin, end: offset + tag.length });
    return;
  }
  if (tag.endsWith("/>")) {
    if (!state.stack.length)
      state.hidden.push({ start: offset, end: offset + tag.length });
    return;
  }
  if (!state.stack.length) state.begin = offset;
  state.stack.push(name);
}

function collect(node: Node, spans: Span[], leaves: Node[]): void {
  const start = node.position?.start.offset,
    end = node.position?.end.offset;
  if (node.type === "html" && start !== undefined && end !== undefined)
    spans.push({ start, end });
  if (node.value !== undefined || node.type === "break") leaves.push(node);
  for (const child of node.children ?? [])
    collect(child as Node, spans, leaves);
}
function fenceSpans(source: string, node: Span): Span[] {
  const spans: Span[] = [];
  let offset = node.start,
    start: number | undefined,
    marker = "";
  for (const line of source.slice(node.start, node.end).split(/(?<=\n)/)) {
    const match = /^ {0,3}(`{3,}|~{3,})/.exec(line);
    if (match?.[1]) {
      if (start === undefined) {
        start = offset;
        marker = match[1];
      } else if (match[1].startsWith(marker)) {
        spans.push({ start, end: offset + line.length });
        start = undefined;
      }
    }
    offset += line.length;
  }
  if (start !== undefined) spans.push({ start, end: node.end });
  return spans;
}
function hiddenSpans(
  source: string,
  nodes: readonly Span[],
  tags: readonly string[],
): { spans: Span[]; unclosed: boolean } {
  const state: HiddenState = { hidden: [], stack: [], begin: 0 };
  const names = new Set(tags.map((tag) => tag.toLowerCase()));
  for (const node of nodes) {
    const text = source.slice(node.start, node.end);
    const fences = fenceSpans(source, node);
    for (const match of text.matchAll(
      /<\/?([a-zA-Z][\w-]*)(?:\s[^<>]*?)?\s*\/?>/g,
    )) {
      const name = match[1]?.toLowerCase();
      if (!name || !names.has(name)) continue;
      const offset = node.start + match.index;
      if (fences.some((fence) => offset >= fence.start && offset < fence.end))
        continue;
      applyTag(state, name, match[0], offset);
    }
  }
  if (state.stack.length)
    state.hidden.push({ start: state.begin, end: source.length });
  return { spans: state.hidden, unclosed: state.stack.length > 0 };
}
function visibleSlice(
  source: string,
  start: number,
  end: number,
  hidden: readonly Span[],
): string {
  let result = "",
    cursor = start;
  for (const span of hidden) {
    if (span.end <= cursor || span.start >= end) continue;
    result += source.slice(cursor, Math.max(cursor, span.start));
    cursor = Math.min(end, Math.max(cursor, span.end));
  }
  return result + source.slice(cursor, end);
}
function plainNode(
  node: Node,
  source: string,
  hidden: readonly Span[],
): string {
  const start = node.position?.start.offset,
    end = node.position?.end.offset;
  if (
    start !== undefined &&
    end !== undefined &&
    hidden.some((span) => span.start <= start && span.end >= end)
  )
    return "";
  if (node.value !== undefined) {
    if (
      start !== undefined &&
      end !== undefined &&
      hidden.some((span) => span.start < end && span.end > start)
    )
      return visibleSlice(source, start, end, hidden);
    return node.value;
  }
  if (node.type === "break") return "\n";
  return "";
}
function shouldFlush(buffer: string, block: string, count: number): boolean {
  return (
    Boolean(buffer) &&
    (buffer.length + block.length + 2 > 32768 || count === 500)
  );
}
export function renderOutgoing(
  source: string,
  hiddenTags: readonly string[] = ["internal-note"],
): RenderResult {
  const tree = fromMarkdown(source, {
    extensions: [gfm()],
    mdastExtensions: [gfmFromMarkdown()],
  });
  const html: Span[] = [];
  const blocks = tree.children.map((node) => {
    const leaves: Node[] = [];
    collect(node, html, leaves);
    return { node, leaves };
  });
  const filtered = hiddenSpans(source, html, hiddenTags);
  const chunks: RenderChunk[] = [];
  let buffer = "",
    plain = "",
    count = 0;
  for (const { node, leaves } of blocks) {
    const start = node.position?.start.offset,
      end = node.position?.end.offset;
    if (start === undefined || end === undefined) continue;
    const block = visibleSlice(source, start, end, filtered.spans);
    if (!block.trim()) continue;
    if (shouldFlush(buffer, block, count)) {
      chunks.push({ kind: "message", text: buffer, plain });
      buffer = "";
      plain = "";
      count = 0;
    }
    const blockPlain = leaves
      .map((leaf) => plainNode(leaf, source, filtered.spans))
      .join("");
    if (block.length > 32768) {
      chunks.push({ kind: "file", text: block, plain: blockPlain });
      continue;
    }
    buffer += (buffer ? "\n\n" : "") + block;
    plain += (plain ? "\n\n" : "") + blockPlain;
    count++;
  }
  if (buffer) chunks.push({ kind: "message", text: buffer, plain });
  return {
    chunks,
    visibleText: visibleSlice(source, 0, source.length, filtered.spans).trim(),
    unclosedTag: filtered.unclosed,
  };
}
