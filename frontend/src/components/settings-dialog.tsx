"use client";

import * as Dialog from "@radix-ui/react-dialog";
import {
  Brain,
  ChevronRight,
  Database,
  FileText,
  Pencil,
  Plus,
  Server,
  Sparkles,
  Wrench,
  X,
  Coins,
} from "lucide-react";
import { useState } from "react";
import { parseAsBoolean, useQueryState } from "nuqs";

import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { cn } from "@/lib/utils";
import { SkillSettings } from "@/components/skill-settings";
import { MemorySettings } from "@/components/memory-settings";
import { KnowledgeSettings } from "@/components/knowledge-settings";

type SettingsSection =
  "memory" | "knowledge" | "token-usage" | "tools" | "skills";

type SettingsDialogProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
};

const sections = [
  { id: "memory", label: "记忆", icon: Brain },
  { id: "knowledge", label: "知识库", icon: Database },
  { id: "token-usage", label: "Token 用量", icon: Coins },
  { id: "tools", label: "MCP 工具", icon: Wrench },
  { id: "skills", label: "技能", icon: Sparkles },
] satisfies Array<{
  id: SettingsSection;
  label: string;
  icon: typeof Brain;
}>;

const initialTools = [
  {
    id: 1,
    name: "filesystem",
    description: "读取和管理本地工作区文件",
    command: "npx @modelcontextprotocol/server-filesystem",
    enabled: true,
  },
  {
    id: 2,
    name: "web-search",
    description: "检索公开网络信息并返回相关结果",
    command: "uvx mcp-server-web-search",
    enabled: false,
  },
];

function SectionHeading({
  title,
  description,
  action,
}: {
  title: string;
  description: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
      <div>
        <h3 className="text-xl font-semibold tracking-tight">{title}</h3>
        <p className="text-muted-foreground mt-1.5 text-sm">{description}</p>
      </div>
      {action}
    </div>
  );
}

function ToolSettings() {
  const [tools, setTools] = useState(initialTools);

  const addTool = () => {
    setTools((items) => [
      ...items,
      {
        id: Date.now(),
        name: `custom-mcp-${items.length + 1}`,
        description: "新建的自定义 MCP Server",
        command: "填写启动命令",
        enabled: false,
      },
    ]);
  };

  return (
    <div className="space-y-6">
      <SectionHeading
        title="MCP 工具"
        description="添加和管理可供 Agent 调用的 MCP Server。"
        action={
          <Button onClick={addTool}>
            <Plus /> 添加 MCP Server
          </Button>
        }
      />
      <div className="space-y-3">
        {tools.map((tool) => (
          <div
            key={tool.id}
            className="rounded-xl border p-5"
          >
            <div className="flex items-start gap-4">
              <div className="bg-muted flex size-10 shrink-0 items-center justify-center rounded-lg">
                <Server className="size-5" />
              </div>
              <div className="min-w-0 flex-1">
                <p className="font-medium">{tool.name}</p>
                <p className="text-muted-foreground mt-1 text-sm leading-6">
                  {tool.description}
                </p>
              </div>
              <Switch
                aria-label={`启用 ${tool.name}`}
                checked={tool.enabled}
                onCheckedChange={(enabled) =>
                  setTools((items) =>
                    items.map((item) =>
                      item.id === tool.id ? { ...item, enabled } : item,
                    ),
                  )
                }
              />
            </div>
            <div className="bg-muted/60 mt-4 flex items-center gap-3 rounded-lg px-3 py-2.5">
              <FileText className="text-muted-foreground size-4 shrink-0" />
              <code className="min-w-0 flex-1 truncate text-xs">
                {tool.command}
              </code>
              <Button
                variant="ghost"
                size="icon"
                className="size-7"
                aria-label={`编辑 ${tool.name}`}
              >
                <Pencil />
              </Button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function TokenUsageSettings() {
  const [showTokenUsage, setShowTokenUsage] = useQueryState(
    "showTokenUsage",
    parseAsBoolean.withDefault(true),
  );
  const [showMessageTokenUsage, setShowMessageTokenUsage] = useQueryState(
    "showMessageTokenUsage",
    parseAsBoolean.withDefault(true),
  );

  return (
    <div className="space-y-6">
      <SectionHeading
        title="Token 用量"
        description="控制会话中模型 token 统计信息的显示方式。"
      />
      <div className="divide-y rounded-xl border">
        <div className="flex items-center justify-between gap-4 p-5">
          <div>
            <p className="font-medium">显示 Token 用量</p>
            <p className="text-muted-foreground mt-1 text-sm">
              在会话顶部显示累计的输入、输出和总 token 数。
            </p>
          </div>
          <Switch
            aria-label="显示 Token 用量"
            checked={showTokenUsage}
            onCheckedChange={setShowTokenUsage}
          />
        </div>
        <div className="flex items-center justify-between gap-4 p-5">
          <div>
            <p className="font-medium">显示每步用量</p>
            <p className="text-muted-foreground mt-1 text-sm">
              在每次模型调用下方显示该步的 token 明细。
            </p>
          </div>
          <Switch
            aria-label="显示每步 Token 用量"
            checked={showMessageTokenUsage}
            disabled={!showTokenUsage}
            onCheckedChange={setShowMessageTokenUsage}
          />
        </div>
      </div>
      <p className="text-muted-foreground text-xs leading-5">
        只有模型提供商返回 usage_metadata 时才会产生统计数据。子 Agent
        用量已计入发起委派的模型步骤。
      </p>
    </div>
  );
}

export function SettingsDialog({ open, onOpenChange }: SettingsDialogProps) {
  const [activeSection, setActiveSection] = useState<SettingsSection>("memory");

  return (
    <Dialog.Root
      open={open}
      onOpenChange={onOpenChange}
    >
      <Dialog.Portal>
        <Dialog.Overlay className="data-[state=closed]:animate-out data-[state=open]:animate-in data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0 fixed inset-0 z-50 bg-black/55 backdrop-blur-[1px]" />
        <Dialog.Content className="bg-background data-[state=closed]:animate-out data-[state=open]:animate-in data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0 data-[state=closed]:zoom-out-95 data-[state=open]:zoom-in-95 fixed top-1/2 left-1/2 z-50 flex h-[min(760px,calc(100vh-32px))] w-[min(1180px,calc(100vw-32px))] -translate-x-1/2 -translate-y-1/2 flex-col overflow-hidden rounded-2xl border shadow-2xl outline-none">
          <div className="flex items-start justify-between px-6 pt-6 pb-5 sm:px-8">
            <div>
              <Dialog.Title className="text-2xl font-semibold tracking-tight">
                设置
              </Dialog.Title>
              <Dialog.Description className="text-muted-foreground mt-1 text-sm">
                根据你的偏好调整 ZHarness 的界面和行为。
              </Dialog.Description>
            </div>
            <Dialog.Close asChild>
              <Button
                variant="ghost"
                size="icon"
                aria-label="关闭设置"
              >
                <X className="size-5" />
              </Button>
            </Dialog.Close>
          </div>

          <div className="flex min-h-0 flex-1 flex-col gap-4 px-4 pb-4 sm:px-6 sm:pb-6 md:flex-row">
            <nav
              aria-label="设置分类"
              className="flex shrink-0 gap-1 overflow-x-auto rounded-xl border p-2 md:w-56 md:flex-col md:overflow-visible"
            >
              {sections.map((section) => {
                const Icon = section.icon;
                return (
                  <button
                    key={section.id}
                    type="button"
                    className={cn(
                      "hover:bg-muted flex shrink-0 items-center gap-3 rounded-lg px-3 py-2.5 text-left text-sm transition-colors md:w-full",
                      activeSection === section.id &&
                        "bg-primary text-primary-foreground hover:bg-primary/90",
                    )}
                    onClick={() => setActiveSection(section.id)}
                  >
                    <Icon className="size-4" />
                    <span className="flex-1">{section.label}</span>
                    <ChevronRight className="hidden size-4 md:block" />
                  </button>
                );
              })}
            </nav>

            <div className="min-h-0 flex-1 overflow-y-auto rounded-xl border p-5 sm:p-7">
              {activeSection === "memory" && <MemorySettings />}
              {activeSection === "knowledge" && <KnowledgeSettings />}
              {activeSection === "token-usage" && <TokenUsageSettings />}
              {activeSection === "tools" && <ToolSettings />}
              {activeSection === "skills" && <SkillSettings />}
            </div>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
