import json
import logging, time
import os
import shutil
from logging.handlers import RotatingFileHandler

import flask_sqlalchemy
from pythonjsonlogger import jsonlogger
import sqlalchemy
import sqlalchemy.orm
from flask import Flask, request, g
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import inspect, text
from sqlalchemy.pool import NullPool


def _ensure_flask_sqlalchemy_legacy_compat() -> None:
    """
    Keep Flask-SQLAlchemy 2.x compatible with SQLAlchemy 2.x.
    """

    if not hasattr(sqlalchemy.orm, "relation") and hasattr(
        sqlalchemy.orm, "relationship"
    ):
        sqlalchemy.orm.relation = sqlalchemy.orm.relationship

    sqlalchemy_public = [
        "Column",
        "Integer",
        "BigInteger",
        "REAL",
        "Float",
        "Boolean",
        "String",
        "Text",
        "DateTime",
        "ForeignKey",
        "UniqueConstraint",
        "CheckConstraint",
        "PrimaryKeyConstraint",
        "ForeignKeyConstraint",
        "Index",
        "Table",
        "func",
        "text",
        "or_",
        "desc",
    ]
    orm_public = [
        "relationship",
        "relation",
        "dynamic_loader",
        "backref",
    ]

    if not hasattr(sqlalchemy, "__all__"):
        sqlalchemy.__all__ = [
            name for name in sqlalchemy_public if hasattr(sqlalchemy, name)
        ]
    if not hasattr(sqlalchemy.orm, "__all__"):
        sqlalchemy.orm.__all__ = [
            name for name in orm_public if hasattr(sqlalchemy.orm, name)
        ]

    session_base = getattr(flask_sqlalchemy, "SessionBase", None)
    signalling_session = getattr(flask_sqlalchemy, "SignallingSession", None)
    if session_base is not None and signalling_session is not None:
        original_get_bind = signalling_session.get_bind
        if not getattr(original_get_bind, "_ysocial_sa2_compat", False):

            def _compat_get_bind(self, mapper=None, clause=None):
                if mapper is not None:
                    try:
                        persist_selectable = mapper.persist_selectable
                    except AttributeError:
                        persist_selectable = mapper.mapped_table

                    info = getattr(persist_selectable, "info", {})
                    bind_key = info.get("bind_key")
                    if bind_key is not None:
                        state = flask_sqlalchemy.get_state(self.app)
                        return state.db.get_engine(self.app, bind=bind_key)
                return session_base.get_bind(self, mapper, clause=clause)

            _compat_get_bind._ysocial_sa2_compat = True
            signalling_session.get_bind = _compat_get_bind


_ensure_flask_sqlalchemy_legacy_compat()

metrics_logger = logging.getLogger("yserver.metrics")

log = logging.getLogger('werkzeug')
log.setLevel(logging.WARNING)


def get_engine_options(uri):
    """Get appropriate engine options based on database type."""
    if uri.startswith("sqlite"):
        return {
            "poolclass": NullPool,
            "connect_args": {"check_same_thread": False, "timeout": 30}
        }
    else:
        # PostgreSQL and other databases
        return {
            "poolclass": NullPool,
        }


def _register_request_logging(app):
    """Attach request duration logging for API metrics."""
    @app.before_request
    def start_timer():
        g.start_time = time.time()

    @app.after_request
    def log_request(response):
        if hasattr(g, "start_time"):
            duration = time.time() - g.start_time
            log = {
                "remote_addr": request.remote_addr,
                "method": request.method,
                "path": request.path,
                "status_code": response.status_code,
                "duration": round(duration, 4),
                "time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
            }
            try:
                from y_server.modals import Rounds

                current_round = Rounds.query.order_by(Rounds.id.desc()).first()
                if current_round:
                    log["day"] = current_round.day
                    log["hour"] = current_round.hour
            except Exception:
                pass

            logger = metrics_logger if metrics_logger.handlers else app.logger
            logger.info("request_complete", extra=log)
        return response


def _setup_file_logging(app, config, db_uri):
    """Configure JSON file logging to _server.log."""
    if not db_uri:
        return

    log_dir = None
    if isinstance(config, dict):
        data_path = config.get("data_path")
        if data_path:
            log_dir = data_path.rstrip(os.sep)

    if not log_dir:
        if db_uri.startswith("sqlite"):
            db_path = db_uri.replace("sqlite:///", "").replace("sqlite://", "")
            if db_path.startswith("../"):
                db_path = db_path[3:]
            log_dir = os.path.dirname(db_path) if os.path.dirname(db_path) else "experiments"
        else:
            base = os.getcwd().split("external")[0]
            db_name = db_uri.rsplit("/", 1)[-1]
            if db_name.startswith("experiments_"):
                db_name = db_name.replace("experiments_", "")
            log_dir = os.path.join(base, "y_web", "experiments", db_name)

    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, "_server.log")

    formatter = jsonlogger.JsonFormatter()
    file_handler = RotatingFileHandler(
        log_path,
        mode="a",
        maxBytes=10 * 1024 * 1024,  # 10MB
        backupCount=5,
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.INFO)

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.handlers.clear()
    app.logger.handlers.clear()
    root_logger.addHandler(file_handler)
    app.logger.addHandler(file_handler)
    app.logger.setLevel(logging.INFO)
    root_logger.propagate = False
    app.logger.propagate = False
    metrics_logger.handlers.clear()
    metrics_logger.setLevel(logging.INFO)
    metrics_logger.addHandler(file_handler)
    metrics_logger.propagate = False
    file_handler.flush()


def _ensure_image_post_schema():
    with app.app_context():
        inspector = inspect(db.engine)
        if "post" in inspector.get_table_names():
            post_columns = {col["name"] for col in inspector.get_columns("post")}
            with db.engine.begin() as conn:
                if "image_post_id" not in post_columns:
                    conn.execute(text("ALTER TABLE post ADD COLUMN image_post_id INTEGER"))
                if "dedupe_key" not in post_columns:
                    conn.execute(text("ALTER TABLE post ADD COLUMN dedupe_key VARCHAR(64)"))
                if "client_action_id" not in post_columns:
                    conn.execute(
                        text("ALTER TABLE post ADD COLUMN client_action_id VARCHAR(96)")
                    )

        with db.engine.begin() as conn:
            if db.engine.dialect.name == "postgresql":
                conn.execute(
                    text(
                        """
                        CREATE TABLE IF NOT EXISTS image_posts (
                            id SERIAL PRIMARY KEY,
                            url VARCHAR(500) NOT NULL,
                            source_url VARCHAR(500),
                            title VARCHAR(300),
                            subreddit VARCHAR(100),
                            description TEXT,
                            fetched_on VARCHAR(20),
                            used BOOLEAN DEFAULT FALSE,
                            local_path VARCHAR(500),
                            high_res_url VARCHAR(500)
                        )
                        """
                    )
                )
            else:
                conn.execute(
                    text(
                        """
                        CREATE TABLE IF NOT EXISTS image_posts (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            url VARCHAR(500) NOT NULL,
                            source_url VARCHAR(500),
                            title VARCHAR(300),
                            subreddit VARCHAR(100),
                            description TEXT,
                            fetched_on VARCHAR(20),
                            used BOOLEAN DEFAULT 0,
                            local_path VARCHAR(500),
                            high_res_url VARCHAR(500)
                        )
                        """
                    )
                )

            refreshed_columns = {
                col["name"] for col in inspect(db.engine).get_columns("image_posts")
            }
            if "local_path" not in refreshed_columns:
                conn.execute(text("ALTER TABLE image_posts ADD COLUMN local_path VARCHAR(500)"))
            if "high_res_url" not in refreshed_columns:
                conn.execute(text("ALTER TABLE image_posts ADD COLUMN high_res_url VARCHAR(500)"))


def _ensure_comment_dedupe_schema(app):
    """
    Ensure post table has comment dedupe columns/indexes.

    This runs before route registration so mapped Post columns exist in DB
    for both sqlite-backed and postgresql-backed experiment databases.
    """
    try:
        with app.app_context():
            inspector = inspect(db.engine)
            if "post" not in set(inspector.get_table_names()):
                return

            existing_cols = {c["name"] for c in inspector.get_columns("post")}
            with db.engine.begin() as conn:
                if "dedupe_key" not in existing_cols:
                    conn.execute(text("ALTER TABLE post ADD COLUMN dedupe_key VARCHAR(64)"))
                if "client_action_id" not in existing_cols:
                    conn.execute(text("ALTER TABLE post ADD COLUMN client_action_id VARCHAR(96)"))
                conn.execute(
                    text(
                        """
                        CREATE UNIQUE INDEX IF NOT EXISTS idx_post_comment_action_id_uniq
                        ON post (user_id, client_action_id)
                        WHERE client_action_id IS NOT NULL
                        """
                    )
                )
                conn.execute(
                    text(
                        """
                        CREATE UNIQUE INDEX IF NOT EXISTS idx_post_comment_dedupe_uniq
                        ON post (user_id, comment_to, round, dedupe_key)
                        WHERE comment_to <> -1 AND dedupe_key IS NOT NULL
                        """
                    )
                )
    except Exception as exc:
        app.logger.warning("comment_dedupe_schema_migration_failed", extra={"error": str(exc)})


def _ensure_moderation_schema(app):
    try:
        with app.app_context():
            from y_server.schema_migrations import ensure_moderation_schema

            ensure_moderation_schema(db.engine)
    except Exception as exc:
        app.logger.warning("moderation_schema_migration_failed", extra={"error": str(exc)})


config = {}
config_file = os.environ.get(
    "YSERVER_CONFIG", f"config_files{os.sep}exp_config.json"
)

try:
    # read the experiment configuration
    config = json.load(open(config_file))

    # Check for DATABASE_URL environment variable (PostgreSQL)
    database_url = os.environ.get("DATABASE_URL")

    if database_url and "postgresql" in database_url:
        # PostgreSQL mode
        app = Flask(__name__)
        app.config["SECRET_KEY"] = "4YrzfpQ4kGXjuP6w"
        app.config["SQLALCHEMY_DATABASE_URI"] = database_url
        app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
        app.config["SQLALCHEMY_ENGINE_OPTIONS"] = get_engine_options(database_url)
        db = SQLAlchemy(app)
    else:
        # SQLite mode (default)
        app = Flask(__name__)
        app.config["SECRET_KEY"] = "4YrzfpQ4kGXjuP6w"

        configured_db_uri = config.get("database_uri")
        if isinstance(configured_db_uri, str) and configured_db_uri.strip():
            if configured_db_uri.startswith("sqlite:"):
                uri = configured_db_uri
            else:
                db_path = os.path.abspath(configured_db_uri).replace("\\", "/")
                db_dir = os.path.dirname(db_path)
                if db_dir:
                    os.makedirs(db_dir, exist_ok=True)
                if (not os.path.exists(db_path)) or config.get("reset_db") == "True":
                    shutil.copyfile(
                        f"data_schema{os.sep}database_clean_server.db",
                        db_path,
                    )
                uri = f"sqlite:///{db_path}"
        else:
            # create the experiments folder
            if not os.path.exists(f".{os.sep}experiments"):
                os.mkdir(f".{os.sep}experiments")

            default_db = f"experiments{os.sep}{config['name']}.db"
            if (not os.path.exists(default_db)) or config["reset_db"] == "True":
                # copy the clean database to the experiments folder
                shutil.copyfile(
                    f"data_schema{os.sep}database_clean_server.db",
                    default_db,
                )
            uri = f"sqlite:///../experiments/{config['name']}.db"

        app.config["SQLALCHEMY_DATABASE_URI"] = uri
        app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
        app.config["SQLALCHEMY_ENGINE_OPTIONS"] = get_engine_options(uri)
        db = SQLAlchemy(app)

except:  # Y Web subprocess
    # base path
    BASE_DIR = os.path.dirname(os.path.abspath(__file__)).split("y_server")[0]

    # Check for DATABASE_URL environment variable (PostgreSQL)
    database_url = os.environ.get("DATABASE_URL")

    if database_url and "postgresql" in database_url:
        # PostgreSQL mode for Y Web subprocess
        app = Flask(__name__)
        app.config["SECRET_KEY"] = "4YrzfpQ4kGXjuP6w"
        app.config["SQLALCHEMY_DATABASE_URI"] = database_url
        app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
        app.config["SQLALCHEMY_ENGINE_OPTIONS"] = get_engine_options(database_url)
        db = SQLAlchemy(app)
    else:
        # SQLite mode for Y Web subprocess
        # create the experiments folder
        if not os.path.exists(f"{BASE_DIR}experiments"):
            os.mkdir(f"{BASE_DIR}experiments")
            shutil.copyfile(
                f"{BASE_DIR}data_schema{os.sep}database_clean_server.db",
                f"{BASE_DIR}experiments{os.sep}dummy.db",
            )

        app = Flask(__name__)
        app.config["SECRET_KEY"] = "4YrzfpQ4kGXjuP6w"
        uri = f"sqlite:///../experiments/dummy.db"
        app.config["SQLALCHEMY_DATABASE_URI"] = uri
        app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
        app.config["SQLALCHEMY_ENGINE_OPTIONS"] = get_engine_options(uri)
        db = SQLAlchemy(app)

_register_request_logging(app)
_setup_file_logging(app, config, app.config.get("SQLALCHEMY_DATABASE_URI"))
_ensure_image_post_schema()
_ensure_comment_dedupe_schema(app)
_ensure_moderation_schema(app)

from y_server.routes import *

try:
    from y_server.routes.content_management import configure_memory_embedding_from_config

    configure_memory_embedding_from_config(config)
except Exception:
    pass
