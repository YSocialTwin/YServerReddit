from __future__ import annotations

from sqlalchemy import inspect, text


def ensure_moderation_schema(engine) -> None:
    """
    Ensure moderation additive schema exists for legacy experiment databases.
    """
    from y_server.modals import Reported, SysMessage

    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())

    if "post" in table_names:
        post_columns = {column["name"] for column in inspector.get_columns("post")}
        if "moderated" not in post_columns:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE post ADD COLUMN moderated INTEGER DEFAULT 0"))

    SysMessage.__table__.create(bind=engine, checkfirst=True)
    Reported.__table__.create(bind=engine, checkfirst=True)
