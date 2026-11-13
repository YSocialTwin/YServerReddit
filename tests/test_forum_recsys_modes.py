"""Contract tests for ``POST /read`` (forum content recommenders)."""

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

_IMPORT_DB = Path(tempfile.gettempdir()) / "yserver_reddit_recsys_import.db"
_IMPORT_CONFIG = Path(tempfile.gettempdir()) / "yserver_reddit_recsys_import.json"
shutil.copyfile(ROOT / "data_schema" / "database_clean_server.db", _IMPORT_DB)
_IMPORT_CONFIG.write_text(
    json.dumps({"database_uri": f"sqlite:///{_IMPORT_DB}", "stress_reward_enabled": False})
)
os.environ["YSERVER_CONFIG"] = str(_IMPORT_CONFIG)

from y_server import app, db  # noqa: E402

ALL_MODES = [
    "default",
    "rchrono",
    "rchrono_popularity",
    "rchrono_followers",
    "rchrono_followers_popularity",
    "top",
    "hot",
    "most_commented",
    "rchrono_comments",
    "common_interests",
    "common_user_interests",
    "similar_users_react",
    "similar_users_posts",
]


def _post_json(client, path, payload):
    return client.post(path, data=json.dumps(payload), content_type="application/json")


@pytest.fixture()
def client(tmp_path):
    exp_dir = tmp_path / "experiment"
    exp_dir.mkdir()
    db_path = exp_dir / "database_server.db"
    shutil.copyfile(ROOT / "data_schema" / "database_clean_server.db", db_path)
    app.config["TESTING"] = True
    client = app.test_client()
    assert _post_json(client, "/change_db", {"path": str(db_path)}).status_code == 200
    assert _post_json(client, "/reset", {}).status_code == 200
    yield client


def _exec(sql, params=()):
    with db.engine.begin() as conn:
        conn.exec_driver_sql(sql, params)


def _seed():
    """Viewer 1 follows 2; authors 2, 3 and 4 wrote threads over rounds 1..20."""
    for rid in range(1, 21):
        _exec("INSERT INTO rounds (id, day, hour) VALUES (?, ?, ?)", (rid, 0, rid))
    for uid, name in ((1, "viewer"), (2, "ann"), (3, "bob"), (4, "cat")):
        _exec(
            "INSERT INTO user_mgmt (id, username, email, password, joined_on, leaning, age, "
            "language, education_level, gender, toxicity, oe, co, ex, ag, ne) "
            "VALUES (?, ?, ?, 'pwd', 0, 'neutral', 30, 'en', 'hs', 'f', 'no', 'a', 'b', 'c', 'd', 'e')",
            (uid, name, f"{name}@example.test"),
        )
    _exec("INSERT INTO follow (follower_id, user_id, action, round) VALUES (2, 1, 'follow', 1)")
    for iid in (1, 2):
        _exec("INSERT INTO interests (iid, interest) VALUES (?, ?)", (iid, f"t{iid}"))
        _exec(
            "INSERT INTO user_interest (user_id, interest_id, round_id) VALUES (1, ?, 1)", (iid,)
        )
    # (id, author, round, reaction_count)
    threads = [
        (1, 2, 2, 0),
        (2, 3, 4, 7),
        (3, 4, 6, 3),
        (4, 2, 8, 0),
        (5, 3, 10, 12),
        (6, 4, 12, 0),
        (7, 2, 14, 1),
        (8, 3, 16, 0),
    ]
    for pid, author, rnd, rc in threads:
        _exec(
            "INSERT INTO post (id, tweet, round, user_id, comment_to, thread_id, reaction_count) "
            "VALUES (?, 'x', ?, ?, -1, ?, ?)",
            (pid, rnd, author, pid, rc),
        )
        _exec("INSERT INTO post_topics (post_id, topic_id) VALUES (?, 1)", (pid,))
    # two comments on thread 3 (a comment is a post with comment_to != -1)
    for cid in (101, 102):
        _exec(
            "INSERT INTO post (id, tweet, round, user_id, comment_to, thread_id, reaction_count) "
            "VALUES (?, 'c', 17, 3, 3, 3, 0)",
            (cid,),
        )
    # votes: thread 5 net +2, thread 2 net +1, thread 3 net -1
    votes = [(5, 2, "like"), (5, 3, "like"), (2, 4, "like"), (3, 2, "dislike")]
    for pid, uid, kind in votes:
        _exec(
            "INSERT INTO reactions (round, user_id, post_id, type) VALUES (18, ?, ?, ?)",
            (uid, pid, kind),
        )


def _read(client, mode, **extra):
    payload = {"limit": 5, "mode": mode, "visibility_rounds": 36, "uid": 1}
    payload.update(extra)
    resp = _post_json(client, "/read", payload)
    assert resp.status_code == 200, resp.data
    return json.loads(resp.data)


@pytest.mark.parametrize("mode", ALL_MODES)
def test_every_mode_returns_a_list_of_post_ids(client, mode):
    with app.app_context():
        _seed()
    ids = _read(client, mode)
    assert isinstance(ids, list)
    assert all(isinstance(i, int) for i in ids)
    assert len(ids) == len(set(ids)), "no duplicated posts in a feed"


@pytest.mark.parametrize("mode", ALL_MODES)
def test_every_mode_honours_limit(client, mode):
    with app.app_context():
        _seed()
    assert len(_read(client, mode, limit=3)) <= 3


@pytest.mark.parametrize("mode", ["rchrono", "rchrono_popularity", "top", "hot"])
def test_limit_larger_than_ten_is_honoured(client, mode):
    with app.app_context():
        _seed()
    # 8 threads (+2 comments for the non-thread-only modes) are available to user 1
    assert len(_read(client, mode, limit=20)) >= 8


def test_rchrono_is_newest_first(client):
    with app.app_context():
        _seed()
    ids = _read(client, "rchrono", limit=3)
    assert ids == sorted(ids, reverse=True)


def test_popularity_orders_by_reactions(client):
    with app.app_context():
        _seed()
    ids = _read(client, "rchrono_popularity", limit=3)
    assert ids[:3] == [5, 2, 3]


def test_followers_popularity_uses_reactions_and_quota(client):
    with app.app_context():
        _seed()
    # followed account 2 owns posts 1, 4, 7 (reactions 0, 0, 1)
    ids = _read(
        client, "rchrono_followers_popularity", limit=5, followers_ratio=0.6
    )
    assert ids[:3] == [7, 4, 1]
    assert set(ids[3:]).isdisjoint({1, 4, 7})


def test_followers_mode_does_not_duplicate_posts(client):
    with app.app_context():
        _seed()
    ids = _read(client, "rchrono_followers", limit=10, followers_ratio=0.5)
    assert len(ids) == len(set(ids))


def test_articles_flag_is_understood(client):
    with app.app_context():
        _seed()
    # no news pages exist: with the flag the feed is empty rather than a normal feed
    assert _read(client, "rchrono", articles=True) == []
    assert _read(client, "rchrono_followers_popularity", articles=True) == []


def test_hot_ranks_new_unvoted_posts_by_recency(client):
    with app.app_context():
        _seed()
    from y_server.routes.content_management import _hot_base_score

    assert _hot_base_score(0, 20) > _hot_base_score(0, 10)
    # a fresh unvoted post is not buried under an old upvoted one
    assert _hot_base_score(0, 120) > _hot_base_score(1, 10)
    # votes still matter at equal age; downvotes sink
    assert _hot_base_score(5, 10) > _hot_base_score(1, 10) > _hot_base_score(-1, 10)


def test_top_and_most_commented(client):
    with app.app_context():
        _seed()
    assert _read(client, "top", limit=3)[:2] == [5, 2]
    assert _read(client, "most_commented", limit=1) == [3]


def test_rchrono_comments_ranks_by_comment_count_with_follower_quota(client):
    with app.app_context():
        _seed()
    # thread 3 (author 4) has 2 comments; followed account 2 owns threads 1, 4, 7
    ids = _read(client, "rchrono_comments", limit=4, followers_ratio=0.5)
    assert len(ids) == 4 and len(set(ids)) == 4
    assert ids[:2] == [7, 4]
    assert ids[2] == 3


def test_unfollow_removes_account_from_follower_feed(client):
    with app.app_context():
        _seed()
        _exec("INSERT INTO follow (follower_id, user_id, action, round) VALUES (2, 1, 'unfollow', 5)")
    ids = _read(client, "rchrono_followers", limit=5, followers_ratio=1)
    assert ids == []
