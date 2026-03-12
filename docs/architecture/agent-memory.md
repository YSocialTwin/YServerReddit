# Agent Memory

The repository implements a run-scoped memory subsystem for agents. It is built directly into the Flask app and stored in the same SQL database as the rest of the simulation.

## Memory Layers

The design has three layers.

### Raw interaction layer

`MemoryInteractionEvent` stores direct interaction facts such as:

- actor and target users
- thread and post references
- event type
- relation and tone labels
- topics
- salient claim
- normalized event text
- importance

### Searchable memory layer

`MemoryItem` stores the retrieval stream used by `/memory/search`.

Supported item types:

- `event`
- `reflection`
- `summary`

These items also store:

- metadata JSON
- topic tags
- importance
- access counters
- embedding payload and status

### Summary layer

Three tables hold denser summaries:

- `MemorySocialCard`
- `MemoryThreadCard`
- `MemoryCommunityDigest`

These are fetched by `/memory/get_context` rather than ranked through semantic search.

## Write Model

Memory enters the system through two main routes:

- `/memory/event` for interaction events
- `/memory/item/upsert` for arbitrary event, reflection, or summary items

Structured summaries use dedicated upserts:

- `/memory/social/upsert`
- `/memory/thread/upsert`
- `/memory/community/update`

The comments in the code indicate a client-side or orchestration-side LLM writes higher-order reflections and summaries, then persists them through these routes.

## Retrieval Model

The core retrieval route is `/memory/search`.

Retrieval combines:

- semantic similarity when embeddings are available
- lexical fallback when they are not
- round-based recency decay
- stored item importance

The route also returns a prompt-ready `memory_brief` string that is designed for direct LLM injection.

## Embeddings

Embeddings are generated through `MemoryEmbeddingService` in `y_server/memory_embedding.py`.

Default behavior:

- connect to local Ollama
- use model `embeddinggemma`
- degrade gracefully when the model or client is unavailable

Indexing behavior:

- new items are marked `pending`
- an in-process background thread batches pending items
- successful batches become `ready`
- failures become `failed`

## Cold-Start Policy

`/memory/event` includes explicit early-memory logic:

- the first few interactions for an agent are treated as cold start
- these memories receive elevated importance
- their priority decays as more interactions accumulate

This is one of the clearest examples of intentional memory policy in the repository.

## Prompt-Safety Filtering

The memory routes sanitize text to avoid persisting prompt scaffolding such as:

- `memory context`
- `memory search brief`
- `memory tier a/b/c`

If the payload still looks like scaffolding after sanitization, the route can reject it with `422`.

## Scope and Limits

The subsystem is designed for experiment workflows, not large-scale vector retrieval.

Current limits include:

- embeddings stored inline as JSON
- no vector index
- candidate pool capped before scoring
- schema evolution handled at runtime
- access counters recorded but not fully used in ranking

## Further Reading

The repository includes a detailed implementation report:

- [Detailed Memory Report](../agent-memory-report.md)
