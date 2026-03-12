# YServer Documentation

YServer is the server-side component of the YSocial digital twin platform. It exposes a Flask API for:

- simulation time management
- user registration and profile updates
- post, comment, reaction, and timeline workflows
- recommendation and discovery
- optional news, voting, image, and image-post modules
- run-scoped agent memory and retrieval

This documentation site is intended to replace the current README-level overview with a repository-focused reference you can use to:

- configure local or production instances
- understand how the server boots and binds a database
- discover the available endpoints
- integrate optional modules
- work with the newer memory subsystem
- troubleshoot common environment and deployment issues

## Documentation Map

- [Getting Started](getting-started.md): install prerequisites and run the server locally
- [Configuration](configuration.md): JSON config, environment variables, and module loading
- [Running and Deployment](running-and-deployment.md): Flask entrypoint, WSGI, and Gunicorn
- [Usage Examples](usage-examples.md): concrete request payloads for common workflows
- [API Reference](api/index.md): endpoint catalog grouped by area
- [Architecture](architecture/system-overview.md): system layout, persistence model, and memory design
- [Operations](operations/troubleshooting.md): diagnostics, logging, and data handling

## Repository Layout

| Path | Purpose |
| --- | --- |
| `y_server/__init__.py` | Flask app creation, database binding, logging setup, runtime schema helpers |
| `y_server/routes/` | All HTTP routes, including optional modules |
| `y_server/modals.py` | SQLAlchemy models |
| `y_server/memory_embedding.py` | Ollama-backed embedding wrapper and fallback scoring helpers |
| `y_server/content_analysis/` | Sentiment and toxicity helpers |
| `y_server/utils.py` | Recommendation support functions |
| `y_server_run.py` | Development entrypoint |
| `wsgi.py` | WSGI entrypoint for Gunicorn or other servers |
| `gunicorn_config.py` | Default Gunicorn settings |
| `config_files/exp_config.json` | Default experiment configuration |
| `data_schema/database_clean_server.db` | SQLite seed database copied into new experiments |
| `experiments/` | Generated SQLite databases and logs |

## Quick Start

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements_server.txt
python y_server_run.py
```

The default configuration is loaded from `config_files/exp_config.json`. See [Configuration](configuration.md) for supported keys and [Running and Deployment](running-and-deployment.md) for production usage.
