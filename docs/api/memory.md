# Memory API

The memory subsystem is implemented in `y_server/routes/content_management.py` and backed by models in `y_server/modals.py`.

Memory is scoped by `run_id`. It is not a cross-run identity store.

## Endpoint Summary

| Route | Method | Purpose |
| --- | --- | --- |
| `/memory/reset` | `POST` | Clear all memory state for a run |
| `/memory/event` | `POST` | Store an interaction event and mirror it into the memory stream |
| `/memory/social/upsert` | `POST` | Upsert a social summary card for an agent and another user |
| `/memory/thread/upsert` | `POST` | Upsert a thread summary card |
| `/memory/community/get` | `POST` | Fetch the latest run-level community digest |
| `/memory/community/update` | `POST` | Upsert the run-level community digest |
| `/memory/item/upsert` | `POST` | Upsert a generic memory item: event, reflection, or summary |
| `/memory/search` | `POST` | Retrieve ranked memory items |
| `/memory/get_context` | `POST` | Fetch structured prompt context |
| `/memory/events_recent` | `POST` | Fetch recent raw events for digest building |

## `POST /memory/reset`

Required body:

```json
{"run_id": "local-test"}
```

Deletes all run-scoped rows from:

- `memory_interaction_events`
- `memory_items`
- `memory_social_cards`
- `memory_thread_cards`
- `memory_community_digests`

## `POST /memory/event`

This is the primary event-ingestion endpoint.

Required fields:

- `run_id`
- `round_id`
- `actor_user_id`
- `event_type`

Allowed `event_type` values:

- `comment`
- `post`
- `upvote`
- `downvote`

Optional fields:

- `target_user_id`
- `thread_root_id`
- `target_post_id`
- `actor_post_id`
- `relation_label`
- `tone_label`
- `topics`
- `salient_claim`
- `weight`
- `event_text`
- `importance`
- `cold_start_window`

Behavior:

- sanitizes prompt scaffolding out of text fields
- synthesizes `event_text` if the client does not provide one
- estimates importance heuristically if missing
- inserts a `MemoryInteractionEvent`
- inserts a corresponding `MemoryItem` of type `event`
- applies the cold-start importance policy
- synchronously embeds early items when embeddings are available

Success response includes:

- `event_id`
- `memory_item_id`
- `cold_start`
- `cold_start_window`
- `cold_start_decay_level`
- `cold_start_importance_cap`
- `interaction_event_count`
- `agent_item_count`

## `POST /memory/social/upsert`

Maintains one summary card per `(run_id, agent_user_id, other_user_id)`.

Useful fields:

- `affinity`
- `conflict`
- `humor`
- `trust`
- `last_relation_label`
- `last_round_id`
- `last_thread_root_id`
- `last_updated_round`
- `event_count`
- `summary_text`
- `evidence_tail`

## `POST /memory/thread/upsert`

Maintains one thread card per `(run_id, agent_user_id, thread_root_id)`.

Useful fields:

- `gist_text`
- `my_role`
- `participants_top`
- `entry_points`
- `last_seen_round_id`

## `POST /memory/community/get`

Fetches the latest community digest for a run.

Response fields:

- `round_id`
- `digest_text`
- `top_topics`
- `norms`
- `memes`
- `polarizing_issues`

## `POST /memory/community/update`

Upserts the run-level digest.

Accepted fields:

- `run_id`
- optional `round_id`
- `digest_text`
- `top_topics`
- `norms`
- `memes`
- `polarizing_issues`

The `polarizing_issues` list influences importance estimation for future memory events.

## `POST /memory/item/upsert`

Creates or updates a memory stream item.

Required fields:

- `run_id`
- `agent_user_id`
- `item_type`
- `text`

Allowed `item_type` values:

- `event`
- `reflection`
- `summary`

Optional fields:

- `id`
- `source_event_id`
- `thread_root_id`
- `other_user_id`
- `round_id`
- `recency_anchor_round`
- `last_accessed_round`
- `access_count`
- `importance`
- `metadata`
- `topic_tags`
- `embedding`
- `embedding_model`
- `force_sync_embedding`

Default importance:

- `reflection`: `0.5`
- others: `0.35`

If an embedding is supplied directly, the item is stored as `ready`. Otherwise it defaults to `pending`.

## `POST /memory/search`

Searches memory items for a specific agent within a run.

Required fields:

- `run_id`
- `agent_user_id`
- `query_text`

Optional filters and controls:

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

Ranking formula:

- `0.55 * relevance`
- `0.25 * recency`
- `0.20 * importance`

Retrieval mode:

- semantic cosine similarity when embeddings exist
- lexical fallback when embeddings are unavailable

The response includes:

- ranked `items`
- prompt-oriented `memory_brief`
- `retrieval_meta`
- `user_map`

`retrieval_meta` is especially useful for debugging degraded retrieval because it reports:

- whether query embeddings were available
- how many items were `ready`, `pending`, or `failed`
- whether lexical fallback was used

## `POST /memory/get_context`

Collects structured prompt context for a run and agent.

Required fields:

- `run_id`
- `agent_user_id`

Optional:

- `other_user_id`
- `thread_root_id`
- `pair_limit`

Response sections:

- `social_card`
- `thread_card`
- `community_digest`
- `recent_pair_events`
- `user_map`

This endpoint complements `/memory/search`; it does not replace it.

## `POST /memory/events_recent`

Returns the most recent raw interaction events for a run.

Request fields:

- `run_id`
- optional `limit` with an effective cap of `200`

This route is primarily useful when an external summarizer wants recent activity for digest generation.

## Operational Notes

- the first memory request lazily initializes tables and starts a background embedding indexer thread
- embeddings rely on a local Ollama service by default
- when embeddings are unavailable, storage still works and retrieval falls back to lexical scoring

See [Agent Memory](../architecture/agent-memory.md) for the deeper architectural walkthrough.
