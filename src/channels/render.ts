import { fromMarkdown } from "mdast-util-from-markdown";
import { gfmFromMarkdown } from "mdast-util-gfm";
import { gfm } from "micromark-extension-gfm";
import { mediaEdit } from "./render-media.js";
import type { MediaEdit, MediaNode } from "./render-media.js";
import { regularChunks } from "./render-regular.js";
import type { TelegramMessageFormat } from "./types.js";

export interface RenderChunk {
  kind: "message" | "file" | "photo" | "document";
  text: string;
  plain: string;
  style?: "html" | undefined;
  filePath?: string | undefined;
  fileName?: string | undefined;
  digest?: string | undefined;
  workspace?: string | undefined;
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
  alt?: string | null | undefined;
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

function collect(
  node: Node,
  spans: Span[],
  leaves: Node[],
  links: MediaNode[],
): void {
  const start = node.position?.start.offset,
    end = node.position?.end.offset;
  if (node.type === "html" && start !== undefined && end !== undefined)
    spans.push({ start, end });
  if (isLeaf(node)) leaves.push(node);
  if (node.type === "link" || node.type === "image") links.push(node);
  for (const child of node.children ?? [])
    collect(child as Node, spans, leaves, links);
}
function isLeaf(node: Node): boolean {
  return (
    node.value !== undefined || node.type === "break" || node.type === "image"
  );
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
  edits: readonly MediaEdit[] = [],
): string {
  const changes = [
    ...hidden.map((span) => ({ ...span, replacement: "" })),
    ...edits,
  ].sort((a, b) => a.start - b.start);
  let result = "",
    cursor = start;
  for (const span of changes) {
    if (span.end <= cursor || span.start >= end) continue;
    result += source.slice(cursor, Math.max(cursor, span.start));
    if (span.start >= start && span.end <= end) result += span.replacement;
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
  return nonValueText(node);
}
function nonValueText(node: Node): string {
  if (node.type === "break") return "\n";
  if (node.type === "image") return node.alt ?? "";
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
  workspace?: string,
  format: TelegramMessageFormat = "rich",
): RenderResult {
  const tree = fromMarkdown(source, {
    extensions: [gfm()],
    mdastExtensions: [gfmFromMarkdown()],
  });
  const html: Span[] = [];
  const links: MediaNode[] = [];
  const blocks = tree.children.map((node) => {
    const leaves: Node[] = [];
    collect(node, html, leaves, links);
    return { node, leaves };
  });
  const filtered = hiddenSpans(source, html, hiddenTags);
  const edits = workspace ? localEdits(links, workspace, filtered.spans) : [];
  const chunks =
    format === "regular"
      ? regularChunks(
          blocks.map(({ node }) => node),
          source,
          filtered.spans,
          new Set(edits.map((edit) => edit.start)),
          (start, end) =>
            visibleSlice(source, start, end, filtered.spans, edits),
        )
      : renderBlocks(blocks, source, filtered.spans, edits);
  if (workspace)
    for (const edit of edits)
      chunks.push({
        ...edit.media,
        workspace,
        text: edit.replacement,
        plain: edit.replacement,
      });
  return {
    chunks,
    visibleText: visibleSlice(
      source,
      0,
      source.length,
      filtered.spans,
      edits,
    ).trim(),
    unclosedTag: filtered.unclosed,
  };
}
function renderBlocks(
  blocks: { node: Node; leaves: Node[] }[],
  source: string,
  hidden: readonly Span[],
  edits: readonly MediaEdit[],
): RenderChunk[] {
  const chunks: RenderChunk[] = [];
  let buffer = "",
    plain = "",
    count = 0;
  for (const { node, leaves } of blocks) {
    const start = node.position?.start.offset,
      end = node.position?.end.offset;
    if (start === undefined || end === undefined) continue;
    const block = visibleSlice(source, start, end, hidden, edits);
    if (!block.trim()) continue;
    if (shouldFlush(buffer, block, count)) {
      chunks.push({ kind: "message", text: buffer, plain });
      buffer = "";
      plain = "";
      count = 0;
    }
    const blockPlain = leaves
      .map((leaf) => plainNode(leaf, source, hidden))
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
  return chunks;
}
function localEdits(
  nodes: MediaNode[],
  workspace: string,
  hidden: readonly Span[],
): MediaEdit[] {
  const edits: MediaEdit[] = [];
  for (const node of nodes) {
    if (edits.length >= 10) break;
    const start = node.position?.start.offset,
      end = node.position?.end.offset;
    if (
      start === undefined ||
      end === undefined ||
      hidden.some((span) => span.start < end && span.end > start)
    )
      continue;
    const edit = mediaEdit(node, workspace);
    if (edit) edits.push(edit);
  }
  return edits;
}
