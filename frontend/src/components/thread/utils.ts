import type { Message } from "@langchain/langgraph-sdk";

/**
 * Extracts a string summary from a message's content, supporting multimodal (text, image, file, etc.).
 * - If text is present, returns the joined text.
 * - If not, returns a label for the first non-text modality (e.g., 'Image', 'Other').
 * - If unknown, returns 'Multimodal message'.
 *
 * 从消息内容中提取字符串摘要，并支持文本、图片和文件等多模态内容。
 * - 存在文本时返回拼接后的文本。
 * - 否则返回第一个非文本模态的标签，例如“图片”或“其他”。
 * - 无法识别时返回“多模态消息”。
 */
export function getContentString(content: Message["content"]): string {
  if (typeof content === "string") return content;
  const texts = content
    .filter((c): c is { type: "text"; text: string } => c.type === "text")
    .map((c) => c.text);
  return texts.join(" ");
}

/**
 * Returns whether a message is a hidden injected reminder (e.g. the dynamic
 * current-date system reminder) that should not be rendered in the UI.
 *
 * 返回消息是否为不应在界面渲染的隐藏注入提醒（例如动态当前日期系统提醒）。
 */
export function isHiddenFromUi(message: Message): boolean {
  return Boolean(message.additional_kwargs?.hide_from_ui);
}
