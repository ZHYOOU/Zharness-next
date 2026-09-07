import { v4 as uuidv4 } from "uuid";
import { Message, ToolMessage } from "@langchain/langgraph-sdk";

export const DO_NOT_RENDER_ID_PREFIX = "do-not-render-";

export function ensureToolCallsHaveResponses(messages: Message[]): Message[] {
  const newMessages: ToolMessage[] = [];

  messages.forEach((message, index) => {
    if (message.type !== "ai" || message.tool_calls?.length === 0) {
      // Ignore messages without AI tool calls. / 忽略不含 AI 工具调用的消息。
      return;
    }
    // Ensure tool calls are followed by a tool message. / 确保工具调用后紧跟工具消息。
    const followingMessage = messages[index + 1];
    if (followingMessage && followingMessage.type === "tool") {
      // Keep an existing tool response unchanged. / 保持已有工具响应不变。
      return;
    }

    // Add a tool response when the following message is not one. / 后续消息不是工具消息时补充工具响应。
    newMessages.push(
      ...(message.tool_calls?.map((tc) => ({
        type: "tool" as const,
        tool_call_id: tc.id ?? "",
        id: `${DO_NOT_RENDER_ID_PREFIX}${uuidv4()}`,
        name: tc.name,
        content: "Successfully handled tool call.",
      })) ?? []),
    );
  });

  return newMessages;
}
