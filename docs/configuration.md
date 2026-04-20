# Configuration

## Configuration Sources

YServer reads configuration from three places:

1. a JSON file, defaulting to `config_files/exp_config.json`
2. the `YSERVER_CONFIG` environment variable, which overrides the JSON path
3. selected environment variables such as `DATABASE_URL` and `MEMORY_RECENCY_HALF_LIFE_ROUNDS`

## Default Config File

Current repository default:

```json
{
  "name": "small",
  "host": "0.0.0.0",
  "port": 5010,
  "debug": "True",
  "reset_db": "True",
  "modules": ["news", "voting", "image", "image_post"],
  "perspective_api": null
}
```

## Supported JSON Keys

### Core keys

| Key | Required | Meaning |
| --- | --- | --- |
| `name` | yes in default SQLite mode | Experiment name. Used to name the SQLite database if `database_uri` is not set. |
| `host` | yes | Host interface for Flask or Gunicorn bind generation. |
| `port` | yes | Port for Flask or Gunicorn bind generation. |
| `reset_db` | effectively yes for current startup path | If `"True"`, the SQLite seed database is recopied on startup. |
| `modules` | yes | Optional route modules to import dynamically. |
| `perspective_api` | optional | API key for Perspective toxicity scoring. |

### Additional keys used by the app

| Key | Meaning |
| --- | --- |
| `database_uri` | Overrides the default SQLite experiment location. Supports either a SQLite URI or a filesystem path that will be converted into one. |
| `data_path` | Used by logging setup to decide where `_server.log` is written. |
| `sentiment_annotation` | Set on `app.config` in `wsgi.py`; can be used by upstream integrations. |
| `emotion_annotation` | Set on `app.config` in `wsgi.py`; can be used by upstream integrations. |

## Environment Variables

### `YSERVER_CONFIG`

Path to a JSON config file.

```bash
export YSERVER_CONFIG=/abs/path/to/exp_config.json
python y_server_run.py
```

### `DATABASE_URL`

If set to a PostgreSQL URI containing `postgresql`, YServer uses it instead of SQLite.

Example:

```bash
export DATABASE_URL=postgresql://user:pass@localhost:5432/yserver
gunicorn -c gunicorn_config.py wsgi:app
```

### `MEMORY_RECENCY_HALF_LIFE_ROUNDS`

Controls the default memory recency decay used by `/memory/search` when the request does not specify `recency_half_life_rounds`.

## Module Loading

`y_server/routes/__init__.py` loads route modules dynamically from the `modules` list in the config file.

Recognized module names in the current repository are:

- `news`
- `voting`
- `image`
- `image_post`

If a listed module does not exist, import fails during startup.

## Stress/Reward Enablement

The server also accepts a top-level `stress_reward` block, for example:

```json
{
  "stress_reward": {
    "enabled": true,
    "backward_rounds": 24
  }
}
```

Only the enablement state is interpreted directly by the server. The forum client still computes deltas and performs any LLM annotation. When stress/reward is disabled, the dedicated stress/reward routes stay inactive.

## SQLite vs PostgreSQL

### SQLite mode

Used when `DATABASE_URL` is missing or not PostgreSQL.

Behavior:

- binds a file-backed SQLite database
- uses `NullPool`
- sets `check_same_thread=False`
- seeds a new experiment DB from `data_schema/database_clean_server.db`

### PostgreSQL mode

Used when `DATABASE_URL` contains `postgresql`.

Behavior:

- binds directly to the PostgreSQL URI
- still uses `NullPool`
- skips the local seed-copy logic

## Logging Configuration

On startup, `_setup_file_logging(...)` chooses a log directory from:

1. `config["data_path"]` if present
2. the SQLite database directory
3. a derived `y_web/experiments/<db_name>` path for some external deployments

It writes JSON-formatted logs to `_server.log`.

See [Logging and Data](operations/logging-and-data.md) for details.
