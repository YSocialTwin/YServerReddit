# System Overview

YServer is a monolithic Flask application that combines:

- HTTP route registration
- SQLAlchemy model binding
- experiment database bootstrapping
- logging setup
- optional module loading
- recommendation and social interaction logic
- a run-scoped agent memory subsystem

## Boot Flow

The effective startup sequence is:

1. `y_server_run.py` or `wsgi.py` determines the active config path.
2. `YSERVER_CONFIG` is read by `y_server/__init__.py`.
3. the app decides between SQLite and PostgreSQL based on `DATABASE_URL`.
4. Flask-SQLAlchemy is initialized.
5. request logging is attached.
6. file logging to `_server.log` is configured.
7. schema helpers for image posts and comment dedupe are executed.
8. route modules are imported.

## Application Layout

### `y_server/__init__.py`

Responsibilities:

- Flask app construction
- SQLAlchemy initialization
- JSON request-duration logging
- log file setup
- database bootstrap logic
- a few runtime schema helpers

### `y_server/routes/`

This folder contains all HTTP endpoints.

Always-loaded route groups:

- time management
- user management
- content management
- interaction management
- experiment management

Conditionally loaded modules:

- news
- voting
- image
- image_post

### `y_server/modals.py`

Defines the persistence layer. The file includes both the classic social simulation tables and the newer memory tables.

### `y_server/utils.py`

Contains recommendation support logic used by feed and similarity-based discovery paths.

### `y_server/content_analysis/`

Contains:

- VADER sentiment scoring
- optional Perspective toxicity scoring

### `y_server/memory_embedding.py`

Contains:

- Ollama-backed embedding client wrapper
- cosine similarity
- lexical fallback scoring

## Runtime Modes

### Local experiment mode

Default behavior:

- create or reuse `experiments/<name>.db`
- seed it from `data_schema/database_clean_server.db`
- run Flask directly or under Gunicorn

### PostgreSQL mode

Activated by `DATABASE_URL`.

Behavior:

- skip SQLite seed-copy logic
- bind SQLAlchemy to the PostgreSQL URI
- still use `NullPool`

### External subprocess mode

`y_server/__init__.py` contains a fallback branch intended for integration contexts where the usual config import may fail. In that path the app binds to a `dummy.db` SQLite file under `experiments/`.

## Module Boundaries

The codebase is functionally split, but not strongly encapsulated.

Examples:

- `content_management.py` mixes feed ranking, content CRUD, prompt-safety filters, memory retrieval, and schema evolution
- `experiment_management.py` performs live database rebinding and logging reconfiguration
- memory indexing is launched from inside route-layer initialization rather than a separate worker service

This architecture is easy to run for experiments, but harder to evolve safely at scale.

## Request Logging

All requests pass through a timing wrapper in `_register_request_logging(...)`.

Logged fields include:

- remote address
- method
- path
- status code
- duration
- wall-clock time
- simulation day and hour when available

The result is a JSON log stream in `_server.log` that can be mined for experiment analysis.

## Dynamic Schema Management

The repository uses a mixed approach:

- the base schema comes from the seed database and model metadata
- some newer fields and indexes are added at runtime through helper functions

Current examples include:

- image-post columns on `post`
- comment dedupe columns and partial unique indexes
- memory table evolution and indexes

This design reduces migration friction for experiments, but it also shifts schema correctness into runtime behavior.

## Recommendation and Feed Logic

Recommendation behavior spans:

- route-level selection logic in `content_management.py`
- graph and similarity helpers in `interaction_management.py` and `utils.py`

The server supports multiple recommendation styles:

- reverse chronological
- popularity-biased
- hashtag-reuse search
- follow suggestions from graph heuristics
- feed diversification helpers based on interests, followers, and similar users

## Memory as a First-Class Subsystem

The memory subsystem is not a standalone service. It is part of the same Flask process and database. That means:

- memory writes are regular HTTP calls
- memory search is served synchronously by the app
- embedding indexing is handled by an in-process background thread

For a detailed breakdown, see:

- [Agent Memory](agent-memory.md)
- [Detailed Memory Report](../agent-memory-report.md)
