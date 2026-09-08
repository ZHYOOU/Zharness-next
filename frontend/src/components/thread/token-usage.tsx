import type { Message } from "@langchain/langgraph-sdk";
import { Coins } from "lucide-react";

import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import {
  accumulateTokenUsage,
  formatTokenCount,
  getMessageTokenUsage,
} from "@/lib/token-usage";

export function TokenUsageIndicator({ messages }: { messages: Message[] }) {
  const usage = accumulateTokenUsage(messages);

  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger asChild>
          <div
            className="text-muted-foreground flex h-8 items-center gap-1.5 rounded-full border bg-white/80 px-2.5 text-xs tabular-nums"
            tabIndex={0}
            aria-label={
              usage ? `Token 用量：${usage.totalTokens}` : "暂无 Token 用量"
            }
          >
            <Coins className="size-3.5" />
            <span>{usage ? formatTokenCount(usage.totalTokens) : "—"}</span>
          </div>
        </TooltipTrigger>
        <TooltipContent
          side="bottom"
          className="space-y-1"
        >
          {usage ? (
            <>
              <p>输入：{formatTokenCount(usage.inputTokens)}</p>
              <p>输出：{formatTokenCount(usage.outputTokens)}</p>
              <p>总计：{formatTokenCount(usage.totalTokens)}</p>
            </>
          ) : (
            <p>模型返回用量后将在这里显示。</p>
          )}
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}

export function MessageTokenUsage({ message }: { message: Message }) {
  const usage = getMessageTokenUsage(message);
  if (!usage) return null;

  return (
    <div className="text-muted-foreground flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] tabular-nums">
      <span className="inline-flex items-center gap-1 font-medium">
        <Coins className="size-3" />
        Tokens
      </span>
      <span>输入 {formatTokenCount(usage.inputTokens)}</span>
      <span>输出 {formatTokenCount(usage.outputTokens)}</span>
      <span className="font-medium">
        总计 {formatTokenCount(usage.totalTokens)}
      </span>
    </div>
  );
}
