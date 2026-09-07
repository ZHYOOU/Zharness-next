# Session-scoped RAG Knowledge Base Design

English | [简体中文](rag-knowledge-base-design.zh-CN.md)

> Implementation status: the first-phase architecture is in place. Code lives in
> `src/zharness/knowledge/`, and the main Agent has registered
> `knowledge_search`, `knowledge_ingest`, `knowledge_list`, and `knowledge_delete`.
> The default sub-Agent only registers the search tool. This document also serves as
> the baseline for implementation constraints and future evolution.

## 1. Background and Goals

ZHarness needs to provide a RAG knowledge base capability that Agents invoke on demand. The knowledge base stores documents imported by the user within the current LangGraph session, recalls relevant fragments through lexical and vector retrieval, and returns source-attributed results to the Agent to produce the final answer.

This design follows these goals:

- The knowledge base is strictly bound to the LangGraph `thread_id`; different sessions cannot read, modify, or delete each other's data.
- Retrieval happens on demand in the form of an Agent tool, rather than automatically injecting knowledge before every model call.
- Use Alibaba Cloud Bailian `text-embedding-v4` to generate dense vectors.
- Use `langchain-text-splitters` for document chunking; do not maintain a custom chunking algorithm.
- Use PostgreSQL to hold document metadata, text indexes, and vector indexes at the same time.
- Use hybrid retrieval combining dense retrieval and lexical retrieval, fused and ranked with RRF.
- Reference LangChain's open-source `langchain-postgres` integration, preferring to reuse its connection management, vector store interfaces, and async capabilities.
- Prioritize personal single-user, session-scoped knowledge base needs; do not add complexity for multi-tenant or multi-instance deployment.

## 2. Non-Goals

The first phase does not include the following capabilities:

- Cross-session shared knowledge bases or user-level public knowledge bases.
- Automatic extraction of knowledge from conversations.
- OCR, image, audio/video, and complex-layout parsing.
- Web scraping and automatic indexing of remote URLs.
- Approximate vector indexes such as HNSW and IVFFlat.
- LLM query rewriting, HyDE, or rerankers.
- Large-scale async indexing task queues.
- A frontend knowledge base management page.

## 3. Core Design Decisions

### 3.1 Separation from Long-Term Memory

The knowledge base is implemented as an independent `knowledge` domain and does not reuse the tables, repositories, or middleware of `memory`.

The semantics of the two differ:

| Capability | `memory` | `knowledge` |
| --- | --- | --- |
| Content | User preferences, constraints, decisions, and long-term facts | Documents and document fragments |
| Default scope | Single user, cross-session | Single `thread_id` |
| Write method | Conversation extraction and memory tool | Explicit file or text import by the user |
| Recall method | Fact search and hidden context | Hybrid retrieval tool |
| Source requirement | May lack document location | Must return citable sources |
| Lifecycle | Long-term retention | Deleted together with the thread |

### 3.2 PostgreSQL and pgvector

Continue using the project's existing PostgreSQL; do not add a Qdrant or Elasticsearch service. The hosted PostgreSQL image must include the `vector` extension, and the database initialization stage is responsible for enabling the extension.

PostgreSQL is responsible for:

- Document and chunk metadata storage.
- `tsvector`/GIN lexical retrieval.
- pgvector cosine distance dense retrieval.
- Thread filtering and document lifecycle transactions.

### 3.3 Use the Newer LangChain PostgreSQL Integration

Adopt the `PGEngine` and `PGVectorStore` of the newer `langchain-postgres`; do not use the deprecated legacy `PGVector`.

Scope of reuse:

- Async connection management of `PGEngine`.
- Field mapping of `PGVectorStore` onto existing tables.
- Document vector write, update, and dense retrieval interfaces.
- Mature implementations such as metadata filters and cosine distance.
- The `search_type` and `search_kwargs` semantics of `VectorStoreRetriever`.
- `HybridSearchConfig`, `reciprocal_rank_fusion`, and `weighted_sum_ranking`.

Its default tables are not used directly because this project needs:

- A dedicated documents table and thread lifecycle.
- Mandatory, non-overridable `thread_id` filtering.
- Pre-tokenized text specialized for Chinese and code.
- Separate `content` and `lexical_text` columns.
- Fine-grained source, section, page, or line number information.

Hybrid retrieval uses `PGVectorStore`'s built-in hybrid query, but explicitly points `tsv_column` at this project's pre-generated `search_vector` and passes the query processed by the same Chinese tokenization rules as `fts_query`. This reuses the official implementation without applying a default tokenization that is a poor fit for Chinese directly to the raw `content`.

`PGVectorStore` must be encapsulated inside KnowledgeService. Tools and Agents must not access the raw vector store directly, nor construct metadata filters on their own.

## 4. Overall Architecture

```text
                         ┌───────────────────────────┐
 current thread workspace ──▶│ knowledge_ingest          │
                         │ - get runtime thread_id   │
                         │ - parse and structure chunks │
                         │ - generate lexical_text   │
                         │ - batch generate embeddings│
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
                               ├─ dedup and adjacent-chunk expansion
                               └─ return content and sources
```

## 5. Module Boundaries

The following package structure is proposed:

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

Responsibilities are as follows:

- `types.py`: types for documents, chunks, retrieval results, and indexing status.
- `loaders.py`: read and normalize supported file formats.
- `chunking.py`: configure and orchestrate `langchain-text-splitters`, and add locator metadata.
- `lexical.py`: Chinese tokenization and code identifier expansion.
- `embeddings.py`: create and validate the embedding provider.
- `repository.py`: documents table, thread cleanup, and indexing transactions.
- `retrieval.py`: retrieval config parsing, policy validation, post-processing, and budget trimming, following LangChain.
- `service.py`: indexing orchestration, idempotency control, and process-level resource lifecycle.
- `tools.py`: ToolRuntime thread resolution, tool argument validation, and result serialization.

## 6. Embedding Design

### 6.1 Model and Interface

Use `langchain_openai.OpenAIEmbeddings` to call the Alibaba Cloud OpenAI-compatible embeddings API.

Fixed defaults:

| Config | Value | Description |
| --- | --- | --- |
| model | `text-embedding-v4` | Alibaba text vector model |
| dimensions | `1024` | Explicitly fixes the vector dimension for both table and request |
| batch_size | `10` | Does not exceed the model's per-request item limit |
| timeout_seconds | `15` | Consistent with the project's external network request policy |
| max_retries | `2` | Limited retries for transient network errors |
| check_embedding_ctx_length | `false` | Length control is the responsibility of this project's chunker |

Alibaba's official docs show that `text-embedding-v4` supports custom dimensions, and 1024 dimensions is the recommended balance for general retrieval scenarios. Passing the dimension explicitly prevents changes to the server-side default from making writes to existing tables fail.

### 6.2 Environment Variables

Sensitive configuration is only read from the environment:

- `EMBEDDING_BASE_URL`
- `EMBEDDING_API_KEY`

`EMBEDDING_BASE_URL` must be an OpenAI-compatible API root path, for example ending in `/compatible-mode/v1`, not a full `/embeddings` request URL. Verify that the variables exist at startup or first use, but never log the API Key, and do not log URLs that contain sensitive query parameters.

Non-sensitive configuration goes into the `knowledge.embedding` section of `config.yaml` and may be overridden by `ZHARNESS_KNOWLEDGE_*` environment variables.

### 6.3 Provider Abstraction

KnowledgeService depends only on the LangChain `Embeddings` interface, with instances created through a separate factory. Tests use fake embeddings and make no real network requests.

The first phase uses the OpenAI-compatible interface and does not implement the DashScope native interface. Therefore, the `text_type=query/document` optimization, available only in the DashScope native API, is not used for now.

## 7. Data Model

### 7.1 Documents Table

Table name: `zharness_knowledge_documents`

| Field | Type | Description |
| --- | --- | --- |
| `id` | UUID | Document ID |
| `thread_id` | TEXT NOT NULL | LangGraph session ID |
| `source_type` | TEXT NOT NULL | `workspace_file` or `text` in the first phase |
| `source_uri` | TEXT | Workspace virtual path or logical source |
| `title` | TEXT NOT NULL | Display name |
| `mime_type` | TEXT | File type |
| `content_hash` | TEXT NOT NULL | SHA-256 of the normalized source text |
| `status` | TEXT NOT NULL | `indexing`, `ready`, or `failed` |
| `chunk_count` | INTEGER NOT NULL | Number of searchable chunks |
| `embedding_model` | TEXT NOT NULL | Model used at write time |
| `embedding_dimensions` | INTEGER NOT NULL | Vector dimension at write time |
| `index_version` | INTEGER NOT NULL | Chunking or retrieval strategy version |
| `error` | TEXT | Summary of the most recent indexing failure |
| `created_at` | TIMESTAMPTZ | Creation time |
| `updated_at` | TIMESTAMPTZ | Update time |

The unique constraint covers at least `(thread_id, source_uri, content_hash)` to enable idempotent imports within the same session.

### 7.2 Chunks Table

Table name: `zharness_knowledge_chunks`

| Field | Type | Description |
| --- | --- | --- |
| `id` | TEXT or UUID | Stable chunk ID |
| `thread_id` | TEXT NOT NULL | Redundant, to allow mandatory filtering |
| `document_id` | UUID NOT NULL | Foreign key to documents, cascade delete |
| `is_active` | BOOLEAN NOT NULL | Whether the current document version may participate in retrieval |
| `ordinal` | INTEGER NOT NULL | Order within the document |
| `content` | TEXT NOT NULL | Raw chunk content |
| `embedding` | vector(1024) NOT NULL | Dense vector |
| `lexical_text` | TEXT NOT NULL | Text after tokenization and identifier expansion |
| `search_vector` | TSVECTOR NOT NULL | Lexical retrieval vector |
| `title` | TEXT | Document title |
| `source_uri` | TEXT | Source path |
| `heading_path` | JSONB | Heading hierarchy |
| `locator` | JSONB | Locator info such as page, line, and paragraph |
| `token_count` | INTEGER | Chunk size |
| `content_hash` | TEXT NOT NULL | Chunk content hash |

`search_vector` is recommended as a stored generated column:

```sql
GENERATED ALWAYS AS (
    to_tsvector('simple', lexical_text)
) STORED
```

Initial indexes:

- `BTREE(thread_id, is_active)`
- `BTREE(thread_id, document_id)`
- `UNIQUE(document_id, ordinal)`
- `GIN(search_vector)`

No HNSW is created in the first phase. When session-scoped data is small, narrowing the range by `thread_id` first and then running an exact cosine distance sort yields stable recall. Approximate indexes are only added when real data and benchmarks prove that exact queries do not meet latency targets.

`is_active` is redundantly stored in the chunks table so that `PGVectorStore` can constrain both the thread and the visible version through its public metadata filter, without relying on a custom private query that joins the documents table. When switching to a new version, the new chunks are activated and the old chunks are deactivated in the same transaction.

## 8. Session Isolation

### 8.1 Thread ID Source

All knowledge tools obtain the session ID from `ToolRuntime.execution_info.thread_id`. `thread_id` does not appear in the tool schema; neither the LLM nor the user can pass in or override it.

When a valid thread ID is missing, the call must fail closed:

```json
{
  "error": "knowledge tool requires an active thread"
}
```

There must be no fallback to a global knowledge base, and no use of an empty string or `NULL` as a shared scope.

### 8.2 Repository Constraints

Every public read/write method of the Repository must require a non-empty `thread_id: str` parameter, for example:

```text
search(thread_id, query, limit)
ingest(thread_id, ...)
list_documents(thread_id)
delete_document(thread_id, document_id)
delete_thread(thread_id)
```

All dense, lexical, read, update, and delete SQL must include a thread condition. Even if a caller knows the `document_id` of another session, it must not be able to read or delete the corresponding data.

### 8.3 Thread Deletion

After a LangGraph thread is deleted successfully, run thread resource cleanup uniformly:

- Delete the documents of that thread; chunks are deleted through the foreign key cascade.
- Clean up the sandbox/container of that thread.
- Keep failure logs and allow the leftover data to be cleaned up again in the background.

A knowledge base cleanup failure must not turn an already-successful LangGraph DELETE response into a failure, but it must be logged and retryable.

## 9. Document Loading and Chunking

### 9.1 First-Phase Formats

The first phase supports:

- Plain text.
- Markdown.
- Common source code and config files.
- Short text submitted directly through a tool.

PDF, Office, images, and remote URLs are not supported for now.

### 9.2 Workspace Boundaries

`knowledge_ingest` accepts `/workspace` virtual paths and may only read files through the sandbox/workspace abstraction corresponding to the current thread. Virtual paths must not be treated directly as host absolute paths, and existing workspace boundaries must not be bypassed.

### 9.3 Chunking Dependencies and Principles

Document chunking uniformly uses the standalone `langchain-text-splitters` package. It must be installed and locked as a direct dependency of ZHarness through `uv`; it must not rely only on a transitive dependency of `langchain`.

The first phase does not implement custom chunking algorithms and does not use the semantic splitters from `langchain-experimental`. `chunking.py` is only responsible for:

- Selecting a stable public splitter per document type.
- Configuring separator, chunk size, and overlap.
- Passing loader metadata to the LangChain `Document`.
- Converting splitter results into this project's KnowledgeChunk.
- Adding locators such as character positions, line numbers, and heading paths.

### 9.4 Splitter Routing

Different types use the following combinations:

| Document type | First-level splitter | Length fallback splitter |
| --- | --- | --- |
| Markdown | `MarkdownHeaderTextSplitter` | `RecursiveCharacterTextSplitter` |
| Supported source code | `RecursiveCharacterTextSplitter.from_language(...)` | The same recursive splitter |
| Plain text and unknown text | None | `RecursiveCharacterTextSplitter` |

Markdown uses a two-stage split:

1. Use `MarkdownHeaderTextSplitter` to extract the heading hierarchy by `#` through `######`.
2. Keep the heading text so that standalone chunks still have enough semantic context.
3. Normalize heading metadata into `heading_path`.
4. Split sections that exceed the limit again with `RecursiveCharacterTextSplitter`.

Source code prefers the language separators provided by `RecursiveCharacterTextSplitter.from_language(...)`. The first phase maps at least Python, JavaScript/TypeScript, Go, Rust, Java, C/C++, and Markdown; unrecognized extensions fall back to the plain-text policy. "Code-aware" here is based on LangChain separators, not AST-based semantic splitting.

Plain text uses `RecursiveCharacterTextSplitter`, with the separator order covering:

```text
paragraph boundary → newline → Chinese sentence-ending punctuation → English sentence-ending punctuation → space → character
```

Separators must be configured explicitly and must not rely on library defaults, to keep behavior stable for mixed Chinese/English documents.

### 9.5 Size Measurement

The first phase uses character count rather than token count as the splitter's length unit:

```text
chunk_size = 2000 characters
chunk_overlap = 200 characters
```

The reason is that `RecursiveCharacterTextSplitter` natively measures in characters by default, and `text-embedding-v4` has no official local tokenizer that the component could use directly. Estimating an Alibaba model with the OpenAI tokenizer would introduce unnecessary coupling and error.

2000 characters is well below the embedding per-input limit, while for English text it stays roughly within the common RAG chunk size range. This value is the first-phase baseline and will be tuned later with Chinese, English, and code evaluation sets.

The embedding client must still reject input that exceeds the model's limit; the splitter config must not be treated as the ultimate safety boundary.

### 9.6 Locators and Metadata

All splitter inputs use `langchain_core.documents.Document` and carry at least:

- `source_uri`
- `document_id`
- `mime_type`
- `language`

Enable `add_start_index=True` for `RecursiveCharacterTextSplitter` to record each chunk's starting character position in the input text. The relative positions produced by the two-stage Markdown split must be mapped back to absolute positions in the source text by `chunking.py`.

Before splitting, the loader builds an index of newline positions and uses the final chunks' absolute character ranges to compute:

- `start_char`
- `end_char`
- `start_line`
- `end_line`
- `heading_path`

Position mapping must do a monotonic lookup in source-text order to handle repeated paragraphs and overlap correctly. Locator info is a metadata enhancement applied after splitting; it does not alter the boundary results of the LangChain splitters on its own.

## 10. Lexical Text Generation

`content` preserves the source text and is used for embedding and returned to the Agent; `lexical_text` serves keyword retrieval only.

`lexical_text` processing rules:

- Apply application-level tokenization for Chinese.
- Normalize English words to a consistent case form.
- Keep both full code identifiers and their split results.
- Split `snake_case`, `camelCase`, PascalCase, and dot-separated paths.
- Keep file names, extensions, class names, function names, and config keys.
- Add titles and source paths as configurable weighted terms.

For example:

```text
MemoryRepository.search_facts
```

can be expanded to:

```text
MemoryRepository search_facts memory repository search facts
```

Lexical preprocessing must be deterministic and carry an `index_version`. Incompatible rule changes trigger re-indexing through a version bump.

## 11. Hybrid Retrieval

### 11.1 Query Flow

```text
query
  ├─ normalize + lexical tokenize ──▶ lexical Top 40
  └─ text-embedding-v4 ─────────────▶ dense Top 40
                                         │
                         thread filter ──┤
                                         ▼
                                  configured fusion
                                         │
                                     dedup and expansion
                                         │
                                  token/character budget trimming
                                         │
                                      Top 6-8
```

Concrete steps:

1. Validate the query and limit.
2. Resolve the thread ID from the runtime.
3. Normalize the query and generate the lexical query text.
4. Call the embedding provider to generate the 1024-dimension query vector.
5. Create a `HybridSearchConfig` dedicated to this call based on the current retrieval config.
6. Have `PGVectorStore` run dense Top 40, and continue with lexical Top 40 when hybrid is enabled.
7. Both queries use the same, non-overridable thread/active filter.
8. Merge the rankings with the configured fusion function.
9. Deduplicate by chunk ID and limit how many positions a single document may occupy.
10. Supplement adjacent chunks around hit chunks when necessary.
11. Return the final results within the total context budget.

`PGVectorStore` currently runs the dense and lexical SQL sequentially; this design follows that behavior and does not maintain a separate parallel query implementation. This will be revisited later only if performance evaluation shows that the database query stage is the primary bottleneck.

### 11.2 Follow LangChain's Retrieval Strategy Model

Retrieval strategy switching follows the existing semantics of the LangChain `VectorStoreRetriever` and `PGVectorStore`, rather than defining a proprietary `strategy` enum that holds all behaviors:

- `search_type` determines the VectorStore's base retrieval method.
- `search_kwargs` holds the parameters for that method.
- The optional `HybridSearchConfig` determines whether a similarity query also runs full-text search and how the results are fused.

On each request, KnowledgeRetriever merges server-side config with the internal thread filter, then calls:

```text
PGVectorStore.as_retriever(
    search_type=validated_search_type,
    search_kwargs=internal_search_kwargs,
)
```

Finally, it obtains the `Document` list through the Retriever's async `ainvoke(query)`. This way retrieval strategy dispatch is handled by the LangChain `VectorStoreRetriever`, and this project only validates legal combinations, enforces scope, creates hybrid config, and applies uniform post-processing.

The `search_type` values publicly supported by the LangChain `VectorStoreRetriever` are:

| `search_type` | Behavior | First-phase support |
| --- | --- | --- |
| `similarity` | Return Top K by vector similarity | Yes |
| `similarity_score_threshold` | Filter out results below a relevance threshold | Yes, dense only |
| `mmr` | Use MMR to balance relevance and diversity | Yes, dense only |

Hybrid is not a fourth `search_type`. In `PGVectorStore`, it is a `HybridSearchConfig` layered onto a similarity query. Therefore this project also keeps these two dimensions orthogonal and does not disguise `hybrid` as a native LangChain `search_type`.

Allowed first-phase combinations:

| Config combination | Result |
| --- | --- |
| `search_type=similarity`, hybrid off | Dense similarity |
| `search_type=similarity_score_threshold`, hybrid off | Dense threshold |
| `search_type=mmr`, hybrid off | Dense MMR |
| `search_type=similarity`, hybrid + RRF | Dense + lexical + RRF |
| `search_type=similarity`, hybrid + weighted sum | Dense + lexical + weighted sum |

The first phase does not support combining hybrid with MMR/score threshold. RRF scores are not normalized relevance and cannot be plugged directly into a similarity score threshold; applying MMR to hybrid results would also require explicit candidate vectors and execution order, and the combination semantics of `PGVectorStore` must not be assumed.

### 11.3 HybridSearchConfig

Each hybrid query creates a new `HybridSearchConfig` that sets at least:

```text
tsv_column = search_vector
tsv_lang = pg_catalog.simple
fts_query = the query processed by lexical.py
primary_top_k = 40
secondary_top_k = 40
fusion_function = reciprocal_rank_fusion
fusion_function_parameters = {rrf_k: 60}
```

The same `HybridSearchConfig` instance must not be shared and mutated across concurrent requests. The official implementation adds fusion parameters such as `fetch_top_k` during the query; creating an independent config for each retrieval prevents state pollution between requests.

The raw query goes to the embedding model, and only the pre-tokenized query goes to `fts_query`. The two must not be swapped:

- The dense path needs the natural-language source text.
- The lexical path needs the same normalization and tokenization rules as `lexical_text`.

### 11.4 Fusion Switching

The default is LangChain PostgreSQL's public `reciprocal_rank_fusion`:

Initial config:

```text
rrf_k = 60
primary_top_k = 40
secondary_top_k = 40
fetch_top_k = 6
```

Base fusion score for a single result:

```text
score = 1 / (rrf_k + dense_rank)
      + 1 / (rrf_k + lexical_rank)
```

If the dense and lexical weights need to be tuned, switch to the official `weighted_sum_ranking` and configure:

```text
primary_results_weight
secondary_results_weight
```

Do not add custom weight parameters to RRF; that would deviate from LangChain PostgreSQL's public function signature and behavior. The fusion function must be resolved from an explicit whitelist; arbitrary Python paths must not be imported dynamically from YAML.

### 11.5 Dense Retrieval

`VectorStoreRetriever` dispatches to the corresponding async public interface of `PGVectorStore` based on `search_type`:

- `asimilarity_search_with_score`
- `asimilarity_search_with_relevance_scores`
- `amax_marginal_relevance_search`

Before creating the Retriever, KnowledgeService always merges the internal thread/active filter into `search_kwargs`; tool arguments cannot modify or remove that filter. Calls use only LangChain public methods and never touch the double-underscore private query methods of `PGVectorStore` or `AsyncPGVectorStore`.

### 11.6 Lexical Retrieval

Lexical retrieval is executed by `PGVectorStore` when a `HybridSearchConfig` is present along with a non-empty `fts_query`:

- `tsv_column` points explicitly at `search_vector`.
- `tsv_lang` uses `pg_catalog.simple`.
- `fts_query` uses the same Chinese/code lexical processing as at write time.
- `plainto_tsquery` and `ts_rank_cd` are used internally.
- Ranking by `ts_rank_cd`.
- Shares the same thread/active metadata filter as the dense query.

Chinese queries must use the same tokenizer and version as at write time.

### 11.7 Config Switching and Stable Boundaries

The retrieval strategy is determined by server-side config; the `knowledge_search` tool does not expose `search_type`, fusion function, or metadata filter arguments. This allows the strategy to be tuned and evaluated offline without letting the Agent arbitrarily change recall behavior on every call.

Keep the following boundaries stable when switching strategies:

- The tool schema and return structure stay unchanged.
- Thread isolation rules stay unchanged.
- Data tables and generated embeddings stay unchanged.
- Post-processing order stays dedup, adjacent expansion, budget trimming.
- Results record the effective `search_type`, hybrid state, fusion name, and config version for observability.

HNSW/IVFFlat are physical indexing strategies and are not `search_type`. Changing the embedding model or dimension is also not a retrieval strategy switch, and it requires re-indexing the data.

## 12. Tool Design

Instead of a single all-purpose tool with an `action` parameter, use multiple tools with distinct responsibilities.

### 12.1 `knowledge_search`

Parameters:

```text
query: str
limit: int | None
```

Returns:

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

The tool returns a stable `rank` and does not promise a `score` that is comparable across strategies. Cosine distance, normalized relevance, MMR ordering, RRF scores, and weighted sum scores have different semantics; putting them in a single field makes them easy for the Agent to misinterpret. Raw scores go only into internal tracing and offline evaluation data.

### 12.2 `knowledge_ingest`

Parameters:

```text
paths: list[str]
replace: bool = false
```

Only files under the current thread's `/workspace` may be read. The first phase uses synchronous indexing with explicit per-file, total-file, and total-chunk limits.

### 12.3 `knowledge_list`

Lists the current thread's documents, statuses, sources, chunk counts, and update times; does not return full text.

### 12.4 `knowledge_delete`

Parameters:

```text
document_id: str
```

The delete statement must match both `thread_id` and `document_id`.

### 12.5 Main Agent and Sub-Agents

Tools should be registered in groups:

- Main Agent: search, ingest, list, delete.
- Default sub-Agent: search only.

This lets sub-Agents query the current session's knowledge without being able to change the knowledge base state.

## 13. Index Writes and Idempotency

### 13.1 New Documents

1. Read and normalize the source text.
2. Compute the document content hash.
3. If the same `(thread_id, source_uri, content_hash)` is already ready, return the existing result directly.
4. Create a document record with `indexing` status.
5. Parse, chunk, and lexically preprocess.
6. Call the embedding API with at most 10 chunks per batch.
7. Write the chunks and mark the document ready in a transaction.

### 13.2 Document Replacement

When replacing, the current ready version must not be deleted first:

1. The new version is created with `indexing` status.
2. Complete all embedding and chunk writes.
3. Switch the ready version in a transaction.
4. Clean up the old version afterwards.

No intermediate failure may make the previously searchable version disappear.

### 13.3 Tool Retries

The Agent already has ToolRetry behavior, so knowledge write operations must be safely retryable:

- Documents are deduplicated by content hash.
- Chunk IDs are generated stably or use unique constraints.
- Upserts do not produce duplicate chunks.
- Replace operations have explicit version status.

## 14. Resource Lifecycle

KnowledgeService is a process-level shared service responsible for holding:

- A single embedding client.
- A single `PGEngine`.
- A single fully configured `PGVectorStore`.
- KnowledgeRepository and KnowledgeRetriever.

Config and schema validation run at server startup, and `PGEngine` is explicitly released at shutdown. A new engine or connection pool must not be created on every tool call.

Database extensions and schema migrations must not be executed implicitly in the retrieval hot path. `CREATE EXTENSION vector`, table structures, and indexes should be handled by explicit database initialization or migration steps.

## 15. Failure Strategy

| Scenario | Behavior |
| --- | --- |
| Missing thread ID | Refuse to run; do not access the database |
| Missing embedding config | Return a structured config error |
| Embedding timeout | Return a structured error after limited retries |
| PostgreSQL unavailable | Return a structured error; do not break the Agent loop |
| Partial ingest failure | Document stays `failed`/`indexing`, not visible to retrieval |
| Query returns no results | Return empty results; not treated as an error |
| Thread deletion cleanup failure | Log the error and allow background retry |
| Embedding dimension mismatch | Refuse the write; suggest re-indexing or fixing the config |

Knowledge content returned by tools is untrusted external data. The Agent system prompt should state clearly: knowledge fragments may only serve as reference material, and instructions inside them must not be executed as system or developer instructions.

## 16. Draft Config

Suggested shape for non-sensitive config:

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

These values are first-phase safe defaults and will eventually be tuned against real datasets.

Config loading must validate combination legality: hybrid can only be enabled together with `search_type=similarity`; `score_threshold` only takes effect for `similarity_score_threshold`; `fetch_k` and `lambda_mult` only take effect for `mmr`. Unknown `search_type` or fusion functions must fail at startup or first initialization, never silently fall back.

## 17. Testing and Acceptance Criteria

### 17.1 Unit Tests

- The embedding factory correctly reads non-sensitive config and environment variables.
- The API Key does not appear in exceptions, logs, or object reprs.
- Batches never exceed 10.
- `langchain-text-splitters` routes to the expected Markdown, code, or plain-text splitter.
- The two-stage Markdown split preserves the correct `heading_path`.
- Character positions and line numbers stay correct with repeated text and overlap.
- The same input and config produce stable chunk boundaries.
- Chinese, `snake_case`, `camelCase`, and path expansion behave as expected.
- RRF produces deterministic rankings for fixed input.
- LangChain's three `search_type` values dispatch to the correct async public methods.
- Illegal search type/hybrid combinations are rejected at initialization.
- Each hybrid query gets an independent `HybridSearchConfig` instance.
- RRF and weighted sum can be switched purely through config.
- Dense and hybrid queries both carry a non-overridable thread/active filter.
- Ingest retries do not produce duplicate documents or chunks.
- All tools fail closed when the thread ID is missing.

### 17.2 Repository Integration Tests

- pgvector 1024-dimension writes and cosine retrieval.
- GIN/`ts_rank_cd` lexical retrieval.
- Documents cascade-delete chunks.
- Data from documents that are not `ready` or chunks with `is_active=false` is not retrievable.
- The same document ID cannot be read or deleted under the wrong thread.
- Thread deletion only cleans up the target thread.

### 17.3 Isolation Acceptance

Two threads must be created, each ingesting documents with similar content but different conclusions, and verified:

- Thread A can only recall A's chunks.
- Thread B can only recall B's chunks.
- Using B's document ID to read or delete in A fails.
- No data is recalled when the runtime thread is missing.

The cross-thread leakage test is the highest-priority acceptance item for this feature.

### 17.4 Retrieval Quality Evaluation

Build small Chinese, English, and code query sets and record:

- Dense Recall@K.
- Lexical Recall@K.
- Hybrid Recall@K.
- MRR or the rank of the first correct chunk.
- Empty-result rate.
- Embedding, dense, lexical, and total retrieval latency.

Only add a reranker, query rewrite, or ANN index when evaluation shows a clear benefit.

## 18. Phased Implementation Order

### Phase One: Infrastructure

- Add the knowledge config and embedding factory.
- Switch the PostgreSQL image to PostgreSQL 16 with pgvector.
- Introduce and lock the newer `langchain-postgres` and `langchain-text-splitters`.
- Set up database initialization and schema.

### Phase Two: Indexing Pipeline

- Implement loaders, chunker orchestration based on `langchain-text-splitters`, and lexical preprocessing.
- Implement the documents lifecycle and idempotent writes.
- Write 1024-dimension chunks using `PGVectorStore`.
- Implement the ingest, list, and delete tools.

### Phase Three: Retrieval Pipeline

- Dispatch dense similarity, threshold, and MMR per LangChain `search_type`.
- Run hybrid recall with a per-query `HybridSearchConfig`.
- Integrate the official RRF/weighted sum switch, dedup, adjacent expansion, and budget trimming.
- Register `knowledge_search` and add Agent usage constraints.

### Phase Four: Lifecycle and Quality

- Wire up thread deletion cleanup.
- Fill in failure, isolation, and integration tests.
- Establish a retrieval quality baseline and phased latency metrics.

## 19. Future Evolution Triggers

Only upgrade the architecture when the following conditions are met:

- Exact vector queries reach unacceptable latency: evaluate HNSW and iterative scan.
- `ts_rank_cd` recall is insufficient for real Chinese queries: evaluate stronger Chinese tokenization or a BM25 extension.
- Hybrid recall is correct but ranking is poor: add a configurable reranker.
- A single import frequently exceeds tool execution time: introduce a persistent indexing task and a status-query tool.
- Cross-session knowledge is explicitly needed: add an explicit `thread`/`user` scope, and do not treat an empty thread as a shared knowledge base.

## 20. References

- [Alibaba Cloud Bailian Embedding docs](https://help.aliyun.com/en/model-studio/embedding)
- [LangChain PostgreSQL official repository](https://github.com/langchain-ai/langchain-postgres)
- [PGVectorStore implementation](https://github.com/langchain-ai/langchain-postgres/blob/main/langchain_postgres/v2/vectorstores.py)
- [LangChain PostgreSQL Hybrid Search implementation](https://github.com/langchain-ai/langchain-postgres/blob/main/langchain_postgres/v2/async_vectorstore.py)
- [LangChain PostgreSQL HybridSearchConfig and fusion functions](https://github.com/langchain-ai/langchain-postgres/blob/main/langchain_postgres/v2/hybrid_search_config.py)
- [LangChain PostgreSQL v2 async design](https://github.com/langchain-ai/langchain-postgres/blob/main/docs/v2_design_overview.md)
- [LangChain VectorStoreRetriever retrieval strategy](https://github.com/langchain-ai/langchain/blob/master/libs/core/langchain_core/vectorstores/base.py)
- [LangChain RecursiveCharacterTextSplitter docs](https://docs.langchain.com/oss/python/integrations/splitters/recursive_text_splitter)
- [LangChain Markdown splitters source](https://github.com/langchain-ai/langchain/blob/master/libs/text-splitters/langchain_text_splitters/markdown.py)
- [langchain-text-splitters PyPI](https://pypi.org/project/langchain-text-splitters/)
- [pgvector official repository](https://github.com/pgvector/pgvector)
- [PostgreSQL full-text search](https://www.postgresql.org/docs/current/textsearch.html)
