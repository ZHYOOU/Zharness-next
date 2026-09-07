# 会话级 RAG 知识库设计

> 实现状态：首期架构已落地。代码位于 `src/zharness/knowledge/`，主 Agent 已注册
> `knowledge_search`、`knowledge_ingest`、`knowledge_list`、`knowledge_delete`，
> 默认子 Agent 仅注册搜索工具。本文同时作为实现约束与后续演进基线。

## 1. 背景与目标

ZHarness 需要提供一个由 Agent 按需调用的 RAG 知识库能力。知识库保存用户在当前 LangGraph 会话中导入的文档，通过词法检索与向量检索召回相关片段，并将带来源信息的结果返回给 Agent 生成最终回答。

本设计遵循以下目标：

- 知识库严格绑定 LangGraph `thread_id`，不同会话之间不能读取、修改或删除彼此的数据。
- 以 Agent tool 的形式按需检索，不在每次模型调用前自动注入知识。
- 使用阿里云百炼 `text-embedding-v4` 生成稠密向量。
- 使用 `langchain-text-splitters` 完成文档切分，不维护自定义切分算法。
- 使用 PostgreSQL 同时承载文档元数据、文本索引和向量索引。
- 采用稠密检索与词法检索组成的混合检索，并用 RRF 融合排序。
- 参考 LangChain 开源的 `langchain-postgres` 集成，优先复用其连接管理、向量存储接口与异步能力。
- 优先满足个人单用户、会话级知识库需求，不为多租户或多实例部署增加复杂度。

## 2. 非目标

第一阶段不包含以下能力：

- 跨会话共享知识库或用户级公共知识库。
- 自动从对话中抽取知识。
- OCR、图片、音视频及复杂版式解析。
- 网页抓取和远程 URL 自动索引。
- HNSW、IVFFlat 等近似向量索引。
- LLM 查询改写、HyDE 或 reranker。
- 大规模异步索引任务队列。
- 前端知识库管理页面。

## 3. 核心设计决策

### 3.1 与长期记忆分离

知识库作为独立的 `knowledge` 领域实现，不复用 `memory` 的表、仓储或中间件。

两者的语义不同：

| 能力 | `memory` | `knowledge` |
| --- | --- | --- |
| 内容 | 用户偏好、约束、决定和长期事实 | 文档及文档片段 |
| 默认作用域 | 单用户跨会话 | 单个 `thread_id` |
| 写入方式 | 对话抽取和 memory tool | 用户明确导入文件或文本 |
| 召回方式 | 事实搜索与隐藏上下文 | 混合检索 tool |
| 来源要求 | 可没有文档定位 | 必须返回可引用来源 |
| 生命周期 | 长期保留 | 随 thread 删除 |

### 3.2 PostgreSQL 与 pgvector

继续使用项目已有 PostgreSQL，不新增 Qdrant 或 Elasticsearch 服务。托管 PostgreSQL 镜像需要包含 `vector` 扩展，数据库初始化阶段负责启用扩展。

PostgreSQL 承担：

- 文档和 chunk 元数据存储。
- `tsvector`/GIN 词法检索。
- pgvector cosine distance 稠密检索。
- thread 过滤和文档生命周期事务。

### 3.3 使用新版 LangChain PostgreSQL 集成

采用 `langchain-postgres` 新版的 `PGEngine` 与 `PGVectorStore`，不使用已弃用的旧 `PGVector`。

复用范围：

- `PGEngine` 的异步连接管理。
- `PGVectorStore` 对已有表的字段映射。
- 文档向量写入、更新与稠密检索接口。
- metadata filter、cosine distance 等成熟实现。
- `VectorStoreRetriever` 的 `search_type` 与 `search_kwargs` 语义。
- `HybridSearchConfig`、`reciprocal_rank_fusion` 和 `weighted_sum_ranking`。

不直接使用其默认表，原因是本项目需要：

- 独立 documents 表和 thread 生命周期。
- 强制、不可覆盖的 `thread_id` 过滤。
- 中文及代码专用的预分词文本。
- 独立的 `content` 和 `lexical_text`。
- 细粒度来源、章节、页码或行号信息。

混合检索使用 `PGVectorStore` 的内置 hybrid query，但显式指定本项目预先生成的 `search_vector` 作为 `tsv_column`，并把经过相同中文分词规则处理的查询作为 `fts_query`。这样既复用官方实现，又不会对原始 `content` 直接做不适合中文的默认分词。

`PGVectorStore` 必须封装在 KnowledgeService 内部，tool 和 Agent 不得直接访问原始 vector store，也不得自行构造 metadata filter。

## 4. 总体架构

```text
                         ┌───────────────────────────┐
当前 thread workspace ──▶│ knowledge_ingest          │
                         │ - 获取 runtime thread_id  │
                         │ - 解析与结构化分块        │
                         │ - 生成 lexical_text       │
                         │ - 批量生成 embedding      │
                         └─────────────┬─────────────┘
                                       │
                                       ▼
                         ┌───────────────────────────┐
                         │ PostgreSQL + pgvector     │
                         │ - knowledge_documents     │
                         │ - knowledge_chunks        │
                         │ - GIN(search_vector)      │
                         │ - vector(1024)            │
                         └─────────────┬─────────────┘
                                       │
Agent ──▶ knowledge_search ──▶ KnowledgeRetriever
                               │
                               ├─ dense Top N
                               ├─ lexical Top N
                               ├─ configured fusion
                               ├─ 去重与邻接块扩展
                               └─ 返回内容和来源
```

## 5. 模块边界

建议新增以下包结构：

```text
zharness/src/zharness/knowledge/
├── __init__.py
├── types.py
├── loaders.py
├── chunking.py
├── lexical.py
├── embeddings.py
├── repository.py
├── retrieval.py
├── service.py
└── tools.py
```

职责如下：

- `types.py`：文档、chunk、检索结果及索引状态类型。
- `loaders.py`：读取和规范化受支持的文件格式。
- `chunking.py`：配置和编排 `langchain-text-splitters`，并补充 locator 元数据。
- `lexical.py`：中文分词及代码标识符展开。
- `embeddings.py`：创建和验证 embedding provider。
- `repository.py`：documents 表、thread 清理及索引事务。
- `retrieval.py`：参考 LangChain 的检索配置解析、策略校验、后处理和预算裁剪。
- `service.py`：索引编排、幂等控制和进程级资源生命周期。
- `tools.py`：ToolRuntime thread 解析、tool 参数校验和结果序列化。

## 6. Embedding 设计

### 6.1 模型与接口

使用 `langchain_openai.OpenAIEmbeddings` 调用阿里云 OpenAI-compatible embeddings API。

固定默认值：

| 配置 | 值 | 说明 |
| --- | --- | --- |
| model | `text-embedding-v4` | 阿里文本向量模型 |
| dimensions | `1024` | 明确固定表和请求的向量维度 |
| batch_size | `10` | 不超过模型单次输入条数限制 |
| timeout_seconds | `15` | 与项目外部网络请求策略一致 |
| max_retries | `2` | 对临时网络错误有限重试 |
| check_embedding_ctx_length | `false` | 由本项目 chunker 负责长度控制 |

阿里官方文档显示 `text-embedding-v4` 支持自定义维度，1024 维是通用检索场景的推荐平衡点。显式传递维度可以防止服务端默认值变化导致既有表无法写入。

### 6.2 环境变量

敏感配置只从环境读取：

- `EMBEDDING_BASE_URL`
- `EMBEDDING_API_KEY`

`EMBEDDING_BASE_URL` 必须是 OpenAI-compatible API 根路径，例如以 `/compatible-mode/v1` 结束，而不是完整的 `/embeddings` 请求地址。启动或首次使用时验证变量存在，但日志中不得输出 API Key，也不应输出包含敏感查询参数的 URL。

非敏感配置进入 `config.yaml` 的 `knowledge.embedding` 段，并允许使用 `ZHARNESS_KNOWLEDGE_*` 环境变量覆盖。

### 6.3 Provider 抽象

KnowledgeService 仅依赖 LangChain `Embeddings` 接口，通过独立 factory 创建实例。测试使用 fake embeddings，不发起真实网络请求。

第一阶段采用 OpenAI-compatible 接口，不实现 DashScope 原生接口。因此暂不使用仅在 DashScope 原生 API 中提供的 `text_type=query/document` 优化。

## 7. 数据模型

### 7.1 Documents 表

表名：`zharness_knowledge_documents`

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | UUID | 文档 ID |
| `thread_id` | TEXT NOT NULL | LangGraph 会话 ID |
| `source_type` | TEXT NOT NULL | 首期为 `workspace_file` 或 `text` |
| `source_uri` | TEXT | workspace 虚拟路径或逻辑来源 |
| `title` | TEXT NOT NULL | 展示名称 |
| `mime_type` | TEXT | 文件类型 |
| `content_hash` | TEXT NOT NULL | 规范化原文 SHA-256 |
| `status` | TEXT NOT NULL | `indexing`、`ready` 或 `failed` |
| `chunk_count` | INTEGER NOT NULL | 可检索 chunk 数量 |
| `embedding_model` | TEXT NOT NULL | 写入时使用的模型 |
| `embedding_dimensions` | INTEGER NOT NULL | 写入时的向量维度 |
| `index_version` | INTEGER NOT NULL | 分块或检索策略版本 |
| `error` | TEXT | 最近一次索引失败摘要 |
| `created_at` | TIMESTAMPTZ | 创建时间 |
| `updated_at` | TIMESTAMPTZ | 更新时间 |

唯一约束至少覆盖 `(thread_id, source_uri, content_hash)`，用于同一会话内的幂等导入。

### 7.2 Chunks 表

表名：`zharness_knowledge_chunks`

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | TEXT 或 UUID | 稳定 chunk ID |
| `thread_id` | TEXT NOT NULL | 冗余保存以便强制过滤 |
| `document_id` | UUID NOT NULL | documents 外键，级联删除 |
| `is_active` | BOOLEAN NOT NULL | 当前文档版本是否可参与检索 |
| `ordinal` | INTEGER NOT NULL | 文档中的顺序 |
| `content` | TEXT NOT NULL | 原始 chunk 内容 |
| `embedding` | vector(1024) NOT NULL | 稠密向量 |
| `lexical_text` | TEXT NOT NULL | 分词和标识符展开后的文本 |
| `search_vector` | TSVECTOR NOT NULL | 词法检索向量 |
| `title` | TEXT | 文档标题 |
| `source_uri` | TEXT | 来源路径 |
| `heading_path` | JSONB | 章节层级 |
| `locator` | JSONB | 页码、行号、段落等定位信息 |
| `token_count` | INTEGER | chunk 大小 |
| `content_hash` | TEXT NOT NULL | chunk 内容哈希 |

`search_vector` 建议使用存储生成列：

```sql
GENERATED ALWAYS AS (
    to_tsvector('simple', lexical_text)
) STORED
```

初始索引：

- `BTREE(thread_id, is_active)`
- `BTREE(thread_id, document_id)`
- `UNIQUE(document_id, ordinal)`
- `GIN(search_vector)`

第一阶段不创建 HNSW。会话级数据规模较小时，先通过 `thread_id` 缩小范围再进行精确 cosine distance 排序，可以获得稳定召回。只有真实数据和基准测试证明精确查询不满足延迟目标时，才增加近似索引。

`is_active` 冗余保存在 chunks 表中，使 `PGVectorStore` 可以通过公开 metadata filter 同时约束 thread 和可见版本，而不依赖连接 documents 表的自定义私有查询。新版本切换时，在同一事务中激活新 chunks 并停用旧 chunks。

## 8. 会话隔离

### 8.1 Thread ID 来源

所有 knowledge tool 从 `ToolRuntime.execution_info.thread_id` 获取会话 ID。`thread_id` 不出现在 tool schema 中，LLM 和用户均不能传入或覆盖它。

缺少合法 thread ID 时必须 fail closed：

```json
{
  "error": "knowledge tool requires an active thread"
}
```

不能回退到全局知识库，也不能使用空字符串或 `NULL` 作为共享作用域。

### 8.2 Repository 约束

Repository 的每个公开读写方法都必须把非空 `thread_id: str` 作为必填参数，例如：

```text
search(thread_id, query, limit)
ingest(thread_id, ...)
list_documents(thread_id)
delete_document(thread_id, document_id)
delete_thread(thread_id)
```

所有 dense、lexical、读取、更新和删除 SQL 都必须包含 thread 条件。即使调用者知道另一个会话的 `document_id`，也不能读取或删除对应数据。

### 8.3 Thread 删除

LangGraph thread 删除成功后，统一执行 thread 资源清理：

- 删除该 thread 的 documents，借助外键级联删除 chunks。
- 清理该 thread 的 sandbox/container。
- 保留失败日志，并允许后台再次清理遗留数据。

知识库清理失败不应使已成功完成的 LangGraph DELETE 响应变为失败，但必须被记录并可重试。

## 9. 文档读取与分块

### 9.1 首期格式

第一阶段支持：

- 纯文本。
- Markdown。
- 常见源代码和配置文件。
- tool 直接提交的短文本。

暂不支持 PDF、Office、图片和远程 URL。

### 9.2 Workspace 边界

`knowledge_ingest` 接受 `/workspace` 虚拟路径，只能通过当前 thread 对应的 sandbox/workspace 抽象读取文件。不得把虚拟路径直接当作宿主绝对路径，也不得绕过现有 workspace 边界。

### 9.3 切分依赖与原则

文档切分统一使用独立包 `langchain-text-splitters`。它必须作为 ZHarness 的直接依赖通过 `uv` 安装和锁定，不能仅依赖 `langchain` 的传递依赖。

首期不实现自定义切分算法，也不使用 `langchain-experimental` 中的语义切分器。`chunking.py` 只负责：

- 按文档类型选择稳定的公开 splitter。
- 配置 separator、chunk size 和 overlap。
- 把 loader 元数据传递给 LangChain `Document`。
- 将 splitter 结果转换为本项目的 KnowledgeChunk。
- 补充字符位置、行号和标题路径等 locator。

### 9.4 Splitter 路由

不同类型使用以下组合：

| 文档类型 | 首级切分 | 长度兜底切分 |
| --- | --- | --- |
| Markdown | `MarkdownHeaderTextSplitter` | `RecursiveCharacterTextSplitter` |
| 已支持的源代码 | `RecursiveCharacterTextSplitter.from_language(...)` | 同一个 recursive splitter |
| 普通文本和未知文本 | 无 | `RecursiveCharacterTextSplitter` |

Markdown 采用两阶段切分：

1. 使用 `MarkdownHeaderTextSplitter` 按 `#` 至 `######` 提取章节层级。
2. 保留标题文本，使独立 chunk 仍有足够语义上下文。
3. 将标题元数据规范化为 `heading_path`。
4. 对超过上限的章节再次使用 `RecursiveCharacterTextSplitter`。

源代码优先使用 `RecursiveCharacterTextSplitter.from_language(...)` 提供的语言 separator。首期至少映射 Python、JavaScript/TypeScript、Go、Rust、Java、C/C++ 和 Markdown；无法识别的扩展名走普通文本策略。这里的“代码感知”是基于 LangChain separator，而不是 AST 语义切分。

普通文本使用 `RecursiveCharacterTextSplitter`，separator 顺序覆盖：

```text
段落边界 → 换行 → 中文句末标点 → 英文句末标点 → 空格 → 字符
```

具体 separator 必须显式配置，不能依赖库默认值，以保证中英文混合文档的行为稳定。

### 9.5 大小计量

首期使用字符数而不是 token 数作为 splitter 的长度单位：

```text
chunk_size = 2000 characters
chunk_overlap = 200 characters
```

原因是 `RecursiveCharacterTextSplitter` 原生默认以字符数计量，而 `text-embedding-v4` 没有供该组件直接使用的官方本地 tokenizer。使用 OpenAI tokenizer 估算阿里模型会造成不必要的耦合和误差。

2000 字符显著低于 embedding 单条输入上限，同时对英文文本大致保持在常用 RAG chunk 大小范围。该值是首期基线，后续通过中文、英文和代码评测集调整。

Embedding 客户端仍必须拒绝超出模型上限的输入，不能把 splitter 配置视为最终安全边界。

### 9.6 Locator 与元数据

所有 splitter 输入使用 `langchain_core.documents.Document`，至少携带：

- `source_uri`
- `document_id`
- `mime_type`
- `language`

对 `RecursiveCharacterTextSplitter` 启用 `add_start_index=True`，记录 chunk 在输入文本中的起始字符位置。Markdown 两阶段切分产生的相对位置，需要由 `chunking.py` 映射回原文绝对位置。

Loader 在切分前建立换行符位置索引，利用最终 chunk 的绝对字符范围计算：

- `start_char`
- `end_char`
- `start_line`
- `end_line`
- `heading_path`

位置映射必须按原文顺序单调查找，以正确处理重复段落和 overlap。定位信息属于切分后的元数据增强，不自行改变 LangChain splitter 的边界结果。

## 10. 词法文本生成

`content` 保留原文，用于 embedding 和返回给 Agent；`lexical_text` 只服务于关键词检索。

`lexical_text` 处理规则：

- 对中文进行应用层分词。
- 英文单词转为一致的大小写形式。
- 同时保留完整代码标识符和拆分结果。
- 拆分 `snake_case`、`camelCase`、PascalCase 和点分路径。
- 保留文件名、扩展名、类名、函数名及配置键。
- 将标题和来源路径作为可配置的加权词汇加入。

例如：

```text
MemoryRepository.search_facts
```

可以展开为：

```text
MemoryRepository search_facts memory repository search facts
```

词法预处理必须是确定性的，并带有 `index_version`。规则发生不兼容变化时，通过版本提升触发重新索引。

## 11. 混合检索

### 11.1 查询流程

```text
query
  ├─ normalize + lexical tokenize ──▶ lexical Top 40
  └─ text-embedding-v4 ─────────────▶ dense Top 40
                                         │
                         thread filter ──┤
                                         ▼
                                  configured fusion
                                         │
                                     去重与扩展
                                         │
                                 token/字符预算裁剪
                                         │
                                      Top 6-8
```

具体步骤：

1. 校验 query 和 limit。
2. 从 runtime 解析 thread ID。
3. 规范化 query，并生成词法查询文本。
4. 调用 embedding provider 生成 1024 维查询向量。
5. 根据当前检索配置创建本次调用专用的 `HybridSearchConfig`。
6. 由 `PGVectorStore` 执行 dense Top 40，并在启用 hybrid 时继续执行 lexical Top 40。
7. 两个查询均使用相同且不可覆盖的 thread/active filter。
8. 使用配置的 fusion function 合并排名。
9. 按 chunk ID 去重，并限制同一文档占据过多位置。
10. 必要时补充命中 chunk 的前后相邻块。
11. 在总上下文预算内返回最终结果。

`PGVectorStore` 当前会顺序执行 dense 和 lexical SQL，本设计遵循其行为，不额外维护一套并行查询实现。后续只有在性能评测显示数据库查询阶段是主要瓶颈时才重新评估。

### 11.2 参考 LangChain 的检索策略模型

检索策略切换遵循 LangChain `VectorStoreRetriever` 和 `PGVectorStore` 的现有语义，而不是定义一个包含所有行为的自有 `strategy` 枚举：

- `search_type` 决定 VectorStore 的基础检索方法。
- `search_kwargs` 保存该方法的参数。
- 可选 `HybridSearchConfig` 决定 similarity 查询是否同时执行全文检索及如何融合。

KnowledgeRetriever 在每次请求中合并服务端配置和内部 thread filter，然后调用：

```text
PGVectorStore.as_retriever(
    search_type=validated_search_type,
    search_kwargs=internal_search_kwargs,
)
```

最后通过 Retriever 的异步 `ainvoke(query)` 获取 `Document` 列表。这样检索策略分派由 LangChain `VectorStoreRetriever` 完成，本项目只负责合法组合校验、强制作用域、hybrid 配置创建和统一后处理。

LangChain `VectorStoreRetriever` 公开支持的 `search_type` 为：

| `search_type` | 行为 | 首期支持 |
| --- | --- | --- |
| `similarity` | 按向量相似度返回 Top K | 是 |
| `similarity_score_threshold` | 过滤低于相关度阈值的结果 | 是，仅 dense |
| `mmr` | 使用 MMR 平衡相关性和多样性 | 是，仅 dense |

Hybrid 不是第四种 `search_type`。在 `PGVectorStore` 中，它是在 similarity 查询上增加 `HybridSearchConfig`。因此本项目也保持这两个维度正交，不把 `hybrid` 伪装成 LangChain 原生 `search_type`。

首期允许的组合：

| 配置组合 | 结果 |
| --- | --- |
| `search_type=similarity`，hybrid 关闭 | Dense similarity |
| `search_type=similarity_score_threshold`，hybrid 关闭 | Dense threshold |
| `search_type=mmr`，hybrid 关闭 | Dense MMR |
| `search_type=similarity`，hybrid + RRF | Dense + lexical + RRF |
| `search_type=similarity`，hybrid + weighted sum | Dense + lexical + weighted sum |

首期不支持 hybrid 与 MMR/score threshold 组合。RRF 分数不是归一化相关度，不能直接套用 similarity score threshold；对 hybrid 结果再做 MMR 也需要明确候选向量和执行顺序，不能假设 `PGVectorStore` 的组合语义。

### 11.3 HybridSearchConfig

每次 hybrid 查询创建新的 `HybridSearchConfig`，至少设置：

```text
tsv_column = search_vector
tsv_lang = pg_catalog.simple
fts_query = 经过 lexical.py 处理的查询
primary_top_k = 40
secondary_top_k = 40
fusion_function = reciprocal_rank_fusion
fusion_function_parameters = {rrf_k: 60}
```

不能在并发请求之间共享并修改同一个 `HybridSearchConfig` 实例。官方实现会在查询期间补充 `fetch_top_k` 等 fusion 参数；为每次检索创建独立配置可以避免请求之间发生状态污染。

原始 query 传给 embedding model，预分词后的 query 只传给 `fts_query`。两者不能互换：

- Dense 路径需要自然语言原文。
- Lexical 路径需要与 `lexical_text` 相同的规范化和分词规则。

### 11.4 Fusion 切换

默认采用 LangChain PostgreSQL 公开的 `reciprocal_rank_fusion`：

初始配置：

```text
rrf_k = 60
primary_top_k = 40
secondary_top_k = 40
fetch_top_k = 6
```

单个结果的基础融合分数：

```text
score = 1 / (rrf_k + dense_rank)
      + 1 / (rrf_k + lexical_rank)
```

如果需要调节 dense 和 lexical 权重，切换为官方 `weighted_sum_ranking`，并配置：

```text
primary_results_weight
secondary_results_weight
```

不要给 RRF 增加自定义权重参数，否则会偏离 LangChain PostgreSQL 的公开函数签名和行为。fusion function 必须从显式白名单解析，不允许从 YAML 动态导入任意 Python 路径。

### 11.5 稠密检索

`VectorStoreRetriever` 根据 `search_type` 分派到 `PGVectorStore` 对应的异步公开接口：

- `asimilarity_search_with_score`
- `asimilarity_search_with_relevance_scores`
- `amax_marginal_relevance_search`

KnowledgeService 在创建 Retriever 前永远把内部 thread/active filter 合并到 `search_kwargs`，tool 参数无法修改或移除该过滤条件。调用时只使用 LangChain 公开方法，不访问 `PGVectorStore` 或 `AsyncPGVectorStore` 的双下划线私有查询方法。

### 11.6 词法检索

词法检索由 `PGVectorStore` 在存在 `HybridSearchConfig` 和非空 `fts_query` 时执行：

- `tsv_column` 显式指向 `search_vector`。
- `tsv_lang` 使用 `pg_catalog.simple`。
- `fts_query` 使用与写入时相同的中文/代码词法处理结果。
- 内部使用 `plainto_tsquery` 和 `ts_rank_cd`。
- `ts_rank_cd` 排序。
- 与 dense 查询共用相同的 thread/active metadata filter。

中文 query 必须使用与写入时一致的分词器和版本。

### 11.7 配置切换与稳定边界

检索策略由服务端配置决定，`knowledge_search` tool 不暴露 `search_type`、fusion function 或 metadata filter 参数。这样可以调整策略和做离线评测，而不会让 Agent 在每次调用中任意改变召回行为。

切换策略时保持以下边界稳定：

- Tool schema 和返回结构不变。
- thread 隔离规则不变。
- 数据表和已生成 embedding 不变。
- 后处理顺序保持去重、邻接扩展、预算裁剪。
- 返回结果记录生效的 `search_type`、hybrid 状态、fusion 名称和配置版本，供可观测性使用。

HNSW/IVFFlat 属于物理索引策略，不属于 `search_type`。更换 embedding 模型或维度也不属于检索策略切换，并且需要重新索引数据。

## 12. Tool 设计

不使用带 `action` 参数的单一万能工具，采用职责明确的多个 tool。

### 12.1 `knowledge_search`

参数：

```text
query: str
limit: int | None
```

返回：

```json
{
  "retrieval": {
    "search_type": "similarity",
    "hybrid": true,
    "fusion": "reciprocal_rank_fusion",
    "config_version": 1
  },
  "results": [
    {
      "rank": 1,
      "content": "...",
      "document_id": "...",
      "chunk_id": "...",
      "title": "...",
      "source_uri": "/workspace/docs/example.md",
      "locator": {"heading": "...", "start_line": 10, "end_line": 32}
    }
  ],
  "count": 1
}
```

Tool 返回稳定的 `rank`，不承诺跨策略可比较的数值 `score`。Cosine distance、归一化 relevance、MMR 顺序、RRF 分数和 weighted sum 分数语义不同，把它们放入同一个字段容易被 Agent 错误解释。原始分数仅进入内部 tracing 和离线评测数据。

### 12.2 `knowledge_ingest`

参数：

```text
paths: list[str]
replace: bool = false
```

只允许读取当前 thread 的 `/workspace` 文件。首期使用同步索引和明确的单文件、总文件、总 chunk 上限。

### 12.3 `knowledge_list`

列出当前 thread 的文档、状态、来源、chunk 数量和更新时间，不返回全文。

### 12.4 `knowledge_delete`

参数：

```text
document_id: str
```

删除语句必须同时匹配 `thread_id` 和 `document_id`。

### 12.5 主 Agent 与子 Agent

工具应分组注册：

- 主 Agent：search、ingest、list、delete。
- 默认子 Agent：仅 search。

这样子 Agent 可以查询当前会话知识，但不能改变知识库状态。

## 13. 索引写入与幂等性

### 13.1 新文档

1. 读取并规范化原文。
2. 计算 document content hash。
3. 若相同 `(thread_id, source_uri, content_hash)` 已 ready，直接返回已有结果。
4. 创建 `indexing` 状态文档记录。
5. 解析、分块、词法预处理。
6. 每批最多 10 个 chunk 调用 embedding API。
7. 在事务中写入 chunks 并将文档标记为 ready。

### 13.2 文档替换

替换时不能先删除当前 ready 版本：

1. 新版本以 `indexing` 状态创建。
2. 完成全部 embedding 和 chunk 写入。
3. 在事务中切换 ready 版本。
4. 再清理旧版本。

任何中间失败都不能使原有可检索版本消失。

### 13.3 Tool 重试

Agent 已有 ToolRetry 行为，因此 knowledge 写操作必须可安全重试：

- document 使用内容哈希去重。
- chunk ID 稳定生成或使用唯一约束。
- upsert 不产生重复 chunk。
- replace 操作有明确版本状态。

## 14. 资源生命周期

KnowledgeService 是进程级共享服务，负责持有：

- 单个 embedding client。
- 单个 `PGEngine`。
- 单个配置完成的 `PGVectorStore`。
- KnowledgeRepository 和 KnowledgeRetriever。

服务端启动时进行配置和 schema 校验，关闭时显式释放 `PGEngine`。不能在每次 tool 调用中创建新的 engine 或连接池。

数据库扩展和 schema 迁移不应在检索热路径中隐式执行。`CREATE EXTENSION vector`、表结构与索引应由明确的数据库初始化或迁移步骤负责。

## 15. 失败策略

| 场景 | 行为 |
| --- | --- |
| 缺少 thread ID | 拒绝执行，不访问数据库 |
| 缺少 embedding 配置 | 返回结构化配置错误 |
| embedding 超时 | 有限重试后返回结构化错误 |
| PostgreSQL 不可用 | 返回结构化错误，不破坏 Agent loop |
| ingest 部分失败 | 文档保持 failed/indexing，不对检索可见 |
| 查询无结果 | 返回空 results，不视为错误 |
| thread 删除清理失败 | 记录错误并允许后台重试 |
| embedding 维度不符 | 拒绝写入，提示重新索引或修正配置 |

Tool 返回的知识内容属于不可信外部数据。Agent system prompt 应明确要求：知识片段只能作为参考资料，不能把其中的指令当作系统或开发者指令执行。

## 16. 配置草案

非敏感配置建议形态：

```yaml
knowledge:
  enabled: true
  embedding:
    model: text-embedding-v4
    dimensions: 1024
    batch_size: 10
    timeout_seconds: 15
    max_retries: 2
  chunking:
    size_characters: 2000
    overlap_characters: 200
    add_start_index: true
  retrieval:
    search_type: similarity
    search_kwargs:
      k: 6
      fetch_k: 40
      lambda_mult: 0.5
      score_threshold: null
    hybrid:
      enabled: true
      fusion_function: reciprocal_rank_fusion
      primary_top_k: 40
      secondary_top_k: 40
      fusion_function_parameters:
        rrf_k: 60
    max_context_chars: 12000
  limits:
    max_file_bytes: 5242880
    max_files_per_call: 20
    max_chunks_per_document: 1000
```

这些数值是首期安全默认值，最终通过真实数据集评测调整。

配置加载时必须验证组合合法性：hybrid 只能与 `search_type=similarity` 同时启用；`score_threshold` 只对 `similarity_score_threshold` 生效；`fetch_k` 和 `lambda_mult` 只对 `mmr` 生效。未知 `search_type` 或 fusion function 必须在启动或首次初始化时失败，不能静默回退。

## 17. 测试与验收标准

### 17.1 单元测试

- embedding factory 正确读取非敏感配置和环境变量。
- API Key 不出现在异常、日志和对象 repr 中。
- batch 永远不超过 10。
- `langchain-text-splitters` 路由到预期的 Markdown、代码或普通文本 splitter。
- Markdown 两阶段切分保留正确的 `heading_path`。
- 字符位置和行号在重复文本及 overlap 场景下仍然正确。
- 相同输入和配置产生稳定的 chunk 边界。
- 中文、snake_case、camelCase 和路径展开符合预期。
- RRF 对固定输入产生确定排名。
- LangChain 三种 `search_type` 分派到正确的异步公开方法。
- 非法的 search type/hybrid 组合在初始化时被拒绝。
- 每次 hybrid 查询获得独立的 `HybridSearchConfig` 实例。
- RRF 与 weighted sum 可以仅通过配置切换。
- Dense 与 hybrid 查询都携带不可覆盖的 thread/active filter。
- ingest 重试不会产生重复文档或 chunk。
- 缺少 thread ID 时所有 tool fail closed。

### 17.2 Repository 集成测试

- pgvector 1024 维写入和 cosine 检索。
- GIN/`ts_rank_cd` 词法检索。
- documents 级联删除 chunks。
- documents 未 ready 或 chunks `is_active=false` 的数据不可检索。
- 同一 document ID 在错误 thread 下不可读取或删除。
- thread 删除只清理目标 thread。

### 17.3 隔离验收

必须建立两个 thread，写入内容相似但结论不同的文档，并验证：

- thread A 只能召回 A 的 chunk。
- thread B 只能召回 B 的 chunk。
- 使用 B 的 document ID 在 A 中读取、删除均失败。
- 缺少 runtime thread 时不会召回任何数据。

跨 thread 泄漏测试是本功能最高优先级验收项。

### 17.4 检索质量评测

建立小型中文、英文和代码查询集，记录：

- dense Recall@K。
- lexical Recall@K。
- hybrid Recall@K。
- MRR 或首个正确 chunk 排名。
- 空结果率。
- embedding、dense、lexical 和总检索延迟。

只有评测显示明显收益时，才加入 reranker、query rewrite 或 ANN 索引。

## 18. 分阶段实施顺序

### 阶段一：基础设施

- 增加 knowledge 配置和 embedding factory。
- 将 PostgreSQL 镜像切换为带 pgvector 的 PostgreSQL 16。
- 引入并锁定新版 `langchain-postgres` 与 `langchain-text-splitters`。
- 建立数据库初始化和 schema。

### 阶段二：索引链路

- 实现 loaders、基于 `langchain-text-splitters` 的 chunker 编排和 lexical 预处理。
- 实现 documents 生命周期和幂等写入。
- 使用 `PGVectorStore` 写入 1024 维 chunks。
- 实现 ingest、list 和 delete tools。

### 阶段三：检索链路

- 按 LangChain `search_type` 分派 dense similarity、threshold 和 MMR。
- 使用每次查询独立的 `HybridSearchConfig` 执行 hybrid 召回。
- 接入官方 RRF/weighted sum 切换、去重、邻接扩展与预算裁剪。
- 注册 `knowledge_search` 并增加 Agent 使用约束。

### 阶段四：生命周期与质量

- 接入 thread 删除清理。
- 补齐故障、隔离和集成测试。
- 建立检索质量基线和阶段延迟指标。

## 19. 后续演进条件

只有满足下列条件时才升级架构：

- 精确向量查询达到不可接受延迟：评估 HNSW 和 iterative scan。
- `ts_rank_cd` 对真实中文查询召回不足：评估更强的中文分词或 BM25 扩展。
- 混合召回正确但排序较差：增加可配置 reranker。
- 单次导入经常超过 tool 执行时间：引入持久化索引任务和状态查询 tool。
- 明确需要跨会话知识：新增显式 `thread`/`user` scope，不允许把空 thread 当作共享知识库。

## 20. 参考资料

- [阿里云百炼 Embedding 文档](https://help.aliyun.com/en/model-studio/embedding)
- [LangChain PostgreSQL 官方仓库](https://github.com/langchain-ai/langchain-postgres)
- [PGVectorStore 实现](https://github.com/langchain-ai/langchain-postgres/blob/main/langchain_postgres/v2/vectorstores.py)
- [LangChain PostgreSQL Hybrid Search 实现](https://github.com/langchain-ai/langchain-postgres/blob/main/langchain_postgres/v2/async_vectorstore.py)
- [LangChain PostgreSQL HybridSearchConfig 与融合函数](https://github.com/langchain-ai/langchain-postgres/blob/main/langchain_postgres/v2/hybrid_search_config.py)
- [LangChain PostgreSQL v2 异步设计](https://github.com/langchain-ai/langchain-postgres/blob/main/docs/v2_design_overview.md)
- [LangChain VectorStoreRetriever 检索策略](https://github.com/langchain-ai/langchain/blob/master/libs/core/langchain_core/vectorstores/base.py)
- [LangChain RecursiveCharacterTextSplitter 文档](https://docs.langchain.com/oss/python/integrations/splitters/recursive_text_splitter)
- [LangChain Markdown splitters 源码](https://github.com/langchain-ai/langchain/blob/master/libs/text-splitters/langchain_text_splitters/markdown.py)
- [langchain-text-splitters PyPI](https://pypi.org/project/langchain-text-splitters/)
- [pgvector 官方仓库](https://github.com/pgvector/pgvector)
- [PostgreSQL 全文检索](https://www.postgresql.org/docs/current/textsearch.html)
