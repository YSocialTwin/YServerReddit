import json
import logging
import os
import traceback

from flask import request
from logging.handlers import RotatingFileHandler
from pythonjsonlogger import jsonlogger
from sqlalchemy.pool import NullPool

from y_server import app, db, _ensure_comment_dedupe_schema
from y_server.modals import (
    User_mgmt,
    Post,
    Reactions,
    Follow,
    Hashtags,
    Post_hashtags,
    Mentions,
    Post_emotions,
    Rounds,
    Recommendations,
    Websites,
    Articles,
    Voting,
    Interests,
    Post_topics,
    User_interest,
    Images,
    Article_topics,
)

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Define JSON log format
formatter = jsonlogger.JsonFormatter(
    fmt='%(asctime)s %(levelname)s %(name)s %(message)s %(pathname)s %(lineno)d',
    datefmt='%Y-%m-%dT%H:%M:%S'
)


def log_error(message):
    """Log error message to stderr."""
    import sys
    print(message, file=sys.stderr, flush=True)


def rebind_db(new_uri):
    """Rebind the database to a new URI without calling init_app."""
    from flask import current_app
    from sqlalchemy import create_engine

    log_error(f"rebind_db: Starting database rebind to {new_uri}")

    try:
        # Use NullPool for both SQLite and PostgreSQL to avoid connection pool issues
        if new_uri.startswith("sqlite"):
            log_error(f"rebind_db: Creating SQLite engine with NullPool")
            engine = create_engine(new_uri,
                                 poolclass=NullPool,
                                 connect_args={"check_same_thread": False, "timeout": 30})
        else:
            log_error(f"rebind_db: Creating PostgreSQL engine with NullPool")
            engine = create_engine(new_uri, poolclass=NullPool)

        with current_app.app_context():
            log_error(f"rebind_db: Removing current session")
            db.session.remove()
            log_error(f"rebind_db: Disposing current engine")
            db.engine.dispose()
            log_error(f"rebind_db: Configuring session with new engine")
            db.session.configure(bind=engine)
            log_error(f"rebind_db: Database rebind completed successfully")
    except Exception as e:
        log_error(f"rebind_db: CRITICAL ERROR during database rebind\nURI: {new_uri}\nError: {str(e)}\nTraceback: {traceback.format_exc()}")
        raise


@app.route("/change_db", methods=["POST"])
def change_db():
    """
    Change the database to the given name. Supports both SQLite and PostgreSQL.

    :param db_name: the name of the database
    :return: the status of the change
    """
    try:
        # get the data from the request
        data = json.loads(request.get_data())
        uri = data["path"]

        if "postgresql" in uri:
            # PostgreSQL configuration
            app.config["SQLALCHEMY_DATABASE_URI"] = uri
            app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
            app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
                "poolclass": NullPool,
            }
            # PostgreSQL URIs always use forward slashes
            db_path = os.path.join("experiments", uri.split("/")[-1].replace("experiments_", ""))
            rebind_db(uri)
            log_dir = db_path
            cwd = os.path.abspath(os.getcwd()).split("external")[0]
            cwd = os.path.join(cwd, "y_web")
            log_dir = os.path.join(cwd, log_dir)
        else:
            # SQLite configuration
            sqlite_uri = f"sqlite:////{data['path']}"
            app.config["SQLALCHEMY_DATABASE_URI"] = sqlite_uri
            app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
            app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
                "poolclass": NullPool,
                "pool_pre_ping": True,
                "connect_args": {
                    "check_same_thread": False,
                    "timeout": 30
                }
            }
            rebind_db(sqlite_uri)
            log_dir = uri.split("database_server.db")[0]

        # Ensure dedupe columns/indexes exist on the currently bound database.
        _ensure_comment_dedupe_schema(app)

        # Set up file logging
        if os.path.isabs(log_dir):
            log_path = os.path.join(log_dir, "_server.log")
        else:
            if os.name != "nt":  # POSIX
                log_path = os.path.join(f"{os.sep}{log_dir}", "_server.log")
            else:  # Windows
                drive = os.environ.get("SystemDrive", "C:")
                log_path = os.path.join(drive + os.sep, log_dir, "_server.log")

        # Create log directory if it doesn't exist
        os.makedirs(log_dir, exist_ok=True)

        # Remove all existing handlers to avoid duplicate logging
        logger.handlers.clear()
        app.logger.handlers.clear()

        # Create rotating file handler
        fileHandler = RotatingFileHandler(
            log_path,
            mode='a',
            maxBytes=10*1024*1024,  # 10MB
            backupCount=5
        )
        fileHandler.setFormatter(formatter)
        fileHandler.setLevel(logging.INFO)

        # Add handler to both root logger and Flask app logger
        logger.addHandler(fileHandler)
        app.logger.addHandler(fileHandler)
        app.logger.setLevel(logging.INFO)
        logger.propagate = False
        fileHandler.flush()

        app.logger.info(f"Database configuration successful. URI: {uri}, Log: {log_path}")
        return {"status": 200}

    except Exception as e:
        log_error(f"ERROR in change_db: {str(e)}\nTraceback: {traceback.format_exc()}")
        return {"status": 500, "error": str(e), "traceback": traceback.format_exc()}, 500


@app.route("/shutdown", methods=["POST"])
def shutdown_server():
    """
    Shutdown the server
    """
    shutdown = request.environ.get("werkzeug.server.shutdown")
    if shutdown is None:
        raise RuntimeError("Not running with the Werkzeug Server")
    shutdown()


@app.route("/reset", methods=["POST"])
def reset_experiment():
    """
    Reset the experiment.
    Delete all the data from the database.

    :return: the status of the reset
    """
    db.session.query(User_mgmt).delete()
    db.session.query(Post).delete()
    db.session.query(Reactions).delete()
    db.session.query(Follow).delete()
    db.session.query(Hashtags).delete()
    db.session.query(Post_hashtags).delete()
    db.session.query(Post_emotions).delete()
    db.session.query(Mentions).delete()
    db.session.query(Rounds).delete()
    db.session.query(Recommendations).delete()
    db.session.query(Websites).delete()
    db.session.query(Articles).delete()
    db.session.query(Interests).delete()
    db.session.query(User_interest).delete()
    db.session.query(Voting).delete()
    db.session.query(Post_topics).delete()
    db.session.query(Images).delete()
    db.session.query(Article_topics).delete()
    db.session.commit()
    return {"status": 200}
