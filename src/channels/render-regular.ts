import type { RenderChunk } from "./render.js";

interface Tag {
  open: string;
  close: string;
}
interface Piece {
  text: string;
  tags: Tag[];
}
interface Span {
  start: number;
  end: number;
}
export interface RegularNode {
  type: string;
  value?: string | undefined;
  url?: string | undefined;
  alt?: string | null | undefined;
  identifier?: string | undefined;
  ordered?: boolean | null | undefined;
  start?: number | null | undefined;
  children?: unknown[] | undefined;
  position?:
    | {
        start: { offset?: number | undefined };
        end: { offset?: number | undefined };
      }
    | undefined;
}
interface Context {
  source: string;
  hidden: readonly Span[];
  local: ReadonlySet<number>;
  pieces: Piece[];
  definitions: ReadonlyMap<string, string>;
}
function tag(name: string): Tag {
  return { open: `<${name}>`, close: `</${name}>` };
}
function escape(value: string): string {
  return value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}
function bounds(node: RegularNode): Span | undefined {
  const start = node.position?.start.offset,
    end = node.position?.end.offset;
  return start === undefined || end === undefined ? undefined : { start, end };
}
function covered(span: Span | undefined, hidden: readonly Span[]): boolean {
  return (
    !!span &&
    hidden.some((item) => item.start <= span.start && item.end >= span.end)
  );
}
function visible(node: RegularNode, ctx: Context): string {
  const span = bounds(node);
  if (!span) return node.value ?? "";
  if (
    !ctx.hidden.some((item) => item.start < span.end && item.end > span.start)
  )
    return node.value ?? "";
  let output = "",
    cursor = span.start;
  for (const item of ctx.hidden) {
    if (item.end <= cursor || item.start >= span.end) continue;
    output += ctx.source.slice(cursor, Math.max(cursor, item.start));
    cursor = Math.max(cursor, Math.min(span.end, item.end));
  }
  return output + ctx.source.slice(cursor, span.end);
}
function add(ctx: Context, text: string, tags: Tag[]): void {
  if (text) ctx.pieces.push({ text, tags });
}
function addText(ctx: Context, text: string, tags: Tag[]): void {
  const parts = text.split("||");
  if (parts.length < 3 || parts.length % 2 === 0) {
    add(ctx, text, tags);
    return;
  }
  parts.forEach((part, i) => {
    add(
      ctx,
      part,
      i % 2
        ? [...tags, { open: '<span class="tg-spoiler">', close: "</span>" }]
        : tags,
    );
  });
}
function safeLink(url: string | undefined): string | undefined {
  if (!url || !/^(?:https?:\/\/|mailto:|tg:\/\/)/iu.test(url)) return;
  return escape(url);
}
function children(node: RegularNode): RegularNode[] {
  return (node.children ?? []) as RegularNode[];
}
function inlineTags(node: RegularNode, tags: Tag[], ctx: Context): Tag[] {
  if (node.type === "inlineCode") return [tag("code")];
  if (node.type === "code") return [tag("pre"), tag("code")];
  if (
    node.type === "blockquote" &&
    tags.some((item) => item.open === "<blockquote>")
  )
    return tags;
  const name: Record<string, string> = {
    strong: "b",
    emphasis: "i",
    delete: "s",
    blockquote: "blockquote",
    heading: "b",
  };
  const element = name[node.type];
  if (element) return [...tags, tag(element)];
  return linkTags(node, tags, ctx);
}
function referenceKey(identifier: string): string {
  return identifier.replace(/\s+/gu, " ").trim().toLowerCase();
}
function linkTags(node: RegularNode, tags: Tag[], ctx: Context): Tag[] {
  if (
    ["link", "image", "linkReference", "imageReference"].includes(node.type) &&
    !ctx.local.has(bounds(node)?.start ?? -1)
  ) {
    const target = node.identifier
      ? ctx.definitions.get(referenceKey(node.identifier))
      : node.url;
    const url = safeLink(target);
    if (url)
      return [
        ...tags.filter((item) => item.open !== "<blockquote>"),
        { open: `<a href="${url}">`, close: "</a>" },
      ];
  }
  return tags;
}
function emitMissingReference(
  node: RegularNode,
  ctx: Context,
  tags: Tag[],
): boolean {
  if (
    (node.type === "linkReference" || node.type === "imageReference") &&
    node.identifier &&
    !ctx.definitions.has(referenceKey(node.identifier))
  ) {
    const span = bounds(node);
    if (span)
      add(
        ctx,
        visible(
          { ...node, value: ctx.source.slice(span.start, span.end) },
          ctx,
        ),
        tags,
      );
    return true;
  }
  return false;
}
function emitSpecialLeaf(
  node: RegularNode,
  ctx: Context,
  tags: Tag[],
): boolean {
  if (node.type === "image" || node.type === "imageReference") {
    add(ctx, node.alt ?? "image", tags);
    return true;
  }
  if (node.type === "break") {
    add(ctx, "\n", tags);
    return true;
  }
  return false;
}
function emit(node: RegularNode, ctx: Context, tags: Tag[] = []): void {
  if (covered(bounds(node), ctx.hidden)) return;
  if (emitMissingReference(node, ctx, tags)) return;
  const next = inlineTags(node, tags, ctx);
  if (node.type === "text") {
    addText(ctx, visible(node, ctx), next);
    return;
  }
  if (["inlineCode", "code", "html"].includes(node.type)) {
    add(ctx, visible(node, ctx), next);
    return;
  }
  if (emitSpecialLeaf(node, ctx, next)) return;
  if (node.type === "blockquote") {
    emitBlockquote(node, ctx, next);
    return;
  }
  if (node.type === "list") {
    emitList(node, ctx, next);
    return;
  }
  if (node.type === "table") {
    emitTable(node, ctx, next);
    return;
  }
  for (const child of children(node)) emit(child, ctx, next);
}
function emitBlockquote(node: RegularNode, ctx: Context, tags: Tag[]): void {
  let emitted = false;
  for (const child of children(node)) {
    const before = ctx.pieces.length;
    emit(child, ctx, tags);
    if (ctx.pieces.length === before) continue;
    if (emitted) ctx.pieces.splice(before, 0, { text: "\n\n", tags });
    emitted = true;
  }
}
function emitList(node: RegularNode, ctx: Context, tags: Tag[]): void {
  children(node).forEach((item, index) => {
    if (index) add(ctx, "\n", tags);
    add(
      ctx,
      node.ordered ? `${String((node.start ?? 1) + index)}. ` : "• ",
      tags,
    );
    children(item).forEach((child, i) => {
      if (i) add(ctx, "\n", tags);
      emit(child, ctx, tags);
    });
  });
}
function emitTable(node: RegularNode, ctx: Context, tags: Tag[]): void {
  children(node).forEach((row, i) => {
    if (i) add(ctx, "\n", tags);
    children(row).forEach((cell, j) => {
      if (j) add(ctx, " | ", tags);
      emit(cell, ctx, tags);
    });
  });
}
interface Output {
  html: string;
  plain: string;
  tags: Tag[];
  chunks: RenderChunk[];
}
function changeTags(out: Output, next: Tag[]): void {
  let shared = 0;
  while (
    shared < out.tags.length &&
    shared < next.length &&
    out.tags[shared]?.open === next[shared]?.open &&
    out.tags[shared]?.close === next[shared]?.close
  )
    shared++;
  for (let i = out.tags.length - 1; i >= shared; i--)
    out.html += out.tags[i]?.close ?? "";
  for (let i = shared; i < next.length; i++) out.html += next[i]?.open ?? "";
  out.tags = next;
}
function appendPart(out: Output, text: string, tags: Tag[]): void {
  if (!text) return;
  changeTags(out, tags);
  out.html += escape(text);
  out.plain += text;
}
function finishChunk(out: Output): void {
  if (!out.plain) return;
  changeTags(out, []);
  out.chunks.push({
    kind: "message",
    style: "html",
    text: out.html,
    plain: out.plain,
  });
  out.html = "";
  out.plain = "";
}
function splitPieces(pieces: Piece[]): RenderChunk[] {
  const out: Output = { html: "", plain: "", tags: [], chunks: [] };
  for (const piece of pieces) {
    let part = "";
    for (const char of piece.text) {
      if (out.plain.length + part.length + char.length > 4096) {
        appendPart(out, part, piece.tags);
        finishChunk(out);
        part = "";
      }
      part += char;
    }
    appendPart(out, part, piece.tags);
  }
  finishChunk(out);
  return out.chunks;
}
function definitions(
  nodes: RegularNode[],
  hidden: readonly Span[],
): Map<string, string> {
  const result = new Map<string, string>();
  for (const node of nodes) {
    const span = bounds(node);
    if (node.type !== "definition" || !node.identifier || !node.url || !span)
      continue;
    if (hidden.some((item) => item.start < span.end && item.end > span.start))
      continue;
    const key = referenceKey(node.identifier);
    if (!result.has(key)) result.set(key, node.url);
  }
  return result;
}
export function regularChunks(
  nodes: RegularNode[],
  source: string,
  hidden: readonly Span[],
  local: ReadonlySet<number>,
  slice: (start: number, end: number) => string,
): RenderChunk[] {
  const chunks: RenderChunk[] = [],
    ctx: Context = {
      source,
      hidden,
      local,
      pieces: [],
      definitions: definitions(nodes, hidden),
    };
  for (const node of nodes) {
    const span = bounds(node);
    if (node.type === "definition" || !span || covered(span, hidden)) continue;
    const raw = slice(span.start, span.end);
    if (!raw.trim()) continue;
    if (raw.length > 32768) {
      chunks.push(...splitPieces(ctx.pieces));
      ctx.pieces = [];
      chunks.push({ kind: "file", text: raw, plain: raw });
      continue;
    }
    if (ctx.pieces.length) add(ctx, "\n\n", []);
    emit(node, ctx);
  }
  chunks.push(...splitPieces(ctx.pieces));
  return chunks;
}
