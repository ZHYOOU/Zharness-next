import { Button } from "@/components/ui/button";
import { useThreads } from "@/providers/Thread";
import { Thread } from "@langchain/langgraph-sdk";
import { MouseEvent, useEffect, useState } from "react";

import { getContentString } from "../utils";
import { useQueryState, parseAsBoolean } from "nuqs";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import {
  ChevronsUpDown,
  LoaderCircle,
  PanelRightOpen,
  PanelRightClose,
  Settings,
  Trash2,
} from "lucide-react";
import { useMediaQuery } from "@/hooks/useMediaQuery";
import { toast } from "sonner";
import { SettingsDialog } from "@/components/settings-dialog";

function ThreadList({
  threads,
  onThreadClick,
}: {
  threads: Thread[];
  onThreadClick?: (threadId: string) => void;
}) {
  const [threadId, setThreadId] = useQueryState("threadId");
  const { deleteThread, getThreads, setThreads } = useThreads();
  const [deletingThreadId, setDeletingThreadId] = useState<string | null>(null);

  const handleDelete = async (
    event: MouseEvent<HTMLButtonElement>,
    thread: Thread,
  ) => {
    event.preventDefault();
    event.stopPropagation();

    const confirmed = window.confirm(
      "Delete this thread and its workspace resources? This cannot be undone.",
    );
    if (!confirmed) return;

    setDeletingThreadId(thread.thread_id);
    try {
      await deleteThread(thread.thread_id);
      if (thread.thread_id === threadId) {
        await setThreadId(null);
      }
      setThreads(await getThreads());
      toast.success("Thread deleted");
    } catch (error) {
      console.error(error);
      toast.error("Failed to delete thread");
    } finally {
      setDeletingThreadId(null);
    }
  };

  return (
    <div className="flex h-full w-full flex-col items-start justify-start gap-2 overflow-y-scroll [&::-webkit-scrollbar]:w-1.5 [&::-webkit-scrollbar-thumb]:rounded-full [&::-webkit-scrollbar-thumb]:bg-gray-300 [&::-webkit-scrollbar-track]:bg-transparent">
      {threads.map((t) => {
        let itemText = t.thread_id;
        if (
          typeof t.values === "object" &&
          t.values &&
          "messages" in t.values &&
          Array.isArray(t.values.messages) &&
          t.values.messages?.length > 0
        ) {
          const firstMessage = t.values.messages[0];
          itemText = getContentString(firstMessage.content);
        }
        return (
          <div
            key={t.thread_id}
            className="group flex w-full items-center gap-1 px-1"
          >
            <Button
              variant="ghost"
              className="min-w-0 flex-1 items-start justify-start text-left font-normal"
              disabled={deletingThreadId === t.thread_id}
              onClick={(e) => {
                e.preventDefault();
                onThreadClick?.(t.thread_id);
                if (t.thread_id === threadId) return;
                setThreadId(t.thread_id);
              }}
            >
              <p className="truncate text-ellipsis">{itemText}</p>
            </Button>
            <Button
              type="button"
              variant="ghost"
              size="icon"
              className="text-muted-foreground hover:bg-destructive/10 hover:text-destructive size-8 shrink-0 opacity-70 transition-opacity group-hover:opacity-100 focus-visible:opacity-100"
              aria-label={`Delete thread: ${itemText}`}
              title="Delete thread"
              disabled={deletingThreadId !== null}
              onClick={(event) => handleDelete(event, t)}
            >
              {deletingThreadId === t.thread_id ? (
                <LoaderCircle className="animate-spin" />
              ) : (
                <Trash2 />
              )}
            </Button>
          </div>
        );
      })}
    </div>
  );
}

function ThreadHistoryLoading() {
  return (
    <div className="flex h-full w-full flex-col items-start justify-start gap-2 overflow-y-scroll [&::-webkit-scrollbar]:w-1.5 [&::-webkit-scrollbar-thumb]:rounded-full [&::-webkit-scrollbar-thumb]:bg-gray-300 [&::-webkit-scrollbar-track]:bg-transparent">
      {Array.from({ length: 30 }).map((_, i) => (
        <Skeleton
          key={`skeleton-${i}`}
          className="h-10 w-[280px]"
        />
      ))}
    </div>
  );
}

export default function ThreadHistory() {
  const isLargeScreen = useMediaQuery("(min-width: 1024px)");
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [chatHistoryOpen, setChatHistoryOpen] = useQueryState(
    "chatHistoryOpen",
    parseAsBoolean.withDefault(false),
  );

  const { getThreads, threads, setThreads, threadsLoading, setThreadsLoading } =
    useThreads();

  useEffect(() => {
    if (typeof window === "undefined") return;
    setThreadsLoading(true);
    getThreads()
      .then(setThreads)
      .catch(console.error)
      .finally(() => setThreadsLoading(false));
  }, [getThreads, setThreads, setThreadsLoading]);

  return (
    <>
      <div className="shadow-inner-right hidden h-screen w-[300px] shrink-0 flex-col border-r-[1px] border-slate-300 lg:flex">
        <div className="flex w-full items-center justify-between px-4 pt-1.5">
          <Button
            className="hover:bg-gray-100"
            variant="ghost"
            onClick={() => setChatHistoryOpen((p) => !p)}
          >
            {chatHistoryOpen ? (
              <PanelRightOpen className="size-5" />
            ) : (
              <PanelRightClose className="size-5" />
            )}
          </Button>
          <h1 className="text-xl font-semibold tracking-tight">
            Thread History
          </h1>
        </div>
        <div className="min-h-0 flex-1 px-2 pt-6">
          {threadsLoading ? (
            <ThreadHistoryLoading />
          ) : (
            <ThreadList threads={threads} />
          )}
        </div>
        <div className="w-full border-t p-2">
          <Button
            variant="ghost"
            className="text-muted-foreground w-full justify-start px-3 font-normal"
            onClick={() => setSettingsOpen(true)}
          >
            <Settings className="size-4" />
            <span className="flex-1 text-left">设置与更多</span>
            <ChevronsUpDown className="size-4" />
          </Button>
        </div>
      </div>
      <div className="lg:hidden">
        <Sheet
          open={!!chatHistoryOpen && !isLargeScreen}
          onOpenChange={(open) => {
            if (isLargeScreen) return;
            setChatHistoryOpen(open);
          }}
        >
          <SheetContent
            side="left"
            className="flex lg:hidden"
          >
            <SheetHeader>
              <SheetTitle>Thread History</SheetTitle>
            </SheetHeader>
            <ThreadList
              threads={threads}
              onThreadClick={() => setChatHistoryOpen((o) => !o)}
            />
            <Button
              variant="ghost"
              className="mt-auto w-full justify-start"
              onClick={() => setSettingsOpen(true)}
            >
              <Settings /> 设置与更多
            </Button>
          </SheetContent>
        </Sheet>
      </div>
      <SettingsDialog
        open={settingsOpen}
        onOpenChange={setSettingsOpen}
      />
    </>
  );
}
