import type { Message } from "@langchain/langgraph-sdk";

export interface TokenUsage {
  inputTokens: number;
  outputTokens: number;
  totalTokens: number;
}

type RawTokenUsage = {
  input_tokens?: unknown;
  output_tokens?: unknown;
  total_tokens?: unknown;
};

function nonNegativeNumber(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value) && value >= 0
    ? value
    : undefined;
}

export function normalizeTokenUsage(value: unknown): TokenUsage | null {
  if (!value || typeof value !== "object") return null;

  const usage = value as RawTokenUsage;
  const inputTokens = nonNegativeNumber(usage.input_tokens);
  const outputTokens = nonNegativeNumber(usage.output_tokens);
  const reportedTotal = nonNegativeNumber(usage.total_tokens);
  if (inputTokens === undefined || outputTokens === undefined) return null;

  return {
    inputTokens,
    outputTokens,
    totalTokens: reportedTotal ?? inputTokens + outputTokens,
  };
}

export function getMessageTokenUsage(message: Message): TokenUsage | null {
  if (message.type !== "ai") return null;

  const record = message as unknown as Record<string, unknown>;
  const additionalKwargs = message.additional_kwargs as
    Record<string, unknown> | undefined;
  return normalizeTokenUsage(
    record.usage_metadata ?? additionalKwargs?.usage_metadata,
  );
}

export function accumulateTokenUsage(messages: Message[]): TokenUsage | null {
  const total: TokenUsage = {
    inputTokens: 0,
    outputTokens: 0,
    totalTokens: 0,
  };
  const countedIds = new Set<string>();
  let found = false;

  for (const message of messages) {
    const usage = getMessageTokenUsage(message);
    if (!usage) continue;
    if (message.id) {
      if (countedIds.has(message.id)) continue;
      countedIds.add(message.id);
    }
    found = true;
    total.inputTokens += usage.inputTokens;
    total.outputTokens += usage.outputTokens;
    total.totalTokens += usage.totalTokens;
  }

  return found ? total : null;
}

export function formatTokenCount(count: number): string {
  if (count < 10_000) return new Intl.NumberFormat().format(count);
  return `${(count / 1000).toFixed(1)}K`;
}
