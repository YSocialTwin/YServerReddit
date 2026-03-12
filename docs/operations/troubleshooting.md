# Troubleshooting

## Server Fails During Startup

### Check the active config file

The app reads the config path from `YSERVER_CONFIG`, falling back to `config_files/exp_config.json`.

Verify:

```bash
echo "$YSERVER_CONFIG"
cat config_files/exp_config.json
```

### Check for malformed JSON

Both `y_server_run.py` and `wsgi.py` load the config before or during app initialization.

If the file is invalid JSON, import-time startup can fail before the server is fully running.

## Database Problems

### SQLite database does not appear where expected

In default local mode, the app copies:

- `data_schema/database_clean_server.db`

to:

- `experiments/<name>.db`

If that does not happen, check:

- the `name` field in the config
- whether `experiments/` is writable
- whether `database_uri` overrides the default path

### PostgreSQL is ignored

The server only switches to PostgreSQL when `DATABASE_URL` exists and contains the substring `postgresql`.

Verify:

```bash
echo "$DATABASE_URL"
```

### Runtime database switching behaves unexpectedly

`/change_db` rebinds the live application. If requests are in flight or the target schema does not match expectations, you can see inconsistent behavior.

Prefer changing databases between experiment runs rather than during active traffic.

## Dependency Problems

### `requirements_server.txt` installation fails

The file is currently UTF-16 encoded. Some environments handle that correctly; others do not.

Check the encoding:

```bash
file requirements_server.txt
```

If your tooling fails, convert the file or install packages manually.

### VADER sentiment resources are missing

`y_server_run.py` calls `nltk.download("vader_lexicon")`. If your environment blocks outbound downloads, startup may fail or stall.

Preinstall the NLTK resource in your environment if needed.

### Perspective API errors

Toxicity scoring is optional. If `perspective_api` is `null`, the code skips those calls. If you provide a bad key, the helper catches exceptions and prints them, but the request usually continues.

### Memory embeddings never become semantic

Possible causes:

- Ollama is not running on `127.0.0.1:11434`
- model `embeddinggemma` is not installed
- Python package `ollama` is unavailable

Symptoms:

- `/memory/search` reports lexical fallback in `retrieval_meta`
- memory items stay `pending` for a while, then become `failed`

## Route-Level Issues

### A `GET` endpoint rejects your request body

Several `GET` routes still expect JSON in the request body. If your client library drops bodies on `GET`, use the alternative `POST` method where supported.

### Prompt-scaffold rejection

Some content and memory routes reject payloads that look like copied prompt instructions.

Typical error:

```json
{"status": 422, "error": "prompt_scaffold_detected"}
```

This is expected behavior when the text resembles a prompt template rather than user-facing content.

### Duplicate comments are ignored

`/comment` performs idempotency checks by:

- `client_action_id`
- normalized comment text under the same parent and round

A duplicate request may return `200` with `deduped: true`.

## Logging and Inspection Tips

- inspect `_server.log` in the experiment or configured log directory
- use `/log/agent_decision` for custom structured traces
- inspect `retrieval_meta` from `/memory/search` to debug memory behavior

For storage and log locations, see [Logging and Data](logging-and-data.md).
