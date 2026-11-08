import type { RichBlock, RichText } from "grammy/types";
import type { ChannelAttachment } from "./types.js";

function unknownReadable(value: unknown): string {
  if (typeof value === "string") return value;
  if (Array.isArray(value))
    return value.map(unknownReadable).filter(Boolean).join(" ");
  if (!value || typeof value !== "object") return "";
  const item = value as Record<string, unknown>;
  return [
    "text",
    "caption",
    "summary",
    "title",
    "description",
    "alternative_text",
    "expression",
    "blocks",
    "items",
    "cells",
  ]
    .map((key) => unknownReadable(item[key]))
    .filter(Boolean)
    .join(" ");
}
function unknownFiles(value: unknown): ChannelAttachment[] {
  if (Array.isArray(value)) return value.flatMap(unknownFiles);
  if (!value || typeof value !== "object") return [];
  const item = value as Record<string, unknown>;
  if (
    typeof item.file_id === "string" &&
    typeof item.file_unique_id === "string"
  ) {
    const attachment: ChannelAttachment = {
      kind: "document",
      fileId: item.file_id,
      fileUniqueId: item.file_unique_id,
    };
    if (typeof item.file_name === "string")
      attachment.fileName = item.file_name;
    if (typeof item.file_size === "number")
      attachment.fileSize = item.file_size;
    return [attachment];
  }
  return Object.entries(item)
    .filter(([key]) => !["blocks", "items"].includes(key))
    .flatMap(([, child]) => unknownFiles(child));
}
function richText(text: RichText): string {
  if (typeof text === "string") return text;
  if (Array.isArray(text)) return text.map(richText).join("");
  const value = "text" in text ? richText(text.text) : "";
  return formatRichText(text, value);
}
function formatRichText(
  text: Exclude<RichText, string | RichText[]>,
  value: string,
): string {
  switch (text.type) {
    case "bold":
      return `**${value}**`;
    case "italic":
      return `_${value}_`;
    case "strikethrough":
      return `~~${value}~~`;
    case "code":
      return `\`${value}\``;
    case "url":
      return "url" in text ? `[${value}](${text.url})` : value;
    default:
      return value || unknownReadable(text);
  }
}
function richBlock(block: RichBlock): string {
  if (block.type === "heading")
    return `${"#".repeat(block.size)} ${richText(block.text)}`;
  if (block.type === "pre")
    return `\`\`\`${block.language ?? ""}\n${richText(block.text)}\n\`\`\``;
  if (block.type === "divider") return "---";
  if (block.type === "table")
    return block.cells
      .map((row) =>
        row.map((cell) => (cell.text ? richText(cell.text) : "")).join(" | "),
      )
      .join("\n");
  return complexRichBlock(block);
}
function complexRichBlock(block: RichBlock): string {
  if (block.type === "details")
    return `${richText(block.summary)}\n\n${block.blocks.map(richBlock).join("\n\n")}`;
  if (block.type === "list")
    return block.items
      .map(
        (item) =>
          `${item.value === undefined ? "-" : `${String(item.value)}.`} ${item.has_checkbox ? (item.is_checked ? "[x] " : "[ ] ") : ""}${item.blocks.map(richBlock).join(" ")}`,
      )
      .join("\n");
  if (block.type === "blockquote")
    return block.blocks
      .map(richBlock)
      .join("\n\n")
      .split("\n")
      .map((line) => `> ${line}`)
      .join("\n");
  if ("text" in block) return richText(block.text);
  if ("blocks" in block) return block.blocks.map(richBlock).join("\n\n");
  if ("caption" in block)
    return richText(
      typeof block.caption === "object" &&
        !Array.isArray(block.caption) &&
        "text" in block.caption
        ? block.caption.text
        : block.caption,
    );
  return unknownReadable(block);
}
export function richBlocksToMarkdown(blocks: readonly RichBlock[]): string {
  return blocks.map(richBlock).filter(Boolean).join("\n\n");
}

export function richAttachments(
  blocks: readonly RichBlock[],
): ChannelAttachment[] {
  const found: ChannelAttachment[] = [];
  for (const block of blocks) {
    if ("blocks" in block) found.push(...richAttachments(block.blocks));
    if (block.type === "list")
      for (const item of block.items)
        found.push(...richAttachments(item.blocks));
    found.push(...blockAttachments(block));
  }
  return found;
}
function blockAttachments(block: RichBlock): ChannelAttachment[] {
  const kind = block.type === "voice_note" ? "voice" : block.type;
  const file = blockFile(block);
  if (!file) {
    return unknownFiles(block);
  }
  if (!isAttachmentKind(kind)) return [];
  const entry: ChannelAttachment = {
    kind,
    fileId: file.file_id,
    fileUniqueId: file.file_unique_id,
  };
  if (file.file_size !== undefined) entry.fileSize = file.file_size;
  if ("file_name" in file && typeof file.file_name === "string")
    entry.fileName = file.file_name;
  return [entry];
}
function isAttachmentKind(kind: string): kind is ChannelAttachment["kind"] {
  return ["photo", "document", "audio", "video", "voice"].includes(kind);
}
function blockFile(block: RichBlock) {
  switch (block.type) {
    case "photo":
      return block.photo.at(-1);
    case "document":
      return block.document;
    case "audio":
      return block.audio;
    case "video":
      return block.video;
    case "voice_note":
      return block.voice_note;
    default:
      return undefined;
  }
}
