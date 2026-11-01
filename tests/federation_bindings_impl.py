"""INV-17 in YServerReddit: accounts are reported by username and the global id comes from the coordinator, never from the local id."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

pytest.importorskip("ysocial_federation", reason="the federation SDK is not installed")

from ysocial_federation.envelope import Envelope  # noqa: E402
from ysocial_federation.schemas import MembershipChange  # noqa: E402

from y_server import app, db  # noqa: E402
from y_server.federation import reset_federation  # noqa: E402
from y_server.federation.adapter import YServerRedditAdapter  # noqa: E402
from y_server.federation import visibility  # noqa: E402
from y_server.modals import Follow, Interests, Post, Post_topics, Reactions, Recommendations, Rounds, User_mgmt  # noqa: E402
from sqlalchemy import select  # noqa: E402


def _post_json(client, path, payload):
    return client.post(path, data=json.dumps(payload), content_type="application/json")


@pytest.fixture()
def client(tmp_path):
    exp_dir = tmp_path / "experiment"
    exp_dir.mkdir()
    db_path = exp_dir / "database_server.db"
    shutil.copyfile(ROOT / "data_schema" / "database_clean_server.db", db_path)
    app.config["TESTING"] = True
    c = app.test_client()
    assert _post_json(c, "/change_db", {"path": str(db_path)}).status_code == 200
    with app.app_context():  # the clean template of this repository has no tables
        db.create_all()
        db.session.remove()
        for i in (1, 2, 3):
            db.session.add(User_mgmt(id=i, username=f"u{i}", password="x", email=f"u{i}@x.org", joined_on=0, language="it" if i == 2 else "en"))
        for iid, name in ((1, "sport"), (2, "music"), (3, "Cinema")):
            db.session.add(Interests(iid=iid, interest=name))
        if db.session.scalars(select(Rounds)).first() is None:
            db.session.add(Rounds(day=0, hour=0))
        db.session.commit()
    yield c
    reset_federation()
    with app.app_context():
        db.session.remove()


def _post(client, uid, text, crosspost=None):
    body = {"user_id": uid, "tweet": text, "emotions": [], "hashtags": [], "mentions": [], "topics": [], "tid": 1}
    if crosspost is not None:
        body["crosspost"] = crosspost
    assert _post_json(client, "/post", body).status_code == 200




@pytest.fixture()
def bound(client, tmp_path):
    a = YServerRedditAdapter("forum", 1, bindings_file=str(tmp_path / "bindings.db"), export_policy="all")
    app.config["federation_adapter"] = a
    with app.app_context():
        yield a
    app.config.pop("federation_adapter", None)


def test_accounts_are_offered_by_username_once(bound):
    offered = bound.account_bindings()
    assert offered == [{"username": f"u{i}", "local_account_id": str(i)} for i in (1, 2, 3)]
    bound.bindings_reported(offered, [{"global_agent_id": "12", "local_account_id": "1"}, {"global_agent_id": "30", "local_account_id": "3"}])
    assert bound.account_bindings() == []
    with app.app_context():
        db.session.add(User_mgmt(id=4, username="u4", password="x", email="u4@x.org", joined_on=0))
        db.session.commit()
    offered = bound.account_bindings()
    assert offered == [{"username": "u4", "local_account_id": "4"}]
    bound.bindings_reported(offered, [])
    assert bound.account_bindings() == []
    assert {"username": "u4", "local_account_id": "4"} in bound.account_bindings(retry=True)


def test_the_global_id_is_the_bound_one_and_never_the_local_id(bound):
    bound.bindings_reported(bound.account_bindings(), [{"global_agent_id": "12", "local_account_id": "1"}])
    assert bound.agent_of(1) == "12" and bound.user_of("12") == 1
    assert bound.agent_of(2) == "unbound:2"
    assert bound.user_of("2") is None and bound.user_of("3") is None


def test_membership_reaches_the_account_of_the_bound_agent(bound):
    bound.bindings_reported(bound.account_bindings(), [{"global_agent_id": "12", "local_account_id": "1"}])
    bound.apply_membership([MembershipChange("12", "dormant", 0, "initial"), MembershipChange("3", "dormant", 0, "initial")])
    assert bound._active(1) is False and bound._active(3) is True


def test_only_bound_accounts_are_exported(client, bound):
    bound.bindings_reported(bound.account_bindings(), [{"global_agent_id": "12", "local_account_id": "1"}])
    bound.export_outbox(0)  # fixes the watermark
    _post(client, 1, "from a federation agent")
    _post(client, 2, "from an account the federation does not know")
    out = bound.export_outbox(3)
    assert [e.payload["global_agent_id"] for e in out] == ["12"]


def test_a_restart_keeps_the_bindings(client, tmp_path):
    path = str(tmp_path / "again.db")
    first = YServerRedditAdapter("forum", 1, bindings_file=path)
    with app.app_context():
        first.bindings_reported(first.account_bindings(), [{"global_agent_id": "12", "local_account_id": "2"}])
        second = YServerRedditAdapter("forum", 1, bindings_file=path)
        assert second.agent_of(2) == "12" and second.account_bindings() == []


def test_without_a_bindings_file_nothing_changes(client):
    a = YServerRedditAdapter("forum", 1)
    with app.app_context():
        assert a.account_bindings() == [] and a.agent_of(2) == "2" and a.user_of("2") == 2
