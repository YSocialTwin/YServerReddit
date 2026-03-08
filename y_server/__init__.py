from flask import Flask, request, g
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import inspect, text
from sqlalchemy.pool import NullPool
import json
import shutil
import os
import logging, time

log = logging.getLogger('werkzeug')
log.setLevel(logging.WARNING)

try:
    # read the experiment configuration
    config = json.load(open(f"config_files{os.sep}exp_config.json"))
    database_url = os.environ.get("DATABASE_URL")

    if not database_url:
        if not os.path.exists(f".{os.sep}experiments"):
            os.mkdir(f".{os.sep}experiments")

        if (
            not os.path.exists(f"experiments{os.sep}{config['name']}.db")
            or config["reset_db"] == "True"
        ):
            shutil.copyfile(
                f"data_schema{os.sep}database_clean_server.db",
                f"experiments{os.sep}{config['name']}.db",
            )

    app = Flask(__name__)
    app.config["SECRET_KEY"] = "4YrzfpQ4kGXjuP6w"
    if database_url and "postgresql" in database_url:
        app.config["SQLALCHEMY_DATABASE_URI"] = database_url
        app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {"poolclass": NullPool}
    else:
        app.config[
            "SQLALCHEMY_DATABASE_URI"
        ] = f"sqlite:///../experiments/{config['name']}.db"
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    db = SQLAlchemy(app)

except:  # Y Web subprocess
    # base path
    BASE_DIR = os.path.dirname(os.path.abspath(__file__)).split("y_server")[0]

    # create the experiments folder
    if not os.path.exists(f"{BASE_DIR}experiments"):
        os.mkdir(f"{BASE_DIR}experiments")
        shutil.copyfile(
            f"{BASE_DIR}data_schema{os.sep}database_clean_server.db",
            f"{BASE_DIR}experiments{os.sep}dummy.db",
        )

    app = Flask(__name__)
    app.config["SECRET_KEY"] = "4YrzfpQ4kGXjuP6w"
    app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///../experiments/dummy.db"
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    # Manually add check_same_thread=False
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
        "connect_args": {"check_same_thread": False}
    }

    db = SQLAlchemy(app)

    # Log the request duration
    @app.before_request
    def start_timer():
        g.start_time = time.time()


    @app.after_request
    def log_request(response):
        if hasattr(g, 'start_time'):
            duration = time.time() - g.start_time
            log = {
                "remote_addr": request.remote_addr,
                "method": request.method,
                "path": request.path,
                "status_code": response.status_code,
                "duration": round(duration, 4),
                "time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
            }

            logging.info(log)
        return response


def _ensure_image_post_schema():
    with app.app_context():
        inspector = inspect(db.engine)
        if "post" in inspector.get_table_names():
            post_columns = {col["name"] for col in inspector.get_columns("post")}
            if "image_post_id" not in post_columns:
                with db.engine.begin() as conn:
                    conn.execute(text("ALTER TABLE post ADD COLUMN image_post_id INTEGER"))

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


_ensure_image_post_schema()

from y_server.routes import *
