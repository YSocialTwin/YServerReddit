import json
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from y_server import app, db
from y_server.modals import Agent_Opinion, Interests


def _post_json(client, path, payload):
    return client.post(
        path,
        data=json.dumps(payload),
        content_type="application/json",
    )


def _register_user(client, *, name, email, leaning="center"):
    resp = _post_json(
        client,
        "/register",
        {
            "name": name,
            "email": email,
            "password": "secret",
            "leaning": leaning,
            "age": 30,
            "user_type": "agent",
            "oe": "0.5",
            "co": "0.5",
            "ex": "0.5",
            "ag": "0.5",
            "ne": "0.5",
            "language": "en",
            "education_level": "college",
            "joined_on": 0,
            "round_actions": 5,
            "owner": "tests",
            "gender": "na",
            "nationality": "IT",
            "toxicity": "low",
            "daily_activity_level": 1,
            "profession": "tester",
        },
    )
    assert resp.status_code == 200


@pytest.fixture()
def client(tmp_path):
    exp_dir = tmp_path / "experiment"
    exp_dir.mkdir()
    db_path = exp_dir / "database_server.db"
    shutil.copyfile(ROOT / "data_schema" / "database_clean_server.db", db_path)

    app.config["TESTING"] = True
    client = app.test_client()

    resp = _post_json(client, "/change_db", {"path": str(db_path)})
    assert resp.status_code == 200
    resp = _post_json(client, "/reset", {})
    assert resp.status_code == 200

    with app.app_context():
        db.session.remove()

    yield client

    with app.app_context():
        db.session.remove()


def test_set_and_get_user_opinions_returns_latest_per_topic(client):
    _register_user(client, name="alice", email="alice@example.org")
    _post_json(client, "/set_interests", ["climate", "economy"])

    resp = _post_json(
        client,
        "/set_user_opinions",
        {
            "user_id": 1,
            "round": 1,
            "opinions": {"climate": 0.25, "economy": -0.4},
            "id_interacted_with": -1,
            "id_post": -1,
        },
    )
    assert resp.status_code == 200

    resp = _post_json(
        client,
        "/set_user_opinions",
        {
            "user_id": 1,
            "round": 2,
            "opinions": {"climate": 0.6},
            "id_interacted_with": -1,
            "id_post": -1,
        },
    )
    assert resp.status_code == 200

    resp = _post_json(client, "/get_user_opinions", {"user_id": 1})
    assert resp.status_code == 200
    body = json.loads(resp.get_data(as_text=True))

    assert body["climate"][0] == pytest.approx(0.6)
    assert body["economy"][0] == pytest.approx(-0.4)

    with app.app_context():
        climate = Interests.query.filter_by(interest="climate").first()
        economy = Interests.query.filter_by(interest="economy").first()
        assert body["climate"][1] == climate.iid
        assert body["economy"][1] == economy.iid
        assert Agent_Opinion.query.count() == 3


def test_get_users_opinions_returns_latest_followed_user_opinions(client):
    _register_user(client, name="alice", email="alice@example.org")
    _register_user(client, name="bob", email="bob@example.org")

    resp = _post_json(
        client,
        "/follow",
        {"user_id": 1, "target": 2, "action": "follow", "tid": 1},
    )
    assert resp.status_code == 200

    resp = _post_json(
        client,
        "/set_user_opinions",
        {
            "user_id": 2,
            "round": 1,
            "opinions": {"federation": 0.2},
            "id_interacted_with": 1,
            "id_post": -1,
        },
    )
    assert resp.status_code == 200

    resp = _post_json(
        client,
        "/set_user_opinions",
        {
            "user_id": 2,
            "round": 2,
            "opinions": {"federation": 0.8},
            "id_interacted_with": 1,
            "id_post": -1,
        },
    )
    assert resp.status_code == 200

    resp = _post_json(client, "/get_users_opinions", {"user_id": 1, "topic": "federation"})
    assert resp.status_code == 200
    body = json.loads(resp.get_data(as_text=True))

    assert body == [pytest.approx(0.8)]


def test_reset_experiment_clears_agent_opinions(client):
    _register_user(client, name="alice", email="alice@example.org")

    resp = _post_json(
        client,
        "/set_user_opinions",
        {
            "user_id": 1,
            "round": 1,
            "opinions": {"topic-a": 0.1},
            "id_interacted_with": -1,
            "id_post": -1,
        },
    )
    assert resp.status_code == 200

    with app.app_context():
        assert Agent_Opinion.query.count() == 1

    resp = _post_json(client, "/reset", {})
    assert resp.status_code == 200

    with app.app_context():
        assert Agent_Opinion.query.count() == 0


def test_user_interests_still_work_with_opinion_support_present(client):
    _register_user(client, name="alice", email="alice@example.org")
    _post_json(client, "/set_interests", ["alpha", "beta"])

    with app.app_context():
        alpha = Interests.query.filter_by(interest="alpha").first()
        beta = Interests.query.filter_by(interest="beta").first()

    resp = _post_json(
        client,
        "/set_user_interests",
        {
            "user_id": 1,
            "interests": [alpha.iid, beta.iid],
            "round": 3,
        },
    )
    assert resp.status_code == 200

    resp = client.get(
        "/get_user_interests",
        data=json.dumps(
            {
                "user_id": 1,
                "round_id": 3,
                "n_interests": 5,
                "time_window": 10,
            }
        ),
        content_type="application/json",
    )
    assert resp.status_code == 200
    body = json.loads(resp.get_data(as_text=True))

    assert {row["topic"] for row in body} == {"alpha", "beta"}
