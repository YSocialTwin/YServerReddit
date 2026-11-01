"""H5b: human participants of the forum: gateway (logic, HTTP, offline) and their place in the federation rules."""

import json

import pytest

from federation_bus_impl import _post_json, client  # noqa: F401  (fixture)
from y_server import app, db
from y_server.federation.adapter import YServerRedditAdapter
from y_server.gateway.core import ForumHumanGateway, RateLimiter
from y_server.gateway.http_app import TOKEN_HEADER, create_gateway_app
from y_server.gateway.offline import client_call, handle_offline
from y_server.modals import Follow, Post, Reactions, User_mgmt
from sqlalchemy import select

pytest.importorskip("ysocial_federation")

ANN = {"username": "ann", "email": "ann@x.org", "language": "it", "age": 30}
WHO = {"username": "ann", "email": "ann@x.org"}


@pytest.fixture()
def gw(client):  # noqa: F811
    with app.app_context():
        yield ForumHumanGateway(client_call(client), RateLimiter(burst=50, per_second=50))


def _register(gw, key="u1", account=ANN):
    status, out = gw.register(key, account)
    assert status == 200, out
    return out["account_id"]


def test_register_creates_a_person_once_and_refuses_the_username_of_an_agent(gw):
    uid = _register(gw)
    user = db.session.get(User_mgmt, uid)
    assert (user.user_type, user.username, user.language, user.age) == ("human", "ann", "it", 30)
    assert gw.register("u1", ANN) == (200, {"ok": True, "account_id": uid, "created": False})
    assert gw.register("u1", dict(ANN, username="u1", email="u1@x.org"))[0] == 409  # fixture account u1 is an agent
    assert gw.register("", ANN)[0] == 400 and gw.register("k", {"username": "x"})[0] == 400
    assert gw.register("k", dict(ANN, username="x" * 16))[0] == 400


def test_a_person_posts_comments_reacts_follows_and_reports_through_the_routes_of_the_server(gw):
    uid = _register(gw)
    status, out = gw.act(WHO, [{"action": "post", "text": "My thread", "topics": ["forged"], "tid": 99, "user_id": 1}])
    assert status == 200 and out["ok"] is True
    post = db.session.scalars(select(Post).where(Post.user_id == uid, Post.comment_to == -1)).one()
    assert post.tweet == "My thread" and post.round != 99 and post.thread_id == post.id
    status, out = gw.act(WHO, [{"action": "comment", "post_id": post.id, "text": "a comment"},
                               {"action": "react", "post_id": post.id, "type": "like"},
                               {"action": "follow", "target": 2},
                               {"action": "report", "post_id": post.id, "type": "toxic"}])
    assert status == 200 and all(r["ok"] for r in out["results"]), out
    assert db.session.scalars(select(Post).where(Post.user_id == uid, Post.comment_to == post.id)).one().tweet == "a comment"
    assert db.session.scalars(select(Reactions).where(Reactions.user_id == uid)).one().type == "like"
    assert db.session.scalars(select(Follow).where(Follow.user_id == uid, Follow.follower_id == 2)).one().action == "follow"
    gw.act(WHO, [{"action": "unfollow", "target": 2}])
    assert db.session.scalars(select(Follow).where(Follow.user_id == uid).order_by(Follow.id.desc())).first().action == "unfollow"


@pytest.mark.parametrize("action", [{"action": "share", "post_id": 1}, {"action": "post"}, {"action": "post", "text": "x" * 4001},
                                    {"action": "react", "post_id": 1, "type": "love"}, {"action": "comment", "text": "t"},
                                    {"action": "follow", "target": "2"}, {"action": "report", "post_id": 1, "type": "x"},
                                    {"action": "post", "text": "t", "crosspost_to": "forum"}, "post"])
def test_invalid_actions_are_refused_and_share_does_not_exist_in_the_forum(gw, action):
    _register(gw)
    status, out = gw.act(WHO, [action])
    assert status == 200 and out["ok"] is False and out["results"][0]["status"] == 400
    assert db.session.scalars(select(Post).where(Post.user_id > 3)).first() is None


def test_only_persons_are_served_and_the_request_is_bounded(client):  # noqa: F811
    with app.app_context():
        g = ForumHumanGateway(client_call(client), RateLimiter(burst=2, per_second=0.0001))
        assert g.act({"username": "u1", "email": "u1@x.org"}, [{"action": "post", "text": "t"}])[0] == 403  # an agent
        assert g.act({"username": "nobody", "email": "n@x.org"}, [{"action": "post", "text": "t"}])[0] == 404
        assert g.act({"username": "ann"}, [])[0] == 400
        _register(g)
        assert g.act(WHO, [])[0] == 400 and g.act(WHO, [{"action": "post", "text": "t"}] * 21)[0] == 400
        assert g.act(WHO, [{"action": "react", "post_id": 1, "type": "like"}] * 2)[0] == 200
        assert g.act(WHO, [{"action": "react", "post_id": 1, "type": "like"}])[0] == 429
        assert g.state(WHO)[1]["round"]["id"] >= 1


def test_the_http_transport_needs_the_token(gw):
    app_ = create_gateway_app(gw, "secret")
    c = app_.test_client()
    assert c.get("/health").status_code == 401 and c.get("/health", headers={TOKEN_HEADER: "nope"}).status_code == 401
    h = {TOKEN_HEADER: "secret"}
    assert c.get("/health", headers=h).get_json() == {"ok": True}
    r = c.post("/register", json={"key": "u1", "account": ANN}, headers=h)
    assert r.status_code == 200 and r.get_json()["created"] is True
    r = c.post("/act", json=dict(WHO, actions=[{"action": "post", "text": "over http"}]), headers=h)
    assert r.status_code == 200 and r.get_json()["ok"] is True
    assert c.post("/state", json=WHO, headers=h).get_json()["username"] == "ann"
    assert c.post("/act", data="not json", headers=h).status_code == 400
    with pytest.raises(ValueError):
        create_gateway_app(gw, "")


# -- federation ------------------------------------------------------------------------------------------
@pytest.fixture()
def fed(client):  # noqa: F811
    a = YServerRedditAdapter("forum", 1, accounts={"g1": 1, "g2": 2, "g3": 3}, crosspost_mode="agent_action")
    app.config["federation_adapter"] = a
    with app.app_context():
        yield a
    app.config.pop("federation_adapter", None)


def test_a_person_with_a_human_identity_crossposts_with_its_own_identity(client, fed):  # noqa: F811
    g = ForumHumanGateway(client_call(client), RateLimiter(burst=50, per_second=50))
    uid = _register(g)
    fed._accounts["human:7"], fed._by_user[uid] = uid, "human:7"
    fed.set_destinations({"human:7": [{"member": "micro", "local_account_id": "501"}]})
    fed.export_outbox(0)
    status, out = g.act(WHO, [{"action": "post", "text": "from a person", "crosspost_to": [{"member": "micro"}, {"member": "micro", "text": "dup"}]}])
    assert status == 200 and out["ok"]
    sent = fed.export_outbox(3)
    assert [(e.payload_type, e.target_member, e.payload["global_agent_id"], e.payload["target_account_id"], e.payload["text_excerpt"])
            for e in sent] == [("crosspost.v1", "micro", "human:7", "501", "from a person")]


def test_a_person_without_an_identity_never_leaves_and_never_gets_the_id_of_an_agent(client, fed):  # noqa: F811
    g = ForumHumanGateway(client_call(client), RateLimiter(burst=50, per_second=50))
    uid = _register(g)
    assert fed.agent_of(uid) == f"human:local-{uid}" and fed.agent_of(1) == "g1" and fed.agent_of(99) == "99"
    fed.export_outbox(0)
    g.act(WHO, [{"action": "post", "text": "local only", "crosspost_to": [{"member": "micro"}]}])
    assert fed.export_outbox(3) == []


def test_persons_are_outside_the_rules_of_the_agents(client, fed):  # noqa: F811
    from y_server.federation.tables import FederationAttention, FederationMembership, ensure_tables
    from y_server.modals import Rounds

    g = ForumHumanGateway(client_call(client), RateLimiter(burst=50, per_second=50))
    uid = _register(g)
    ensure_tables()
    fed._accounts["human:7"], fed._by_user[uid] = uid, "human:7"
    for u, gid in ((1, "g1"), (uid, "human:7")):
        db.session.add(FederationMembership(user_id=u, global_agent_id=gid, state="active", since_tick=0, reason="initial"))
    db.session.get(User_mgmt, uid).left_on = 0
    db.session.get(User_mgmt, 1).left_on = 0
    db.session.add(Rounds(day=0, hour=1))
    db.session.commit()
    assert [s.global_agent_id for s in fed.affective_report(0)] == ["g1"]
    assert fed.churned_agents(0) == ["g1"]
    fed.apply_attention(0, {"g1": 2.0, "human:7": 3.0})
    assert [(r.user_id, r.multiplier) for r in db.session.scalars(select(FederationAttention)).all()] == [(1, 2.0)]


# -- offline ------------------------------------------------------------------------------------------------
def test_offline_applies_the_request_under_a_lock_and_refuses_while_the_server_runs(client, tmp_path):  # noqa: F811
    with app.app_context():
        factory = lambda folder: client_call(client)  # noqa: E731
        s, out = handle_offline(tmp_path, "register", {"key": "u1", "account": ANN}, factory, running=lambda: False)
        assert s == 200 and out["created"] is True and (tmp_path / "human_gateway.lock").exists()
        s, out = handle_offline(tmp_path, "act", dict(WHO, actions=[{"action": "post", "text": "while stopped"}]), factory, running=lambda: False)
        assert s == 200 and out["ok"]
        assert db.session.scalars(select(Post).where(Post.tweet == "while stopped")).one().comment_to == -1
        assert handle_offline(tmp_path, "act", dict(WHO, actions=[{"action": "post", "text": "no"}]), factory, running=lambda: True)[0] == 409
        assert handle_offline(tmp_path, "state", WHO, factory, running=lambda: True)[0] == 409
        assert handle_offline(tmp_path, "reset", {}, factory)[0] == 409 and handle_offline(tmp_path, "act", [], factory)[0] == 400
        assert db.session.scalars(select(Post).where(Post.tweet == "no")).first() is None


def test_an_offline_crosspost_leaves_at_the_first_barrier_after_the_restart(client, fed, tmp_path):  # noqa: F811
    uid = _register(ForumHumanGateway(client_call(client), RateLimiter(burst=50, per_second=50)))
    fed._accounts["human:7"], fed._by_user[uid] = uid, "human:7"
    fed.set_destinations({"human:7": [{"member": "micro", "local_account_id": "501"}]})
    fed.export_outbox(0)  # the watermark exists
    s, out = handle_offline(tmp_path, "act", dict(WHO, actions=[{"action": "post", "text": "posted offline", "crosspost_to": [{"member": "micro", "text": "hi micro"}]}]),
                            lambda folder: client_call(client), running=lambda: False)
    assert s == 200 and out["ok"]
    sent = fed.export_outbox(5)  # the restarted server reaches its first barrier
    assert [(e.target_member, e.payload["text_excerpt"], e.payload["global_agent_id"]) for e in sent] == [("micro", "hi micro", "human:7")]
