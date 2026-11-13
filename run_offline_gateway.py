"""Offline entry of a person on the forum: one JSON request on stdin, one JSON answer on stdout.

Usage: python run_offline_gateway.py <exp_config.json> <database_server.db>   (stdin: {"op": ..., "body": {...}})

op is register ({"key", "account": {username, email, ...}}), state ({"username", "email"}) or act ({"username", "email",
"actions": [...]}); the same operations and actions as the online gateway (`python -m y_server.gateway`). Works only while
the forum server is stopped (answer 409 otherwise)."""

import json
import os
import sys
from pathlib import Path


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 2:
        print(__doc__)
        return 2
    config_file, database = Path(argv[0]).resolve(), Path(argv[1]).resolve()
    try:
        request = json.loads(sys.stdin.read() or "{}")
        config = json.loads(config_file.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        print(json.dumps({"ok": False, "status": 400, "error": "invalid request or configuration"}))
        return 2
    os.environ["YSERVER_CONFIG"] = str(config_file)  # y_server reads its configuration when it is imported
    from y_server.gateway.offline import handle_offline, server_running, client_call

    def call_factory(folder):
        from y_server import app

        client = app.test_client()
        client.post("/change_db", data=json.dumps({"path": str(database)}), content_type="application/json")
        section = config.get("federation") or {}
        if section.get("enabled"):  # a post with crosspost_to is recorded for the first barrier after the restart
            from y_server.federation.adapter import YServerRedditAdapter

            adapter = YServerRedditAdapter(section["member"], int(section.get("federation_id", 1)), accounts=section.get("accounts"),
                                           crosspost_mode=section.get("crosspost_mode", "coordinator_rule"))
            with app.app_context():
                adapter._watermark()  # posts made now must not fall under the watermark of a run that has not started
            app.config["federation_adapter"] = adapter
        return client_call(client)

    status, body = handle_offline(
        database.parent, str(request.get("op", "")), request.get("body") or {}, call_factory,
        running=lambda: server_running(str(config.get("host", "127.0.0.1")), int(config.get("port", 0))),
    )
    print(json.dumps({**body, "status": status}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
