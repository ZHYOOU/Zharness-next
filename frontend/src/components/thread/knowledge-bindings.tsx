"use client";

import { BookOpen, LoaderCircle } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { useQueryState } from "nuqs";

import { Button } from "@/components/ui/button";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet";
import { Switch } from "@/components/ui/switch";
import { backendRequest } from "@/lib/backend-request";
import { DEFAULT_API_URL, resolveApiUrl } from "@/lib/config";

type KnowledgeBase = {
  id: string;
  name: string;
  description: string;
  document_count: number;
};

export function KnowledgeBindings({ threadId }: { threadId: string | null }) {
  const [apiUrl] = useQueryState("apiUrl");
  const [authScheme] = useQueryState("authScheme");
  const base = resolveApiUrl(
    apiUrl || process.env.NEXT_PUBLIC_API_URL || DEFAULT_API_URL,
  );
  const [open, setOpen] = useState(false);
  const [items, setItems] = useState<KnowledgeBase[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  const request = useCallback(
    (path: string, init?: RequestInit) =>
      backendRequest(base, authScheme, `/knowledge${path}`, init),
    [base, authScheme],
  );

  useEffect(() => {
    if (!open || !threadId) return;
    const controller = new AbortController();
    Promise.all([
      request("/bases", { signal: controller.signal }),
      request(`/threads/${encodeURIComponent(threadId)}/bindings`, {
        signal: controller.signal,
      }),
    ])
      .then(([bases, bindings]) => {
        if (controller.signal.aborted) return;
        setItems(bases.knowledge_bases);
        setSelected(bindings.knowledge_base_ids);
      })
      .catch((cause: Error) => {
        if (!controller.signal.aborted) setError(cause.message);
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [open, request, threadId]);

  return (
    <Sheet
      open={open}
      onOpenChange={(nextOpen) => {
        setOpen(nextOpen);
        if (nextOpen) {
          setLoading(true);
          setError("");
        }
      }}
    >
      <SheetTrigger asChild>
        <Button
          variant="ghost"
          size="icon"
          aria-label="绑定会话知识库"
          title={threadId ? "会话知识库" : "发送第一条消息后可绑定知识库"}
          disabled={!threadId}
        >
          <BookOpen className="size-5" />
          {selected.length > 0 && (
            <span className="bg-primary absolute top-1 right-1 size-1.5 rounded-full" />
          )}
        </Button>
      </SheetTrigger>
      <SheetContent className="w-full gap-0 sm:max-w-md">
        <SheetHeader className="border-b px-5 py-5">
          <SheetTitle>会话知识库</SheetTitle>
          <SheetDescription>
            选择 Agent 在当前会话中可以检索的知识库。
          </SheetDescription>
        </SheetHeader>
        <div className="flex-1 space-y-3 overflow-y-auto p-5">
          {error && (
            <p
              role="alert"
              className="text-destructive text-sm"
            >
              {error}
            </p>
          )}
          {loading && (
            <p
              role="status"
              className="text-muted-foreground flex items-center gap-2 text-sm"
            >
              <LoaderCircle className="size-4 animate-spin" /> 正在加载知识库…
            </p>
          )}
          {!loading && !error && items.length === 0 && (
            <div className="text-muted-foreground rounded-xl border border-dashed p-8 text-center text-sm">
              暂无可绑定的知识库，请先在设置中创建。
            </div>
          )}
          {items.map((item) => (
            <div
              key={item.id}
              className="flex items-center gap-4 rounded-xl border p-4"
            >
              <div className="min-w-0 flex-1">
                <p className="font-medium break-words">{item.name}</p>
                <p className="text-muted-foreground mt-1 line-clamp-2 text-sm">
                  {item.description || "暂无描述"}
                </p>
                <p className="text-muted-foreground mt-1 text-xs">
                  {item.document_count} 个文档
                </p>
              </div>
              <Switch
                aria-label={`绑定 ${item.name}`}
                checked={selected.includes(item.id)}
                disabled={loading || saving}
                onCheckedChange={(checked) =>
                  setSelected((current) =>
                    checked
                      ? [...current, item.id]
                      : current.filter((id) => id !== item.id),
                  )
                }
              />
            </div>
          ))}
        </div>
        <SheetFooter className="border-t p-5">
          <Button
            className="w-full"
            disabled={loading || saving || !threadId || !!error}
            onClick={() => {
              if (!threadId) return;
              setSaving(true);
              setError("");
              request(`/threads/${encodeURIComponent(threadId)}/bindings`, {
                method: "PUT",
                body: JSON.stringify({ knowledge_base_ids: selected }),
              })
                .then(() => setOpen(false))
                .catch((cause: Error) => setError(cause.message))
                .finally(() => setSaving(false));
            }}
          >
            {saving ? "正在保存…" : "保存绑定"}
          </Button>
        </SheetFooter>
      </SheetContent>
    </Sheet>
  );
}
