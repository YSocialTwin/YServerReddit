# Agent Memory Implementation Report

## Scope

This report analyzes the agent memory implementation in the repository, with focus on:

- persistent schema and storage model
- write paths and update flows
- embedding and indexing behavior
- retrieval and prompt-context assembly
- safety guards and operational behavior
- architectural limitations and risks

Primary implementation files:

- `y_server/modals.py`
- `y_server/memory_embedding.py`
- `y_server/routes/content_management.py`

## Executive Summary

The repository implements a run-scoped agent memory subsystem directly inside the Flask server. It is a hybrid design:

- raw interaction events are stored in `memory_interaction_events`
- searchable memory stream entries are stored in `memory_items`
- higher-level summaries are stored separately as social cards, thread cards, and a run-level community digest
- semantic retrieval is attempted through Ollama embeddings, with automatic fallback to lexical matching when embeddings are unavailable

The design is pragmatic and self-contained. It can work without migrations and can tolerate missing embedding infrastructure. The tradeoff is that memory policy, storage lifecycle, retrieval logic, and schema evolution are all implemented inside one large route module, which makes the system operationally convenient but structurally brittle.

## Source Map

### Data model

- `y_server/modals.py`
  - `MemoryInteractionEvent`
  - `MemorySocialCard`
  - `MemoryThreadCard`
  - `MemoryCommunityDigest`
  - `MemoryItem`

### Embeddings

- `y_server/memory_embedding.py`
  - `MemoryEmbeddingService`
  - `cosine_similarity`
  - `lexical_relevance`

### Memory orchestration, API surface, schema creation, retrieval

- `y_server/routes/content_management.py`

## High-Level Architecture

The implementation separates memory into two layers.

### 1. Event log layer

`MemoryInteractionEvent` stores direct interaction facts for a run:

- who acted
- who was targeted
- which thread/post was involved
- what type of interaction happened
- relation/tone labels
- topics
- salient claim
- importance
- generated or normalized event text

This is the closest thing to episodic memory.

### 2. Searchable memory layer

`MemoryItem` stores the search/index representation used by retrieval. These items can be:

- `event`
- `reflection`
- `summary`

This layer adds:

- searchable text
- metadata JSON
- topic tags
- importance
- round-based recency anchors
- access bookkeeping
- embedding payload and status

The implementation effectively treats `memory_items` as the canonical retrieval stream.

### 3. Structured summary layer

Three additional tables store denser summaries:

- `MemorySocialCard`: per agent, per other user
- `MemoryThreadCard`: per agent, per thread
- `MemoryCommunityDigest`: per run

These are not searched semantically in `/memory/search`. They are fetched directly for prompt injection and summarization workflows.

## Data Model Details

### `MemoryInteractionEvent`

Defined in `y_server/modals.py`.

Purpose:

- append-only interaction history per run
- source of recent-pair context and digest-building input
- origin record for event-derived `MemoryItem` rows

Important fields:

- `run_id`, `round_id`
- `actor_user_id`, `target_user_id`
- `thread_root_id`, `target_post_id`, `actor_post_id`
- `event_type`
- `relation_label`, `tone_label`
- `topics_json`, `salient_claim`
- `weight`
- `event_text`
- `importance`
- `last_accessed_round`, `access_count`

Observations:

- `weight` is persisted but not used in retrieval scoring.
- `last_accessed_round` and `access_count` are present but not updated by the event-specific APIs after creation.

### `MemorySocialCard`

Purpose:

- hold a compact relationship state for one agent toward another user

Stored attributes:

- numeric relationship axes: `affinity`, `conflict`, `humor`, `trust`
- latest relational metadata
- event count
- freeform summary
- evidence tail JSON

This is effectively a structured interpersonal memory cache.

### `MemoryThreadCard`

Purpose:

- hold a compact understanding of a thread from one agent’s perspective

Stored attributes:

- `gist_text`
- `my_role`
- `participants_top_json`
- `entry_points_json`
- `last_seen_round_id`

This is thread-local contextual memory, not full-text searched.

### `MemoryCommunityDigest`

Purpose:

- store run-wide social context

Stored attributes:

- `digest_text`
- `top_topics_json`
- `norms_json`
- `memes_json`
- `polarizing_issues_json`

This digest also affects importance estimation for new events: events touching polarizing issues get a score boost.

### `MemoryItem`

Purpose:

- retrieval index and memory stream

Stored attributes:

- identity: `run_id`, `agent_user_id`, `item_type`
- content: `text`, `metadata_json`, `topic_tags_json`
- linkage: `source_event_id`, `thread_root_id`, `other_user_id`
- ranking state: `round_id`, `importance`, `recency_anchor_round`
- access state: `last_accessed_round`, `access_count`
- vector state: `embedding_json`, `embedding_model`, `embedding_dim`, `embedding_status`
- audit timestamps: `created_at`, `updated_at`

Observations:

- `recency_anchor_round` is written but retrieval currently uses `round_id`, not `recency_anchor_round`.
- embeddings are stored inline as JSON text rather than in a vector index or separate table.
- `embedding_status` drives async indexing and degraded retrieval behavior.

## Schema Management and Initialization

The memory schema is created lazily in `content_management.py` through `_ensure_memory_schema()`.

Behavior:

- calls `db.create_all()` once
- runs ad hoc schema evolution through `_ensure_memory_schema_evolution()`
- starts a background embedding indexer thread

Schema evolution is manual and runtime-driven:

- `_ensure_column(...)` checks the database and executes `ALTER TABLE`
- `_ensure_index(...)` checks indexes and runs `CREATE INDEX`

Memory-specific indexes added at runtime include:

- `memory_interaction_events(run_id, id)`
- `memory_items(run_id, agent_user_id)`
- `memory_items(item_type)`
- `memory_items(round_id)`
- `memory_items(other_user_id)`
- `memory_items(thread_root_id)`
- `memory_items(embedding_status)`
- `memory_items(importance)`
- `memory_items(run_id, agent_user_id, round_id, id)`
- `memory_items(run_id, agent_user_id, item_type, round_id, id)`

Assessment:

- This avoids requiring formal migrations for experiment databases.
- It also moves migration responsibility into request handling, which increases hidden operational complexity.
- Failures are mostly swallowed, so a partially migrated schema can remain undetected.

## Text Sanitization and Prompt-Scaffold Filtering

The implementation includes defensive filtering to prevent prompt scaffolding from being stored as memory.

Relevant helpers:

- `_looks_like_prompt_scaffold`
- `_sanitize_generated_text`
- `_payload_has_prompt_scaffold`
- `_reject_prompt_scaffold`

Blocked scaffold patterns include strings like:

- `memory tier a/b/c`
- `memory context`
- `memory search brief`
- `memory pack`
- `facts pack`
- other prompt-template phrases

Behavior:

- line-level scaffold lines are stripped from generated text
- entire payloads can be rejected with HTTP `422`
- JSON-like payloads are checked by serializing them and scanning the serialized content

This is one of the stronger aspects of the implementation. It shows awareness that LLM-generated summaries can accidentally persist prompt boilerplate.

## Write Path: `/memory/event`

`/memory/event` is the main ingestion endpoint.

### Inputs

Required:

- `run_id`
- `round_id`
- `actor_user_id`
- `event_type` in `comment`, `post`, `upvote`, `downvote`

Optional:

- target/thread/post references
- relation/tone labels
- topics
- salient claim
- event text
- importance
- weight
- cold start window

### Processing steps

1. Validate identifiers and event type.
2. Normalize optional ints and short labels.
3. Sanitize topics and salient claim.
4. Sanitize `event_text`, or synthesize one with `_normalize_event_text(...)` if absent.
5. Estimate importance with `_estimate_importance(...)` if the client did not supply one.
6. Insert a `MemoryInteractionEvent`.
7. Insert a corresponding `MemoryItem` of type `event`.
8. Apply cold-start logic.
9. Optionally compute synchronous embeddings for cold-start items.
10. Commit both rows together.

### Event text normalization

If the client does not provide `event_text`, the server generates a pipe-delimited representation such as:

- event type
- actor id
- target user id
- thread/post ids
- relation/tone
- claim
- topics

This normalized representation is important because it becomes the retrieval text for event memories.

### Importance estimation

Default importance is heuristic:

- base value by event type
- boost for hostile/disagree/angry/snarky interactions
- boost for helpful/funny interactions
- boost when a salient claim exists
- extra boost when topics overlap with community digest polarizing issues

This is simple, deterministic, and easy to reason about.

### Cold-start policy

This endpoint contains the most explicit memory-policy logic in the system.

Rules:

- first `cold_start_window` interactions for an agent are marked as cold start
- those initial event memory items get minimum importance `0.70`
- after the window passes, previously imprinted early items are progressively capped downward
- decay floor is `0.25`

Interpretation:

- the system intentionally over-remembers the earliest interactions
- later interactions gradually reduce the privileged status of those early memories

This is a deliberate attempt to model early-impression effects without letting them dominate forever.

### Synchronous embedding optimization

If an event is in cold start and embeddings are available:

- embedding is generated synchronously
- the item moves directly to `embedding_status = "ready"`

Otherwise it remains `pending` for async indexing.

## Write Path: `/memory/item/upsert`

This endpoint lets the client create or update arbitrary memory items of type:

- `event`
- `reflection`
- `summary`

### Capabilities

- optional explicit item id for updates
- attach source event id
- bind to thread or other user
- attach topic tags and metadata
- set custom importance
- inject a precomputed embedding
- force synchronous embedding on demand

### Role in the design

This is the generic insertion path for higher-order memories. The server assumes the client may perform LLM-on-write summarization and send back reflections/summaries for storage.

### Defaults

- `reflection` default importance: `0.5`
- other types default importance: `0.35`
- no embedding provided: mark as `pending`

This endpoint is the clearest evidence that the design expects external summarization logic, most likely from the client or an orchestrating agent layer.

## Summary Memory Upserts

### `/memory/social/upsert`

Updates or creates one social card per:

- `run_id`
- `agent_user_id`
- `other_user_id`

Used for relationship summaries and supporting evidence.

### `/memory/thread/upsert`

Updates or creates one thread card per:

- `run_id`
- `agent_user_id`
- `thread_root_id`

Used for thread gist, role, participant, and entry-point summaries.

### `/memory/community/update`

Updates or creates one community digest per:

- `run_id`

Used for macro-level social context and importance shaping.

### `/memory/community/get`

Fetches the latest digest.

Observation:

- the model enforces uniqueness logically, but retrieval still orders by descending id and takes first, suggesting defensive handling of possible duplicate rows.

## Reset Path

`/memory/reset` deletes all memory state for a run:

- interaction events
- memory items
- social cards
- thread cards
- community digest

This confirms that memory scope is the simulation run, not a persistent cross-run agent identity.

## Embedding Service and Indexing

### `MemoryEmbeddingService`

The embedding wrapper is intentionally tolerant of missing infrastructure.

Behavior:

- lazy initialization
- tries to import Ollama client dynamically
- tests connectivity with a sample embed call
- caches availability state
- records last error
- returns `None` or `[None, ...]` rather than raising through the call stack

Default model:

- `embeddinggemma`

Default host:

- `http://127.0.0.1:11434`

### Background indexer

The server starts a daemon thread named `memory-embedding-indexer`.

Loop behavior:

- query up to 32 `MemoryItem` rows with `embedding_status == "pending"`
- discard empty texts as `failed`
- batch embed texts
- store embedding JSON, dimension, model name
- set status to `ready` or `failed`
- sleep when idle or on error

Operational characteristics:

- polling loop, not queue-driven
- no sharding by run or agent
- no retry policy beyond leaving items as `pending` until processed, then `failed`
- broad exception swallowing with rollback on outer failure

### Degraded mode

If Ollama or the embedding model is unavailable:

- queries still work
- items can still be stored
- search falls back to lexical scoring

This is a strong availability choice. It prioritizes continuity over retrieval quality.

## Retrieval Path: `/memory/search`

This is the core retrieval endpoint for the memory stream.

### Query filters

Required:

- `run_id`
- `agent_user_id`
- `query_text`

Optional:

- `other_user_id`
- `thread_root_id`
- `time_window_rounds`
- `round_id`
- `types`
- `topic_tags`
- `k`
- `max_chars`
- `include_evidence_tail`
- `recency_half_life_rounds`

### Candidate generation

The endpoint:

- filters by run and agent
- optionally narrows by item type, user, thread, time window, topic tags
- orders by most recent `round_id`, then `id`
- limits the candidate pool to 300 rows before scoring

This is a bounded in-database prefilter followed by application-side scoring.

### Query normalization

The system normalizes query text by:

- lowercasing
- converting `&` to `and`
- stripping non-alphanumeric characters
- normalizing whitespace

It also expands certain aliases through `_MEMORY_QUERY_ALIAS_MAP`.

Current alias coverage is very narrow. In the checked code, the only explicit family is:

- `dnd`
- `d&d`
- `d and d`
- `dungeons and dragons`

That suggests the alias mechanism exists, but the vocabulary is not generalized yet.

### Relevance scoring

If both query embedding and item embedding are available:

- relevance is cosine similarity

Otherwise:

- relevance is lexical overlap
- the best score across query variants is used
- a legacy lexical score acts as a floor for compatibility

### Recency scoring

Recency is exponential decay:

- half-life defaults to `96` rounds
- override can come from request payload, app config, or env var `MEMORY_RECENCY_HALF_LIFE_ROUNDS`

Formula:

- `exp(-lambda * delta_rounds)` with `lambda = ln(2) / half_life`

### Importance scoring

The stored item importance is clamped into `[0, 1]` and contributes directly.

### Final score

Fixed weights:

- relevance: `0.55`
- recency: `0.25`
- importance: `0.20`

Final ranking:

- sort by score
- break ties by importance
- then by item id

### Response shape

The endpoint returns:

- detailed item payloads
- `memory_brief` text formatted for prompt injection
- `retrieval_meta`
- `user_map`

The response is designed for both programmatic use and direct LLM prompt inclusion.

### Access bookkeeping

For top results only:

- `access_count` increments
- `last_accessed_round` is updated when `current_round` is known

Current usage:

- these fields are tracked
- they are not yet fed back into scoring

So access-awareness is only partially implemented.

## Context Assembly Path: `/memory/get_context`

This endpoint collects structured context for prompt injection.

Inputs:

- `run_id`
- `agent_user_id`
- optional `other_user_id`
- optional `thread_root_id`
- optional `pair_limit`

Returned payload:

- user map
- normalized username for other user
- social card
- thread card
- latest community digest
- recent pairwise interaction events

Important distinction:

- `/memory/search` retrieves from the memory stream
- `/memory/get_context` retrieves directly from summary tables and recent interaction history

This means prompt construction can combine:

- semantic or lexical memory recall
- relationship summary
- thread summary
- community summary
- recent direct interaction trace

## Recent Events Path: `/memory/events_recent`

This endpoint returns the most recent raw interaction events for a run.

Purpose:

- support digest construction
- provide a chronological slice of recent interaction activity

It is not agent-specific. It is run-global.

## Notable Design Patterns

### Run-scoped isolation

Every memory table uses `run_id`. Memory is deliberately tied to one simulation run.

### Client-assisted summarization

The server stores summaries and reflections, but does not generate them itself in this code path. The comments and endpoint contracts imply a client or external orchestration layer performs LLM-on-write and sends outputs back.

### Graceful degradation

The subsystem is designed to keep functioning when embeddings fail:

- storage still works
- search still works lexically
- embedding failures do not crash requests

### Prompt-oriented response design

`memory_brief`, evidence tail handling, and username humanization all show that retrieval output is meant to feed LLM prompts directly, not just backend consumers.

## Operational and Architectural Limitations

### 1. Memory logic is concentrated in one large route module

Most memory behavior lives in `y_server/routes/content_management.py`.

Consequences:

- storage policy, retrieval policy, schema migration, and HTTP API are tightly coupled
- unit testing is harder
- changes to memory logic require editing a high-churn file with unrelated responsibilities

### 2. Runtime schema evolution replaces formal migrations

The code performs schema repair at request time.

Risks:

- silent failures
- inconsistent schema state across deployments
- operational surprises under concurrent startup or multi-worker configurations

### 3. Embedding dependency appears undeclared in server requirements

The memory system imports `ollama` dynamically, but `requirements_server.txt` does not visibly declare an `ollama` package.

Practical effect:

- semantic search may silently never activate in a fresh environment
- the system will still run, but only lexically

### 4. Inline JSON embeddings do not scale well

Embeddings are stored as text blobs in the main `memory_items` table.

Tradeoffs:

- simple implementation
- expensive for large memory volumes
- no ANN index
- no vector-native filtering

### 5. Candidate set is hard-capped before scoring

Search scores at most 300 prefiltered candidates, selected by recency order.

Implication:

- older but semantically strong memories may never be considered
- recall quality depends strongly on the recency filter and recent insertion order

### 6. Access tracking is underused

`access_count` and `last_accessed_round` are recorded, but not used to adjust retrieval.

So the implementation does not yet support:

- spaced repetition
- reinforcement by repeated recall
- suppression of overused memories

### 7. `recency_anchor_round` is currently not meaningful in retrieval

The field exists and is populated, but recency scoring uses `round_id`.

That suggests either:

- an unfinished feature
- or future flexibility that has not been wired into ranking

### 8. Event `weight` is stored but unused

`MemoryInteractionEvent.weight` does not appear to affect:

- memory item creation
- importance
- retrieval ranking

It is effectively dead data in the current implementation.

### 9. Summary-table writes are trusted almost entirely

The upsert endpoints sanitize text and block scaffold patterns, but they do not enforce a richer schema on summary content.

This is flexible, but it also means:

- data shape depends heavily on client discipline
- malformed summary JSON can be stored as plain text

### 10. No visible test coverage in the repository

A repository scan did not find a test suite covering this memory subsystem.

This matters because the subsystem contains:

- ranking heuristics
- cold-start policy
- async indexing behavior
- schema evolution logic
- prompt-safety filtering

All of those are areas where regressions are easy to introduce.

## Behavioral Assessment

### What the implementation does well

- cleanly separates raw events from searchable memories and structured summaries
- supports both episodic and summarized memory forms
- has explicit cold-start policy instead of accidental early-bias behavior
- degrades safely when embeddings are unavailable
- returns prompt-ready retrieval artifacts
- supports run-scoped resets, which fits simulation workflows

### What is still relatively immature

- vector retrieval infrastructure
- migration discipline
- modularity
- testing
- use of access history
- use of stored weight/anchor fields
- semantic alias coverage

## End-to-End Memory Lifecycle

The effective lifecycle is:

1. A client observes or infers an interaction.
2. The client calls `/memory/event` with labels, topics, and optional event text.
3. The server stores the raw interaction in `memory_interaction_events`.
4. The server mirrors it into `memory_items` as an `event`.
5. The server either embeds immediately for cold-start items or leaves embedding for the background indexer.
6. The client may later create `reflection` or `summary` items through `/memory/item/upsert`.
7. The client may update social/thread/community summaries through dedicated upsert endpoints.
8. Retrieval happens through:
   - `/memory/search` for ranked memory recall
   - `/memory/get_context` for structured context blocks
   - `/memory/events_recent` for digest-building slices

This is coherent. The system is already more than a simple event log. It is a small memory stack with separate episodic, semantic, and summary layers.

## Recommendations

### Short-term

- move memory helpers, models, retrieval, and background indexing into a dedicated module or package
- add tests for:
  - cold-start importance behavior
  - prompt-scaffold rejection
  - lexical fallback retrieval
  - score calculation
  - schema evolution helpers
- make embedding availability observable in logs or health endpoints
- declare the Ollama client dependency explicitly if semantic retrieval is intended to be enabled by default

### Medium-term

- replace request-time schema evolution with proper migrations
- use `recency_anchor_round` intentionally or remove it
- either use `weight` in scoring or remove it
- introduce retry/error metadata for failed embeddings
- broaden query normalization and alias expansion beyond a single hardcoded alias family

### Longer-term

- move embeddings to a vector-native store or an ANN-capable retrieval layer
- make candidate generation semantic-first rather than recency-first once memory volume grows
- integrate access history into ranking to model reinforcement or forgetting more realistically

## Conclusion

The current agent memory implementation is a practical, run-scoped memory subsystem designed for simulation workloads rather than a general-purpose memory platform. It combines:

- raw interaction logging
- searchable memory items
- structured summaries
- optional semantic embeddings
- lexical fallback retrieval

Its strongest properties are simplicity, resilience, and prompt-oriented design. Its weakest properties are architectural concentration, missing migration discipline, likely undeclared embedding dependency, and several partially implemented ranking features. As implemented, it is a solid experimental memory layer that can support agent simulations effectively, but it would need refactoring and stronger operational rigor before being treated as production-grade infrastructure.
