"use client";

import {
  Database,
  FileText,
  Pencil,
  Plus,
  Search,
  Trash2,
  Upload,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQueryState } from "nuqs";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { backendRequest } from "@/lib/backend-request";
import { DEFAULT_API_URL, resolveApiUrl } from "@/lib/config";

export type KnowledgeBase = {
  id: string;
  name: string;
  description: string;
  document_count: number;
  updated_at?: string;
};

type KnowledgeDocument = {
  id: string;
  title: string;
  source_uri: string;
  status: string;
  chunk_count: number;
  updated_at: string;
};

export function KnowledgeSettings() {
  const [apiUrl] = useQueryState("apiUrl");
  const [authScheme] = useQueryState("authScheme");
  const base = resolveApiUrl(
    apiUrl || process.env.NEXT_PUBLIC_API_URL || DEFAULT_API_URL,
  );
  return (
    <KnowledgePanel
      key={`${base}:${authScheme}`}
      base={base}
      authScheme={authScheme}
    />
  );
}

function KnowledgePanel({
  base,
  authScheme,
}: {
  base: string;
  authScheme: string | null;
}) {
  const [items, setItems] = useState<KnowledgeBase[]>([]);
  const [documents, setDocuments] = useState<KnowledgeDocument[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [editing, setEditing] = useState<KnowledgeBase | null>(null);
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [deleting, setDeleting] = useState<string | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const uploadTarget = useRef<string | null>(null);

  const request = useCallback(
    (path: string, init?: RequestInit) =>
      backendRequest(base, authScheme, `/knowledge${path}`, init),
    [base, authScheme],
  );

  const reload = useCallback(
    async (signal?: AbortSignal) => {
      const data = await request("/bases", { signal });
      setItems(data.knowledge_bases);
    },
    [request],
  );

  useEffect(() => {
    const controller = new AbortController();
    request("/bases", { signal: controller.signal })
      .then((data) => {
        if (!controller.signal.aborted) setItems(data.knowledge_bases);
      })
      .catch((cause: Error) => {
        if (!controller.signal.aborted) setError(cause.message);
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [request]);

  async function perform(action: () => Promise<void>) {
    if (busy) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await action();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "操作失败，请重试。");
    } finally {
      setBusy(false);
    }
  }

  async function openDocuments(id: string) {
    await perform(async () => {
      if (selected === id) {
        setSelected(null);
        return;
      }
      const data = await request(`/bases/${encodeURIComponent(id)}/documents`);
      setDocuments(data.documents);
      setSelected(id);
    });
  }

  async function reloadDocuments(id: string) {
    const data = await request(`/bases/${encodeURIComponent(id)}/documents`);
    setDocuments(data.documents);
    setSelected(id);
  }

  function beginEdit(item?: KnowledgeBase) {
    setCreating(!item);
    setEditing(item ?? null);
    setName(item?.name ?? "");
    setDescription(item?.description ?? "");
    setDeleting(null);
  }

  const filtered = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    return normalized
      ? items.filter((item) =>
          `${item.name} ${item.description}`.toLowerCase().includes(normalized),
        )
      : items;
  }, [items, query]);

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h3 className="text-xl font-semibold tracking-tight">知识库</h3>
          <p className="text-muted-foreground mt-1.5 text-sm">
            创建可复用知识库、索引文档，并在对话中按需绑定。
          </p>
        </div>
        <Button
          onClick={() => beginEdit()}
          disabled={busy || loading}
        >
          <Plus /> 创建知识库
        </Button>
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

      {(creating || editing) && (
        <form
          className="space-y-3 rounded-xl border p-4"
          onSubmit={(event) => {
            event.preventDefault();
            void perform(async () => {
              if (editing) {
                await request(`/bases/${encodeURIComponent(editing.id)}`, {
                  method: "PUT",
                  body: JSON.stringify({
                    name: name.trim(),
                    description: description.trim(),
                  }),
                });
                setNotice("知识库信息已更新。");
              } else {
                await request("/bases", {
                  method: "POST",
                  body: JSON.stringify({
                    name: name.trim(),
                    description: description.trim(),
                  }),
                });
                setNotice("知识库已创建。");
              }
              setCreating(false);
              setEditing(null);
              await reload();
            });
          }}
        >
          <h4 className="font-medium">
            {editing ? "编辑知识库" : "创建知识库"}
          </h4>
          <Input
            aria-label="知识库名称"
            placeholder="知识库名称"
            required
            maxLength={120}
            value={name}
            disabled={busy}
            onChange={(event) => setName(event.target.value)}
          />
          <Textarea
            aria-label="知识库描述"
            placeholder="说明内容范围和适用场景（可选）"
            maxLength={1000}
            value={description}
            disabled={busy}
            onChange={(event) => setDescription(event.target.value)}
          />
          <div className="flex gap-2">
            <Button
              type="submit"
              disabled={busy || !name.trim()}
            >
              保存
            </Button>
            <Button
              type="button"
              variant="outline"
              disabled={busy}
              onClick={() => {
                setCreating(false);
                setEditing(null);
              }}
            >
              取消
            </Button>
          </div>
        </form>
      )}

      <div className="relative max-w-lg">
        <Search className="text-muted-foreground absolute top-1/2 left-3 size-4 -translate-y-1/2" />
        <Input
          aria-label="搜索知识库"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          className="h-11 pl-9"
          placeholder="搜索知识库"
        />
      </div>

      {loading && <p role="status">正在加载知识库…</p>}
      {!loading && !error && filtered.length === 0 && (
        <div className="text-muted-foreground rounded-xl border border-dashed py-14 text-center text-sm">
          {items.length
            ? "没有找到匹配的知识库"
            : "暂无知识库，创建后即可上传文档并绑定到对话。"}
        </div>
      )}
      <div className="space-y-3">
        {filtered.map((item) => (
          <article
            key={item.id}
            className="rounded-xl border p-4"
          >
            <div className="flex items-center gap-4">
              <button
                type="button"
                className="flex min-w-0 flex-1 items-center gap-4 text-left"
                onClick={() => void openDocuments(item.id)}
                disabled={busy}
              >
                <span className="bg-muted flex size-10 shrink-0 items-center justify-center rounded-lg">
                  <Database className="size-5" />
                </span>
                <span className="min-w-0 flex-1">
                  <span className="flex flex-wrap items-center gap-x-3 gap-y-1">
                    <span className="font-medium">{item.name}</span>
                    <span className="text-muted-foreground text-xs">
                      {item.document_count} 个文档
                    </span>
                  </span>
                  <span className="text-muted-foreground mt-1 block truncate text-sm">
                    {item.description || "暂无描述"}
                  </span>
                </span>
              </button>
              <Button
                variant="ghost"
                size="icon"
                aria-label={`上传文档到 ${item.name}`}
                title="上传文档"
                disabled={busy}
                onClick={() => {
                  uploadTarget.current = item.id;
                  fileInput.current?.click();
                }}
              >
                <Upload />
              </Button>
              <Button
                variant="ghost"
                size="icon"
                aria-label={`编辑 ${item.name}`}
                title="编辑"
                disabled={busy}
                onClick={() => beginEdit(item)}
              >
                <Pencil />
              </Button>
              {deleting === item.id ? (
                <div className="flex gap-1">
                  <Button
                    size="sm"
                    variant="destructive"
                    disabled={busy}
                    onClick={() =>
                      void perform(async () => {
                        await request(`/bases/${item.id}`, {
                          method: "DELETE",
                        });
                        setDeleting(null);
                        setSelected(null);
                        await reload();
                        setNotice("知识库已删除。");
                      })
                    }
                  >
                    确认
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => setDeleting(null)}
                  >
                    取消
                  </Button>
                </div>
              ) : (
                <Button
                  variant="ghost"
                  size="icon"
                  className="hover:bg-destructive/10 hover:text-destructive"
                  aria-label={`删除 ${item.name}`}
                  title="删除"
                  disabled={busy}
                  onClick={() => setDeleting(item.id)}
                >
                  <Trash2 />
                </Button>
              )}
            </div>
            {selected === item.id && (
              <div className="mt-4 space-y-2 border-t pt-4">
                <div className="flex items-center justify-between">
                  <h5 className="text-sm font-medium">文档</h5>
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={busy}
                    onClick={() => fileInput.current?.click()}
                  >
                    <Upload /> 上传 UTF-8 文档
                  </Button>
                </div>
                {documents.length === 0 && (
                  <p className="text-muted-foreground py-3 text-sm">
                    暂无文档。
                  </p>
                )}
                {documents.map((document) => (
                  <div
                    key={document.id}
                    className="bg-muted/50 flex items-center gap-3 rounded-lg px-3 py-2"
                  >
                    <FileText className="text-muted-foreground size-4 shrink-0" />
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-sm">{document.title}</p>
                      <p className="text-muted-foreground text-xs">
                        {document.chunk_count} 个片段 · {document.status}
                      </p>
                    </div>
                    <Button
                      variant="ghost"
                      size="icon"
                      aria-label={`删除文档 ${document.title}`}
                      disabled={busy}
                      onClick={() =>
                        void perform(async () => {
                          await request(
                            `/bases/${item.id}/documents/${document.id}`,
                            { method: "DELETE" },
                          );
                          await reloadDocuments(item.id);
                          await reload();
                          setNotice("文档已删除。");
                        })
                      }
                    >
                      <Trash2 />
                    </Button>
                  </div>
                ))}
              </div>
            )}
          </article>
        ))}
      </div>
      <input
        ref={fileInput}
        className="hidden"
        type="file"
        accept=".txt,.md,.markdown,.csv,.json,.yaml,.yml,text/*,application/json"
        onChange={(event) => {
          const file = event.target.files?.[0];
          const target = uploadTarget.current ?? selected;
          event.target.value = "";
          uploadTarget.current = null;
          if (!file || !target) return;
          void perform(async () => {
            const content = await file.text();
            await request(`/bases/${target}/documents`, {
              method: "POST",
              body: JSON.stringify({
                filename: file.name,
                content,
                replace: true,
              }),
            });
            const data = await request(`/bases/${target}/documents`);
            setDocuments(data.documents);
            setSelected(target);
            await reload();
            setNotice(`${file.name} 已完成索引。`);
          });
        }}
      />
    </div>
  );
}
