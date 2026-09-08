"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useQueryState } from "nuqs";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Switch } from "@/components/ui/switch";
import { DEFAULT_API_URL, resolveApiUrl } from "@/lib/config";
import { getApiKey } from "@/lib/api-key";

type Skill = {
  name: string;
  description: string;
  category: "public" | "user";
  enabled: boolean;
  license: string | null;
  allowed_tools: string[] | null;
};
type Detail = Skill & { content: string };

export function SkillSettings() {
  const [apiUrl] = useQueryState("apiUrl");
  const [authScheme] = useQueryState("authScheme");
  const base = resolveApiUrl(
    apiUrl || process.env.NEXT_PUBLIC_API_URL || DEFAULT_API_URL,
  );
  return (
    <SkillPanel
      key={`${base}:${authScheme}`}
      base={base}
      authScheme={authScheme}
    />
  );
}

function SkillPanel({
  base,
  authScheme,
}: {
  base: string;
  authScheme: string | null;
}) {
  const [skills, setSkills] = useState<Skill[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const lock = useRef(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [scope, setScope] = useState("all");
  const [query, setQuery] = useState("");
  const [detail, setDetail] = useState<Detail | null>(null);
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [content, setContent] = useState("");

  const request = useCallback(
    async (path: string, init?: RequestInit) => {
      const headers = new Headers(init?.headers);
      if (init?.body) headers.set("Content-Type", "application/json");
      const key = getApiKey();
      if (key) headers.set("X-Api-Key", key);
      const scheme = authScheme || process.env.NEXT_PUBLIC_AUTH_SCHEME;
      if (scheme) headers.set("X-Auth-Scheme", scheme);
      const response = await fetch(`${base.replace(/\/$/, "")}/skills${path}`, {
        ...init,
        headers,
        cache: "no-store",
      });
      const data = await response.json().catch(() => null);
      if (!response.ok)
        throw new Error(
          data?.error || `技能服务请求失败（${response.status}）`,
        );
      if (!data) throw new Error("技能服务返回了无效数据");
      return data;
    },
    [base, authScheme],
  );

  useEffect(() => {
    const controller = new AbortController();
    request("", { signal: controller.signal })
      .then((data) => {
        if (!controller.signal.aborted) setSkills(data.skills);
      })
      .catch((e: Error) => {
        if (!controller.signal.aborted) setError(e.message);
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [request]);

  async function perform(action: () => Promise<void>) {
    if (lock.current) return;
    lock.current = true;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await action();
    } catch (e) {
      setError(e instanceof Error ? e.message : "请求失败，请重试。");
    } finally {
      lock.current = false;
      setBusy(false);
    }
  }

  const visible = skills.filter(
    (skill) =>
      (scope === "all" || skill.category === scope) &&
      `${skill.name} ${skill.description}`
        .toLowerCase()
        .includes(query.trim().toLowerCase()),
  );
  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h3 className="text-xl font-semibold">技能</h3>
          <p className="text-muted-foreground mt-1.5 text-sm">
            管理 Agent 可发现和使用的技能。启停变更用于后续运行。
          </p>
        </div>
        <div className="flex gap-2">
          <Button
            variant="outline"
            disabled={busy || loading}
            onClick={() =>
              void perform(async () => {
                const data = await request("");
                setSkills(data.skills);
                setDetail(null);
                setNotice("技能列表已刷新。");
              })
            }
          >
            刷新
          </Button>
          <Button
            disabled={busy || loading}
            onClick={() => {
              setCreating(true);
              setDetail(null);
            }}
          >
            新建技能
          </Button>
        </div>
      </div>
      {error && (
        <p
          role="alert"
          className="text-destructive text-sm"
        >
          {error}
        </p>
      )}
      {notice && (
        <p
          role="status"
          className="text-sm"
        >
          {notice}
        </p>
      )}
      {loading && <p role="status">正在加载技能…</p>}
      {creating && (
        <form
          className="space-y-3 rounded-xl border p-4"
          onSubmit={(event) => {
            event.preventDefault();
            void perform(async () => {
              const skill: Skill = await request("", {
                method: "POST",
                body: JSON.stringify({
                  name: name.trim(),
                  description: description.trim(),
                  content: content.trim(),
                }),
              });
              setSkills((items) =>
                [...items, skill].sort((a, b) => a.name.localeCompare(b.name)),
              );
              setCreating(false);
              setName("");
              setDescription("");
              setContent("");
              setScope("user");
              setQuery("");
              setNotice("自定义技能已创建。");
            });
          }}
        >
          <h4 className="font-medium">新建自定义技能</h4>
          <Input
            aria-label="技能名称"
            placeholder="技能名称，例如 summarize-notes"
            required
            maxLength={64}
            pattern="[a-z0-9]+(-[a-z0-9]+)*"
            value={name}
            disabled={busy}
            onChange={(e) => setName(e.target.value)}
          />
          <Textarea
            aria-label="技能描述"
            placeholder="描述适用任务和触发条件"
            required
            maxLength={1024}
            value={description}
            disabled={busy}
            onChange={(e) => setDescription(e.target.value)}
          />
          <Textarea
            aria-label="技能正文"
            placeholder="填写 Markdown 指令正文，名称和描述会自动写入 SKILL.md。"
            className="min-h-48"
            required
            maxLength={100000}
            value={content}
            disabled={busy}
            onChange={(e) => setContent(e.target.value)}
          />
          <div className="flex gap-2">
            <Button
              type="submit"
              disabled={
                busy || !name.trim() || !description.trim() || !content.trim()
              }
            >
              保存技能
            </Button>
            <Button
              type="button"
              variant="outline"
              disabled={busy}
              onClick={() => setCreating(false)}
            >
              取消
            </Button>
          </div>
        </form>
      )}
      <div className="flex flex-wrap gap-2">
        <Input
          className="min-w-40 flex-1"
          aria-label="搜索技能"
          placeholder="搜索名称或描述"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <select
          aria-label="技能分类"
          className="rounded-md border p-2 text-sm"
          value={scope}
          onChange={(e) => setScope(e.target.value)}
        >
          <option value="all">全部（{skills.length}）</option>
          <option value="public">公共</option>
          <option value="user">自定义</option>
        </select>
      </div>
      {!loading && !error && visible.length === 0 && (
        <p className="text-muted-foreground rounded-xl border border-dashed p-8 text-center text-sm">
          {skills.length ? "没有匹配的技能。" : "暂无技能，可新建自定义技能。"}
        </p>
      )}
      {visible.map((skill) => (
        <article
          key={skill.name}
          className="space-y-3 rounded-xl border p-5"
        >
          <div className="flex items-start gap-4">
            <div className="min-w-0 flex-1">
              <h4 className="font-medium break-words">{skill.name}</h4>
              <p className="text-muted-foreground mt-1 text-sm break-words whitespace-pre-wrap">
                {skill.description}
              </p>
            </div>
            <Switch
              aria-label={`启用 ${skill.name}`}
              checked={skill.enabled}
              disabled={busy || loading}
              onCheckedChange={(enabled) =>
                void perform(async () => {
                  const updated: Skill = await request(
                    `/${encodeURIComponent(skill.name)}`,
                    { method: "PATCH", body: JSON.stringify({ enabled }) },
                  );
                  setSkills((items) =>
                    items.map((item) =>
                      item.name === updated.name ? updated : item,
                    ),
                  );
                  setNotice(
                    `${skill.name} 已${updated.enabled ? "启用" : "停用"}。`,
                  );
                })
              }
            />
          </div>
          <div className="flex items-center justify-between gap-2">
            <p className="text-muted-foreground text-xs">
              {skill.category === "public" ? "公共" : "自定义"} ·{" "}
              {skill.enabled ? "已启用" : "已停用"}
            </p>
            <Button
              variant="outline"
              disabled={busy}
              onClick={() =>
                void perform(async () => {
                  setDetail(
                    await request(`/${encodeURIComponent(skill.name)}`),
                  );
                })
              }
            >
              查看内容
            </Button>
          </div>
          {detail?.name === skill.name && (
            <div className="space-y-3 border-t pt-3">
              <div className="flex items-center justify-between">
                <h5 className="text-sm font-medium">SKILL.md</h5>
                <Button
                  variant="ghost"
                  onClick={() => setDetail(null)}
                >
                  收起
                </Button>
              </div>
              {detail.license && (
                <p className="text-muted-foreground text-xs">
                  许可证：{detail.license}
                </p>
              )}
              <pre className="bg-muted max-h-96 overflow-auto rounded-md p-3 text-xs break-words whitespace-pre-wrap">
                {detail.content}
              </pre>
            </div>
          )}
        </article>
      ))}
    </div>
  );
}
