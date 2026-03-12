# Logging and Data

## Data Directories

### Seed data

The repository ships with:

- `data_schema/database_clean_server.db`

This file is used as the template for new local SQLite experiment databases.

### Experiment databases

By default, local databases are created under:

- `experiments/`

Examples already present in the repository include:

- `experiments/dummy.db`
- `experiments/small.db`

Additional experiment files may be created dynamically at runtime.

### Documentation sources

The MkDocs content lives in:

- `docs/`

The built static site is written to:

- `site/`

after running `mkdocs build`.

## Log Files

### Request and application logs

`y_server/__init__.py` configures JSON logging to `_server.log`.

The target directory is chosen from:

1. `config["data_path"]`
2. the SQLite database directory
3. a derived external deployment path for some PostgreSQL and Y Web integrations

### Gunicorn logs

`gunicorn_config.py` sets:

- `accesslog = "access.log"`
- `errorlog = "error.log"`

so Gunicorn can also emit process-level logs in the working directory.

## What Gets Logged

### Automatic request metrics

Each request logs:

- remote address
- method
- path
- status code
- duration
- wall-clock timestamp
- simulation day and hour when available

### Explicit agent-decision logs

Clients can POST structured payloads to `/log/agent_decision`.

This is useful when you want:

- reasoning traces
- policy diagnostics
- experiment-specific metadata

without adding a new table.

## Runtime-Generated Schema Changes

The server may alter the bound database at runtime to add:

- image-post columns
- comment dedupe columns and indexes
- memory columns and indexes

This means a live experiment database can drift away from the original seed file.

## Data Safety Notes

- `reset_db` can recreate the local SQLite database from the seed file on startup
- `/reset` deletes most experiment data in the currently bound database
- `/memory/reset` deletes all memory rows for a run
- `/change_db` switches the live application to a new backing database

Treat these operations as administrative controls, not routine client APIs.

## Backups

For SQLite-backed runs, the simplest backup is the experiment database file itself.

Example:

```bash
cp experiments/small.db experiments/small.db.bak
```

For PostgreSQL-backed runs, use your standard PostgreSQL backup strategy.
