# Running and Deployment

## Development Entrypoint

Use `y_server_run.py` for local development.

```bash
python y_server_run.py
```

You can supply a different config file:

```bash
python y_server_run.py --config_file /abs/path/to/exp_config.json
```

Key behavior:

- exports `YSERVER_CONFIG` before importing the Flask app
- loads the config JSON itself
- sets `app.config["perspective_api"]`
- starts Flask on the configured host and port

## WSGI Entrypoint

Use `wsgi.py` for production process managers such as Gunicorn.

```bash
gunicorn wsgi:app
```

With an explicit config:

```bash
YSERVER_CONFIG=/abs/path/to/exp_config.json gunicorn wsgi:app
```

`wsgi.py` also copies a few config values into the Flask app:

- `perspective_api`
- `sentiment_annotation`
- `emotion_annotation`

## Gunicorn

The repository includes `gunicorn_config.py`.

Basic usage:

```bash
gunicorn -c gunicorn_config.py wsgi:app
```

Current defaults:

- bind host and port from the experiment config
- `workers = 1`
- `threads = 1`
- `worker_class = "sync"`
- `timeout = 120`
- `max_requests = 1000`
- `preload_app = sys.platform != "darwin"`

macOS-specific behavior:

- `preload_app` is disabled on Darwin to avoid fork-safety crashes
- `OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES` is exported to workers

## Running Multiple Instances

The intended pattern is one process per experiment config, each with its own environment.

Example:

```bash
YSERVER_CONFIG=/tmp/exp-a.json gunicorn -c gunicorn_config.py wsgi:app
YSERVER_CONFIG=/tmp/exp-b.json gunicorn -c gunicorn_config.py wsgi:app
```

If both instances use SQLite, they should point at different files.

## Changing Databases at Runtime

The `/change_db` endpoint can rebind the application to a new database URI or SQLite file.

This is operationally powerful, but it also means:

- logging paths can change at runtime
- schema expectations must still match the repository models
- request isolation depends on how the deployment is managed

## Production Notes

- prefer Gunicorn over Flask development server
- treat `/shutdown`, `/reset`, and `/change_db` as privileged endpoints
- if you rely on memory embeddings, ensure Ollama and the expected model are available on the same host
- if you rely on toxicity scoring, set a valid `perspective_api` key
