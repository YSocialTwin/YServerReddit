import json
from types import SimpleNamespace

from y_server import app
from y_server.modals import SysMessage
from y_server.routes.content_management import get_active_system_messages


class _FakeSysMessageQuery:
    def __init__(self, messages):
        self._messages = list(messages)
        self._filtered = list(messages)

    def filter_by(self, **kwargs):
        to_uid = kwargs.get("to_uid")
        self._filtered = [msg for msg in self._messages if msg.to_uid == to_uid]
        return self

    def all(self):
        return list(self._filtered)


def test_get_active_system_messages_filters_user_and_round(monkeypatch):
    messages = [
        SimpleNamespace(
            id=1,
            type="moderation",
            to_uid=9,
            message="Prefix the reply with MOD NOTICE.",
            from_round=4,
            to_round=8,
        ),
        SimpleNamespace(
            id=2,
            type="moderation",
            to_uid=9,
            message="Too early.",
            from_round=10,
            to_round=12,
        ),
        SimpleNamespace(
            id=3,
            type="moderation",
            to_uid=10,
            message="Other user.",
            from_round=4,
            to_round=8,
        ),
    ]

    with app.app_context():
        monkeypatch.setattr(SysMessage, "query", _FakeSysMessageQuery(messages))
        with app.test_request_context(
            "/get_active_system_messages",
            method="POST",
            data=json.dumps({"user_id": 9, "tid": 6}),
        ):
            payload = json.loads(get_active_system_messages())

    assert payload == [
        {
            "id": 1,
            "type": "moderation",
            "message": "Prefix the reply with MOD NOTICE.",
            "to_uid": 9,
            "from_round": 4,
            "to_round": 8,
        }
    ]


def test_get_active_system_messages_returns_empty_list_when_none_match(monkeypatch):
    with app.app_context():
        monkeypatch.setattr(SysMessage, "query", _FakeSysMessageQuery([]))
        with app.test_request_context(
            "/get_active_system_messages",
            method="POST",
            data=json.dumps({"user_id": 9, "tid": 6}),
        ):
            payload = json.loads(get_active_system_messages())

    assert payload == []
