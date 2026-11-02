"""F6.2: cross-post as an agent action (crosspost.mode = agent_action) on YServerReddit."""

import json

import pytest

from federation_bus_impl import _post_json, client  # noqa: F401  (fixture)
from y_server import app, db
from y_server.federation.adapter import YServerRedditAdapter

pytest.importorskip("ysocial_federation")

DEST = {"g1": [{"member": "micro", "local_account_id": "201"}, {"member": "dating", "local_account_id": "301"}],
        "g2": [{"member": "micro", "local_account_id": "202"}]}


@pytest.fixture()
def agent(client):  # noqa: F811
    a = YServerRedditAdapter("forum", 1, accounts={"g1": 1, "g2": 2, "g3": 3}, crosspost_mode="agent_action")
    app.config["federation_adapter"] = a
    a.set_destinations(DEST)
    with app.app_context():
        yield a
    app.config.pop("federation_adapter", None)


def _post(client, uid, text, to=None, crosspost=True):  # noqa: F811
    body = {"user_id": uid, "tweet": text, "emotions": [], "hashtags": [], "mentions": [], "topics": [], "tid": 1}
    if crosspost:
        body["crosspost"] = True
    if to is not None:
        body["crosspost_to"] = to
    assert _post_json(client, "/post", body).status_code == 200


def test_the_agent_sends_a_crosspost_with_its_own_text_to_each_chosen_destination(client, agent):  # noqa: F811
    agent.export_outbox(0)
    _post(client, 1, "original", to=[{"member": "micro", "text": "Title\nbody"}, {"member": "dating", "text": "hi there"}])
    out = agent.export_outbox(3)
    assert [(e.payload_type, e.target_member, e.payload["target_account_id"], e.payload["text_excerpt"]) for e in out] == [
        ("crosspost.v1", "dating", "301", "hi there"), ("crosspost.v1", "micro", "201", "Title\nbody")]
    assert [e.seq for e in out] == [1, 2]  # seq 0 belongs to the post itself: no content.v1 is sent for it
    assert all(e.source_member == "forum" and e.tick_emitted == 3 for e in out)
    assert out[0].payload["global_agent_id"] == "g1" and out[0].payload["source_ref"].startswith("ysocial://1/forum/post/")
    assert [e.fed_msg_id for e in agent.export_outbox(3)] == [e.fed_msg_id for e in out]  # a retry sends the same messages
    assert agent.export_outbox(4) == []


def test_a_retry_keeps_the_account_even_if_the_destinations_changed(client, agent):  # noqa: F811
    agent.export_outbox(0)
    _post(client, 1, "original", to=[{"member": "micro", "text": "x"}])
    first = agent.export_outbox(3)
    agent.set_destinations({})
    assert [e.to_dict() for e in agent.export_outbox(3)] == [e.to_dict() for e in first]


def test_a_destination_without_an_active_account_is_dropped(client, agent):  # noqa: F811
    agent.export_outbox(0)
    _post(client, 2, "two", to=[{"member": "micro", "text": "ok"}, {"member": "dating", "text": "no account"}])
    out = agent.export_outbox(3)
    assert [(e.target_member, e.payload["target_account_id"]) for e in out] == [("micro", "202")]


def test_a_post_without_targets_in_agent_action_mode_sends_nothing_but_content_policy_all_still_does(client, agent):  # noqa: F811
    agent.export_outbox(0)
    _post(client, 1, "marked but nowhere", to=[])
    assert agent.export_outbox(3) != [] and agent.export_outbox(3)[0].payload_type == "content.v1"


def test_exposures_after_crossposts_never_reuse_a_sequence_number(client):  # noqa: F811
    a = YServerRedditAdapter("forum", 1, accounts={"g1": 1, "g2": 2, "g3": 3}, crosspost_mode="agent_action", export_policy="all", emit_exposures=True)
    app.config["federation_adapter"] = a
    a.set_destinations(DEST)
    try:
        with app.app_context():
            a.export_outbox(0)
            _post(client, 1, "with xp", to=[{"member": "micro", "text": "t"}])
            _post(client, 2, "plain", crosspost=False)
            out = a.export_outbox(3)
            assert len({e.seq for e in out}) == len(out)
            assert sorted((e.payload_type, e.target_member) for e in out) == [("content.v1", "coordinator"), ("crosspost.v1", "micro")]
    finally:
        app.config.pop("federation_adapter", None)


def test_targets_are_ignored_in_coordinator_rule_mode(client):  # noqa: F811
    a = YServerRedditAdapter("forum", 1, accounts={"g1": 1})
    app.config["federation_adapter"] = a
    try:
        with app.app_context():
            a.export_outbox(0)
            _post(client, 1, "x", to=[{"member": "micro", "text": "t"}])
            out = a.export_outbox(3)
            assert [(e.payload_type, e.target_member) for e in out] == [("content.v1", "coordinator")]
    finally:
        app.config.pop("federation_adapter", None)


def test_the_destinations_route(client, agent):  # noqa: F811
    assert json.loads(client.get("/federation/destinations?user_id=1").data)["destinations"] == ["dating", "micro"]
    # P15: platform and name of each member, additive
    assert [m["member"] for m in json.loads(client.get("/federation/destinations?user_id=1").data)["members"]] == ["dating", "micro"]
    assert json.loads(client.get("/federation/destinations?user_id=3").data) == {"destinations": [], "members": []}
    assert client.get("/federation/destinations?user_id=x").status_code == 422


def test_an_unknown_mode_is_refused():
    with pytest.raises(ValueError):
        YServerRedditAdapter("forum", 1, crosspost_mode="nope")


# -- P5: sharing another agent's thread (the forum share, re-enabled for federated agents) ----------------
def _share(client, uid, post_id, text, to=None):  # noqa: F811
    body = {"user_id": uid, "post_id": post_id, "text": text, "emotions": [], "hashtags": [], "mentions": [], "tid": 1}
    if to is not None:
        body.update(crosspost=True, crosspost_to=to)
    return _post_json(client, "/share", body)


def _last_post_id():
    from y_server.modals import Post

    return db.session.scalars(db.select(Post).order_by(Post.id.desc())).first().id


def test_the_share_route_is_closed_without_federation_and_without_the_option(client):  # noqa: F811
    app.config.pop("federation_adapter", None)
    app.config["share_enabled"] = False
    _post(client, 2, "words", crosspost=False)
    with app.app_context():
        post_id = _last_post_id()
    assert _share(client, 1, post_id, "nice").status_code == 403


def test_the_option_opens_the_share_route_without_federation(client):  # noqa: F811
    app.config.pop("federation_adapter", None)
    app.config["share_enabled"] = True
    try:
        _post(client, 2, "words", crosspost=False)
        with app.app_context():
            post_id = _last_post_id()
        assert _share(client, 1, post_id, "nice").status_code == 200
    finally:
        app.config["share_enabled"] = False


def test_a_share_with_targets_leaves_as_a_crosspost_quoting_the_original_author(client, agent):  # noqa: F811
    agent.export_outbox(0)
    _post(client, 2, "words of the second agent", crosspost=False)
    original = _last_post_id()
    assert _share(client, 1, original, "so true", to=[{"member": "micro", "text": "so true"}]).status_code == 200
    out = agent.export_outbox(3)
    assert [(e.payload_type, e.target_member, e.payload["global_agent_id"], e.payload["text_excerpt"]) for e in out] == [
        ("crosspost.v1", "micro", "g1", "so true")]
    q = out[0].payload["quoted"]
    assert q["global_agent_id"] == "g2" and q["member"] == "forum" and q["text_excerpt"] == "words of the second agent"
    assert q["source_ref"] == f"ysocial://1/forum/post/{original}"
    assert [e.fed_msg_id for e in agent.export_outbox(3)] == [e.fed_msg_id for e in out]


def test_an_unmarked_share_never_leaves_and_an_unmapped_author_is_anonymous(client, agent):  # noqa: F811
    agent.export_outbox(0)
    _post(client, 2, "words", crosspost=False)
    assert _share(client, 1, _last_post_id(), "plain share").status_code == 200
    assert agent.export_outbox(3) == []
    _post(client, 3, "from a non member", crosspost=False)
    agent._by_user.pop(3, None)
    agent._accounts.pop("g3", None)
    assert _share(client, 1, _last_post_id(), "look", to=[{"member": "micro", "text": "look"}]).status_code == 200
    q = agent.export_outbox(4)[0].payload["quoted"]
    assert q["global_agent_id"] is None and q["source_ref"] is None


def test_a_share_carries_the_topics_of_the_thread_it_shares(client, agent):  # noqa: F811
    from y_server.modals import Post_topics

    agent.export_outbox(0)
    _post(client, 2, "about sport", crosspost=False)
    original = _last_post_id()
    db.session.add(Post_topics(post_id=original, topic_id=1))
    db.session.commit()
    assert _share(client, 1, original, "yes", to=[{"member": "micro", "text": "yes"}]).status_code == 200
    assert agent.export_outbox(3)[0].payload["topics"] == ["sport"]


def test_quoting_a_materialised_thread_sends_the_exposure_first(client):  # noqa: F811
    from ysocial_federation.envelope import Envelope

    a = YServerRedditAdapter("forum", 1, accounts={"g1": 1, "g2": 2, "g3": 3}, crosspost_mode="agent_action", emit_exposures=True)
    app.config["federation_adapter"] = a
    a.set_destinations(DEST)
    try:
        with app.app_context():
            a.export_outbox(0)
            env = Envelope.create(1, "micro", "forum", 2, 0, "crosspost.v1",
                                  {"global_agent_id": "g2", "source_ref": "ysocial://1/micro/post/7", "kind": "post", "text_excerpt": "from micro",
                                   "image_ref": None, "topics": [], "lang": "en", "target_account_id": 2})
            a.ingest_inbox(3, [env])
            foreign = _last_post_id()
            assert _share(client, 1, foreign, "agree", to=[{"member": "dating", "text": "agree"}]).status_code == 200
            out = a.export_outbox(4)
            kinds = [(e.payload_type, e.payload.get("kind")) for e in out]
            assert kinds == [("exposure.v1", "interact"), ("crosspost.v1", "post")]
            assert out[0].payload["viewer_global_agent_id"] == "g1" and out[0].payload["source_ref"] == "ysocial://1/micro/post/7"
            assert out[1].payload["quoted"]["source_ref"] == "ysocial://1/micro/post/7"
            assert len({e.seq for e in out}) == 2
            assert [e.fed_msg_id for e in a.export_outbox(4)] == [e.fed_msg_id for e in out]
    finally:
        app.config.pop("federation_adapter", None)
