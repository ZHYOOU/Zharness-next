"use client";

import { useCallback, useEffect, useState } from "react";
import { useQueryState } from "nuqs";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { DEFAULT_API_URL, resolveApiUrl } from "@/lib/config";
import { getApiKey } from "@/lib/api-key";

const categories: Record<string, string> = {
  preference: "偏好",
  correction: "纠正",
  context: "背景",
  goal: "目标",
  behavior: "行为",
  identity: "身份",
  constraint: "约束",
  decision: "决策",
  other: "其他",
};
type Fact = {
  id: string;
  content: string;
  category: string;
  confidence: number;
  source_type: string;
  revision: number;
};
type Snapshot = {
  facts: Fact[];
  profile: {
    work_context: string;
    personal_context: string;
    top_of_mind: string;
  } | null;
};
type Status = {
  enabled: boolean;
  extraction_enabled: boolean;
  injection_enabled: boolean;
  max_facts: number;
};

export function MemorySettings() {
  const [apiUrl] = useQueryState("apiUrl");
  const [authScheme] = useQueryState("authScheme");
  const base = resolveApiUrl(
    apiUrl || process.env.NEXT_PUBLIC_API_URL || DEFAULT_API_URL,
  );
  return (
    <MemoryPanel
      key={`${base}:${authScheme}`}
      base={base}
      authScheme={authScheme}
    />
  );
}

function MemoryPanel({
  base,
  authScheme,
}: {
  base: string;
  authScheme: string | null;
}) {
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [status, setStatus] = useState<Status | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState("");
  const [editing, setEditing] = useState<Fact | null>(null);
  const [content, setContent] = useState("");
  const [category, setCategory] = useState("context");
  const [deleting, setDeleting] = useState<string | null>(null);

  const request = useCallback(
    async (path: string, init?: RequestInit) => {
      const headers = new Headers(init?.headers);
      if (init?.body) headers.set("Content-Type", "application/json");
      const key = getApiKey();
      if (key) headers.set("X-Api-Key", key);
      const scheme = authScheme || process.env.NEXT_PUBLIC_AUTH_SCHEME;
      if (scheme) headers.set("X-Auth-Scheme", scheme);
      const response = await fetch(`${base.replace(/\/$/, "")}/memory${path}`, {
        ...init,
        headers,
        cache: "no-store",
      });
      const data = await response.json().catch(() => null);
      if (!response.ok)
        throw new Error(
          data?.error || `记忆服务请求失败（${response.status}）`,
        );
      if (!data) throw new Error("记忆服务返回了无效数据");
      return data;
    },
    [base, authScheme],
  );

  const reload = useCallback(
    async (signal?: AbortSignal) => {
      const [nextStatus, nextSnapshot] = await Promise.all([
        request("/status", { signal }),
        request("", { signal }),
      ]);
      setStatus(nextStatus);
      setSnapshot(nextSnapshot);
    },
    [request],
  );

  useEffect(() => {
    const controller = new AbortController();
    Promise.all([
      request("/status", { signal: controller.signal }),
      request("", { signal: controller.signal }),
    ])
      .then(([nextStatus, nextSnapshot]) => {
        if (controller.signal.aborted) return;
        setStatus(nextStatus);
        setSnapshot(nextSnapshot);
      })
      .catch((e: Error) => {
        if (!controller.signal.aborted) setError(e.message);
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [request]);

  function reset() {
    setEditing(null);
    setContent("");
    setCategory("context");
  }

  async function mutate(path: string, init: RequestInit) {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await request(path, init);
      reset();
      setDeleting(null);
      setNotice("已保存到后端");
      try {
        await reload();
      } catch {
        setError("操作已保存，但刷新失败，请重新加载列表。");
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "操作失败，请重试");
    } finally {
      setBusy(false);
    }
  }

  const facts =
    snapshot?.facts.filter(
      (fact) =>
        (!filter || fact.category === filter) &&
        fact.content.toLowerCase().includes(query.trim().toLowerCase()),
    ) || [];

  return (
    <div className="space-y-6">
      <div>
        <h3 className="text-xl font-semibold">记忆</h3>
        <p className="text-muted-foreground mt-2 text-sm">
          管理跨对话使用的独立记忆，以及 Agent 自动整理的用户画像。
        </p>
      </div>
      {error && (
        <div
          role="alert"
          className="text-destructive rounded-xl border p-4"
        >
          {error}
          <Button
            variant="outline"
            className="ml-3"
            disabled={busy || loading}
            onClick={() => {
              setError("");
              setLoading(true);
              reload()
                .catch((e: Error) => setError(e.message))
                .finally(() => setLoading(false));
            }}
          >
            重新加载
          </Button>
        </div>
      )}
      {notice && (
        <p
          role="status"
          className="text-sm"
        >
          {notice}
        </p>
      )}
      {loading && <p role="status">正在加载记忆…</p>}
      {status && (
        <div className="rounded-xl border p-4 text-sm">
          <p>
            长期记忆：{status.enabled ? "已启用" : "已停用"} · 自动提取：
            {status.enabled && status.extraction_enabled ? "开启" : "关闭"} ·
            对话引用：
            {status.enabled && status.injection_enabled ? "开启" : "关闭"}
          </p>
          <p className="text-muted-foreground mt-2">
            启用状态由后端配置管理。停用不删除已保存的数据。容量{" "}
            {snapshot?.facts.length ?? 0} / {status.max_facts}{" "}
            条，超出后按后端策略淘汰。
          </p>
        </div>
      )}
      {snapshot && (
        <>
          <section className="space-y-3 rounded-xl border p-4">
            <h4 className="font-medium">用户画像</h4>
            <p className="text-muted-foreground text-sm">
              由对话自动生成，独立于下方记忆条目；删除条目不会同步清除画像。
            </p>
            {(
              [
                ["work_context", "工作背景"],
                ["personal_context", "个人背景"],
                ["top_of_mind", "近期关注"],
              ] as const
            ).map(([key, label]) => (
              <div key={key}>
                <p className="text-sm font-medium">{label}</p>
                <p className="text-muted-foreground text-sm whitespace-pre-wrap">
                  {snapshot.profile?.[key] || "暂无内容"}
                </p>
              </div>
            ))}
          </section>
          <form
            className="space-y-3 rounded-xl border p-4"
            onSubmit={(event) => {
              event.preventDefault();
              if (!content.trim() || busy) return;
              void mutate(editing ? `/${encodeURIComponent(editing.id)}` : "", {
                method: editing ? "PUT" : "POST",
                body: JSON.stringify({
                  content: content.trim(),
                  category,
                  confidence: editing?.confidence ?? 1,
                }),
              });
            }}
          >
            <h4 className="font-medium">{editing ? "编辑记忆" : "新增记忆"}</h4>
            <Textarea
              aria-label="记忆内容"
              placeholder="例如：我偏好简洁、直接的回答。"
              required
              maxLength={10000}
              value={content}
              disabled={busy}
              onChange={(event) => setContent(event.target.value)}
            />
            <select
              aria-label="记忆分类"
              className="rounded-md border p-2 text-sm"
              value={category}
              disabled={busy}
              onChange={(event) => setCategory(event.target.value)}
            >
              {Object.entries(categories).map(([value, label]) => (
                <option
                  key={value}
                  value={value}
                >
                  {label}
                </option>
              ))}
            </select>
            <div className="flex gap-2">
              <Button
                type="submit"
                disabled={busy || loading || !content.trim()}
              >
                {busy ? "正在保存…" : "保存记忆"}
              </Button>
              {editing && (
                <Button
                  type="button"
                  variant="outline"
                  disabled={busy}
                  onClick={reset}
                >
                  取消编辑
                </Button>
              )}
            </div>
          </form>
          <div className="flex gap-2">
            <Input
              aria-label="搜索记忆"
              placeholder="搜索记忆内容"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
            <select
              aria-label="筛选分类"
              className="rounded-md border p-2 text-sm"
              value={filter}
              onChange={(event) => setFilter(event.target.value)}
            >
              <option value="">全部分类</option>
              {Object.entries(categories).map(([value, label]) => (
                <option
                  key={value}
                  value={value}
                >
                  {label}
                </option>
              ))}
            </select>
          </div>
          <div className="space-y-3">
            {facts.length === 0 && (
              <p className="text-muted-foreground text-sm">
                {snapshot.facts.length
                  ? "没有匹配的记忆。"
                  : "暂无记忆，可手动新增或在对话中积累。"}
              </p>
            )}
            {facts.map((fact) => (
              <article
                key={fact.id}
                className="space-y-3 rounded-xl border p-4"
              >
                <p className="break-words whitespace-pre-wrap">
                  {fact.content}
                </p>
                <p className="text-muted-foreground text-xs">
                  {categories[fact.category] || fact.category} ·{" "}
                  {fact.source_type === "manual" ? "手动添加" : "对话提取"} ·
                  置信度 {Math.round(fact.confidence * 100)}% · 版本{" "}
                  {fact.revision}
                </p>
                <div className="flex flex-wrap items-center gap-2">
                  <Button
                    variant="outline"
                    disabled={busy}
                    onClick={() => {
                      setEditing(fact);
                      setContent(fact.content);
                      setCategory(fact.category);
                      setNotice("");
                    }}
                  >
                    编辑
                  </Button>
                  {deleting === fact.id ? (
                    <>
                      <span className="text-sm">确定删除这条记忆？</span>
                      <Button
                        variant="destructive"
                        disabled={busy}
                        onClick={() =>
                          void mutate(`/${encodeURIComponent(fact.id)}`, {
                            method: "DELETE",
                          })
                        }
                      >
                        确认删除
                      </Button>
                      <Button
                        variant="outline"
                        disabled={busy}
                        onClick={() => setDeleting(null)}
                      >
                        取消
                      </Button>
                    </>
                  ) : (
                    <Button
                      variant="outline"
                      disabled={busy}
                      onClick={() => setDeleting(fact.id)}
                    >
                      删除
                    </Button>
                  )}
                </div>
              </article>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
