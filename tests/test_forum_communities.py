"""Communities of the forum created and cross-posted by the agents (/create_topic, /crosspost_thread)."""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import os  # noqa: E402

_IMPORT_DB = Path(tempfile.gettempdir()) / "yserver_reddit_communities_import.db"
_IMPORT_CONFIG = Path(tempfile.gettempdir()) / "yserver_reddit_communities_import.json"
shutil.copyfile(ROOT / "data_schema" / "database_clean_server.db", _IMPORT_DB)
_IMPORT_CONFIG.write_text(
    json.dumps({"database_uri": f"sqlite:///{_IMPORT_DB}", "stress_reward_enabled": False})
)
os.environ["YSERVER_CONFIG"] = str(_IMPORT_CONFIG)

from y_server import app, db  # noqa: E402


def _post(client, path, payload):
    res = client.post(path, data=json.dumps(payload), content_type="application/json")
    return res.status_code, json.loads(res.data)


@pytest.fixture()
def client(tmp_path):
    exp_dir = tmp_path / "experiment"
    exp_dir.mkdir()
    db_path = exp_dir / "database_server.db"
    shutil.copyfile(ROOT / "data_schema" / "database_clean_server.db", db_path)
    app.config["TESTING"] = True
    client = app.test_client()
    assert _post(client, "/change_db", {"path": str(db_path)})[0] == 200
    assert _post(client, "/reset", {})[0] == 200
    with app.app_context():
        with db.engine.begin() as conn:
            conn.exec_driver_sql("INSERT INTO rounds (id, day, hour) VALUES (1, 0, 1)")
            for uid, name in ((1, "alice"), (2, "bob")):
                conn.exec_driver_sql(
                    "INSERT INTO user_mgmt (id, username, email, password, joined_on) VALUES (?, ?, ?, ?, ?)",
                    (uid, name, f"{name}@example.test", "pwd", 0),
                )
            conn.exec_driver_sql("INSERT INTO interests (iid, interest) VALUES (1, 'Linux')")
            conn.exec_driver_sql("INSERT INTO interests (iid, interest) VALUES (2, 'Windows')")
            conn.exec_driver_sql(
                "INSERT INTO post (id, tweet, round, user_id, comment_to, thread_id, shared_from) "
                "VALUES (10, 'TITLE: kernel 7\n\nbody', 1, 2, -1, 10, -1)"
            )
            conn.exec_driver_sql("INSERT INTO post_topics (post_id, topic_id) VALUES (10, 1)")
            conn.exec_driver_sql(
                "INSERT INTO post (id, tweet, round, user_id, comment_to, thread_id, shared_from) "
                "VALUES (11, 'a reply', 1, 1, 10, 10, -1)"
            )
    yield client


def _topics(client):
    return {t["name"]: t["id"] for t in json.loads(client.get("/get_topics").data)}


def test_create_topic_is_new_then_shared(client):
    code, res = _post(client, "/create_topic", {"user_id": 1, "name": "y/Home Brewing", "tid": 1})
    assert (code, res["created"], res["name"]) == (200, True, "Home Brewing")
    code, again = _post(client, "/create_topic", {"user_id": 2, "name": "  home brewing ", "tid": 1})
    assert (code, again["created"], again["id"]) == (200, False, res["id"])
    assert "Home Brewing" in _topics(client)


def test_create_topic_rejects_bad_names_and_respects_the_limit(client):
    assert _post(client, "/create_topic", {"user_id": 1, "name": "!!", "tid": 1})[0] == 400
    assert _post(client, "/create_topic", {"user_id": 99, "name": "Gardening", "tid": 1})[0] == 404
    # two topics exist already: a limit of 2 admits no new one, an existing name is still answered
    assert _post(client, "/create_topic", {"user_id": 1, "name": "Gardening", "tid": 1, "max_topics": 2})[0] == 409
    assert _post(client, "/create_topic", {"user_id": 1, "name": "linux", "tid": 1, "max_topics": 2})[0] == 200


def test_long_names_are_cut_to_twenty_characters(client):
    _, res = _post(client, "/create_topic", {"user_id": 1, "name": "Competitive speedrunning of retro games", "tid": 1})
    assert len(res["name"]) <= 20


def test_crosspost_creates_a_root_in_the_second_community(client):
    code, res = _post(client, "/crosspost_thread", {"user_id": 1, "post_id": 10, "topic_id": 2, "text": "x-post from y/Linux", "tid": 1})
    assert code == 200 and "id" in res
    with app.app_context():
        row = db.session.execute(db.text("SELECT comment_to, shared_from, user_id, thread_id FROM post WHERE id=:i"), {"i": res["id"]}).one()
        assert tuple(row) == (-1, 10, 1, res["id"])
        topic = db.session.execute(db.text("SELECT topic_id FROM post_topics WHERE post_id=:i"), {"i": res["id"]}).scalar()
        assert topic == 2
    # the same thread to the same community again: nothing new
    code, again = _post(client, "/crosspost_thread", {"user_id": 1, "post_id": 10, "topic_id": 2, "text": "again", "tid": 1})
    assert code == 200 and again["deduped"] and again["id"] == res["id"]


def test_crosspost_refuses_the_same_community_comments_and_unknowns(client):
    base = {"user_id": 1, "topic_id": 1, "text": "x", "tid": 1}
    assert _post(client, "/crosspost_thread", {**base, "post_id": 10})[0] == 409  # already in Linux
    assert _post(client, "/crosspost_thread", {**base, "post_id": 11, "topic_id": 2})[0] == 400  # a comment
    assert _post(client, "/crosspost_thread", {**base, "post_id": 99, "topic_id": 2})[0] == 404
    assert _post(client, "/crosspost_thread", {**base, "post_id": 10, "topic_id": 99})[0] == 404


def test_crosspost_of_a_crosspost_points_to_the_original(client):
    _, first = _post(client, "/crosspost_thread", {"user_id": 1, "post_id": 10, "topic_id": 2, "text": "x-post", "tid": 1})
    _post(client, "/create_topic", {"user_id": 2, "name": "Gaming", "tid": 1})
    gaming = _topics(client)["Gaming"]
    _, second = _post(client, "/crosspost_thread", {"user_id": 2, "post_id": first["id"], "topic_id": gaming, "text": "x-post", "tid": 1})
    with app.app_context():
        assert db.session.execute(db.text("SELECT shared_from FROM post WHERE id=:i"), {"i": second["id"]}).scalar() == 10


def test_post_answers_the_id_of_the_new_post(client):
    code, res = _post(client, "/post", {"user_id": 1, "tweet": "TITLE: hello\n\nworld", "tid": 1, "topics": [1], "emotions": [], "hashtags": [], "mentions": []})
    assert code == 200 and isinstance(res["id"], int)
