"use client";

import * as Dialog from "@radix-ui/react-dialog";
import {
  Brain,
  Check,
  ChevronRight,
  Database,
  FileText,
  Pencil,
  Plus,
  Search,
  Server,
  Sparkles,
  Trash2,
  Upload,
  Wrench,
  X,
} from "lucide-react";
import { useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";

type SettingsSection = "memory" | "knowledge" | "tools" | "skills";

type SettingsDialogProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
};

const sections = [
  { id: "memory", label: "记忆", icon: Brain },
  { id: "knowledge", label: "知识库", icon: Database },
  { id: "tools", label: "MCP 工具", icon: Wrench },
  { id: "skills", label: "技能", icon: Sparkles },
] satisfies Array<{
  id: SettingsSection;
  label: string;
  icon: typeof Brain;
}>;

const initialKnowledgeBases = [
  {
    id: 1,
    name: "产品文档",
    description: "产品说明、使用手册与常见问题",
    files: 24,
  },
  {
    id: 2,
    name: "个人笔记",
    description: "日常记录与项目资料",
    files: 8,
  },
];

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

const initialSkills = [
  {
    id: 1,
    name: "data-analysis",
    description: "分析 Excel、CSV 等结构化数据，生成统计结果与摘要。",
    scope: "公共",
    enabled: true,
  },
  {
    id: 2,
    name: "deep-research",
    description: "针对需要在线资料的问题执行系统化、多角度的深入研究。",
    scope: "公共",
    enabled: true,
  },
  {
    id: 3,
    name: "ppt-generation",
    description: "根据主题和资料生成内容完整、视觉清晰的演示文稿。",
    scope: "自定义",
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

function MemorySettings() {
  const [enabled, setEnabled] = useState(true);
  const [memory, setMemory] = useState(
    "我偏好简洁、直接的回答。涉及代码修改时，先说明改动范围，再给出结果。",
  );
  const [savedMemory, setSavedMemory] = useState(memory);

  return (
    <div className="space-y-6">
      <SectionHeading
        title="记忆"
        description="管理 Agent 在不同对话间可以使用的长期信息。"
      />
      <div className="rounded-xl border p-5">
        <div className="flex items-start justify-between gap-5">
          <div>
            <p className="font-medium">启用长期记忆</p>
            <p className="text-muted-foreground mt-1 text-sm leading-6">
              允许 Agent 在未来的对话中参考已保存的偏好和背景。
            </p>
          </div>
          <Switch
            aria-label="启用长期记忆"
            checked={enabled}
            onCheckedChange={setEnabled}
          />
        </div>
      </div>
      <div className="rounded-xl border p-5">
        <label
          htmlFor="memory-content"
          className="font-medium"
        >
          关于我的记忆
        </label>
        <p className="text-muted-foreground mt-1 text-sm">
          你可以直接编辑希望 Agent 记住的内容。
        </p>
        <Textarea
          id="memory-content"
          value={memory}
          onChange={(event) => setMemory(event.target.value)}
          disabled={!enabled}
          className="mt-4 min-h-40 resize-none leading-6"
          placeholder="例如：我的工作习惯、偏好或长期目标……"
        />
        <div className="mt-4 flex items-center justify-end gap-3">
          {memory === savedMemory && (
            <span className="text-muted-foreground flex items-center gap-1.5 text-sm">
              <Check className="size-4" /> 已保存
            </span>
          )}
          <Button
            disabled={!enabled || memory === savedMemory}
            onClick={() => setSavedMemory(memory)}
          >
            保存更改
          </Button>
        </div>
      </div>
    </div>
  );
}

function KnowledgeSettings() {
  const [query, setQuery] = useState("");
  const [knowledgeBases, setKnowledgeBases] = useState(initialKnowledgeBases);

  const filteredItems = useMemo(() => {
    const normalizedQuery = query.trim().toLowerCase();
    if (!normalizedQuery) return knowledgeBases;
    return knowledgeBases.filter((item) =>
      `${item.name} ${item.description}`
        .toLowerCase()
        .includes(normalizedQuery),
    );
  }, [knowledgeBases, query]);

  const createKnowledgeBase = () => {
    const nextNumber = knowledgeBases.length + 1;
    setKnowledgeBases((items) => [
      ...items,
      {
        id: Date.now(),
        name: `新知识库 ${nextNumber}`,
        description: "暂无描述",
        files: 0,
      },
    ]);
  };

  return (
    <div className="space-y-6">
      <SectionHeading
        title="知识库"
        description="创建知识库、索引文档并测试 RAG 检索效果。"
        action={
          <Button onClick={createKnowledgeBase}>
            <Plus /> 创建知识库
          </Button>
        }
      />
      <div className="relative max-w-lg">
        <Search className="text-muted-foreground absolute top-1/2 left-3 size-4 -translate-y-1/2" />
        <Input
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          className="h-11 pl-9"
          placeholder="搜索文档或知识库"
        />
      </div>
      <div className="space-y-3">
        {filteredItems.map((item) => (
          <div
            key={item.id}
            className="group hover:bg-muted/40 flex items-center gap-4 rounded-xl border p-4 transition-colors"
          >
            <div className="bg-muted flex size-10 shrink-0 items-center justify-center rounded-lg">
              <Database className="size-5" />
            </div>
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                <p className="font-medium">{item.name}</p>
                <span className="text-muted-foreground text-xs">
                  {item.files} 个文档
                </span>
              </div>
              <p className="text-muted-foreground mt-1 truncate text-sm">
                {item.description}
              </p>
            </div>
            <Button
              variant="ghost"
              size="icon"
              aria-label={`上传文档到 ${item.name}`}
              title="上传文档"
            >
              <Upload />
            </Button>
            <Button
              variant="ghost"
              size="icon"
              aria-label={`编辑 ${item.name}`}
              title="编辑"
            >
              <Pencil />
            </Button>
            <Button
              variant="ghost"
              size="icon"
              className="hover:bg-destructive/10 hover:text-destructive"
              aria-label={`删除 ${item.name}`}
              title="删除"
              onClick={() =>
                setKnowledgeBases((items) =>
                  items.filter((candidate) => candidate.id !== item.id),
                )
              }
            >
              <Trash2 />
            </Button>
          </div>
        ))}
        {filteredItems.length === 0 && (
          <div className="text-muted-foreground rounded-xl border border-dashed py-14 text-center text-sm">
            没有找到匹配的知识库
          </div>
        )}
      </div>
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

function SkillSettings() {
  const [scope, setScope] = useState<"公共" | "自定义">("公共");
  const [skills, setSkills] = useState(initialSkills);

  const visibleSkills = skills.filter((skill) => skill.scope === scope);

  const createSkill = () => {
    setScope("自定义");
    setSkills((items) => [
      ...items,
      {
        id: Date.now(),
        name: `custom-skill-${items.length + 1}`,
        description: "描述该技能适用的任务和触发条件。",
        scope: "自定义",
        enabled: false,
      },
    ]);
  };

  return (
    <div className="space-y-5">
      <SectionHeading
        title="技能"
        description="管理 Agent Skill 配置和启用状态。"
        action={
          <Button onClick={createSkill}>
            <Sparkles /> 新建技能
          </Button>
        }
      />
      <div className="border-b">
        {(["公共", "自定义"] as const).map((item) => (
          <button
            key={item}
            type="button"
            className={cn(
              "text-muted-foreground relative px-3 py-2 text-sm transition-colors",
              scope === item && "text-foreground",
            )}
            onClick={() => setScope(item)}
          >
            {item}
            {scope === item && (
              <span className="bg-foreground absolute inset-x-0 -bottom-px h-0.5" />
            )}
          </button>
        ))}
      </div>
      <div className="space-y-3">
        {visibleSkills.map((skill) => (
          <div
            key={skill.id}
            className="flex items-start gap-4 rounded-xl border p-5"
          >
            <div className="min-w-0 flex-1">
              <p className="font-medium">{skill.name}</p>
              <p className="text-muted-foreground mt-1 text-sm leading-6">
                {skill.description}
              </p>
            </div>
            <Switch
              aria-label={`启用 ${skill.name}`}
              checked={skill.enabled}
              onCheckedChange={(enabled) =>
                setSkills((items) =>
                  items.map((item) =>
                    item.id === skill.id ? { ...item, enabled } : item,
                  ),
                )
              }
            />
          </div>
        ))}
        {visibleSkills.length === 0 && (
          <div className="text-muted-foreground rounded-xl border border-dashed py-14 text-center text-sm">
            暂无{scope}技能
          </div>
        )}
      </div>
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
              {activeSection === "tools" && <ToolSettings />}
              {activeSection === "skills" && <SkillSettings />}
            </div>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
