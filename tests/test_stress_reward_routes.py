from __future__ import annotations

import json
import shutil
import sys
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from y_server import app, db
from y_server.modals import StressReward
from y_server.schema_migrations import ensure_moderation_schema


def _post_json(client, path, payload):
    return client.post(path, data=json.dumps(payload), content_type="application/json")


@pytest.fixture()
def client(tmp_path):
    exp_dir = tmp_path / "experiment"
    exp_dir.mkdir()
    db_path = exp_dir / "database_server.db"
    shutil.copyfile(ROOT / "data_schema" / "database_clean_server.db", db_path)

    app.config["TESTING"] = True
    app.config["stress_reward_enabled"] = True
    client = app.test_client()

    assert _post_json(client, "/change_db", {"path": str(db_path)}).status_code == 200
    assert _post_json(client, "/reset", {}).status_code == 200
    with app.app_context():
        ensure_moderation_schema(db.engine)
        db.session.remove()

    yield client

    with app.app_context():
        db.session.remove()


def test_set_and_get_stress_reward_roundtrip(client):
    with app.app_context():
        with db.engine.begin() as conn:
            conn.exec_driver_sql(
                "INSERT INTO user_mgmt (username, email, password, joined_on) VALUES (?, ?, ?, ?)",
                ("stress-user", "", "pwd", 0),
            )
            user_id = conn.exec_driver_sql(
                "SELECT id FROM user_mgmt WHERE username = ?", ("stress-user",)
            ).scalar()
            for rid in (1, 2, 3):
                conn.exec_driver_sql(
                    "INSERT OR IGNORE INTO rounds (id, day, hour) VALUES (?, ?, ?)",
                    (rid, 0, rid),
                )
            conn.exec_driver_sql(
                "INSERT INTO stress_reward (id, uid, variable, value, type, tid) VALUES (?, ?, ?, ?, ?, ?)",
                (str(uuid.uuid4()), user_id, "stress", 0.2, "aggregate", 1),
            )
            conn.exec_driver_sql(
                "INSERT INTO stress_reward (id, uid, variable, value, type, tid) VALUES (?, ?, ?, ?, ?, ?)",
                (str(uuid.uuid4()), user_id, "reward", 0.4, "aggregate", 1),
            )

    set_resp = _post_json(
        client,
        "/set_stress_reward_variations",
        {
            "user_id": user_id,
            "tid": 2,
            "action": "reaction:like",
            "variations": [
                {"variable": "stress", "value": 0.1},
                {"variable": "reward", "value": -0.05},
            ],
        },
    )
    assert json.loads(set_resp.data)["written"] == 2

    get_resp = _post_json(client, "/get_stress_reward", {"user_id": user_id, "tid": 3})
    payload = json.loads(get_resp.data)
    assert payload["stress"] == pytest.approx(0.3)
    assert payload["reward"] == pytest.approx(0.35)

    with app.app_context():
        aggregates = StressReward.query.filter_by(uid=user_id, type="aggregate", tid=3).all()
        aggregate_map = {row.variable: row.value for row in aggregates}
        assert aggregate_map["stress"] == pytest.approx(0.3)
        assert aggregate_map["reward"] == pytest.approx(0.35)
        variation_actions = {
            (row.variable, row.value): row.action
            for row in StressReward.query.filter_by(uid=user_id, type="variation", tid=2).all()
        }
        assert variation_actions[("stress", 0.1)] == "reaction:like"
        assert variation_actions[("reward", -0.05)] == "reaction:like"


def test_churn_route_accepts_explicit_user_id(client):
    with app.app_context():
        with db.engine.begin() as conn:
            conn.exec_driver_sql(
                "INSERT INTO user_mgmt (username, email, password, joined_on) VALUES (?, ?, ?, ?)",
                ("explicit-churn-user", "", "pwd", 0),
            )
            user_id = conn.exec_driver_sql(
                "SELECT id FROM user_mgmt WHERE username = ?", ("explicit-churn-user",)
            ).scalar()

    response = _post_json(client, "/churn", {"user_id": user_id, "left_on": 9})
    payload = json.loads(response.data)
    assert payload["status"] == 200
    assert str(user_id) in payload["removed"]

    with app.app_context():
        row = db.session.execute(
            db.text("SELECT left_on FROM user_mgmt WHERE id = :user_id"),
            {"user_id": user_id},
        ).scalar()
        assert row == 9
