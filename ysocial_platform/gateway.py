"""The human gateway of the forum Standard runtime, as YSocial's World Simulation talks to it.

The gateway is a process of its own (``python -m y_server.gateway``) that YSocial starts next to the server; YSocial writes
``human_gateway.token`` and ``human_gateway.json`` (host, port, pid) in the experiment folder. When the server is stopped
the person acts through the runtime script (``run_offline_gateway.py``).
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import List, Optional

import requests
from y_web.sdk import HumanGatewayTraits


def _email(key) -> str:
    return f"{str(key).replace(':', '_')}@human.invalid"[:50]


def ensure_body(key, username, human_id, language):
    return {
        "key": key,
        "account": {
            "username": username[:15],
            "email": _email(key),
            "language": language or "en",
            "human_id": human_id,
        },
    }


def actions_request(account_id, username, key, actions):
    return "/act", {
        "username": str(username)[:15],
        "email": _email(key),
        "actions": actions,
    }


def offline_ensure(paths, body):
    return [str(paths.server_config), str(paths.database)], {
        "op": "register",
        "body": body,
    }


def offline_actions(paths, path, body):
    return [str(paths.server_config), str(paths.database)], {
        "op": "act",
        "body": body,
    }


def start_process(launch):
    """The gateway process: ``(process, state)`` (the state is what YSocial writes to ``human_gateway.json``)."""
    proc = subprocess.Popen(
        [
            launch.python,
            "-m",
            "y_server.gateway",
            "--server",
            launch.server_url,
            "--token-file",
            str(launch.token_file),
            "--port",
            str(launch.port),
        ],
        cwd=launch.repo_dir,
        stdin=subprocess.DEVNULL,
        **launch.popen_kwargs,
    )
    return proc, {"host": "127.0.0.1", "port": launch.port, "pid": proc.pid}


def post(text: str, targets: list, community: Optional[str] = None) -> dict:
    action = {"action": "post", "text": text, "crosspost_to": targets}
    if community:
        action["community"] = (
            community  # a topic name as the forum server lists it (GET /get_topics)
        )
    return action


def build_action(text, targets, post_id=None, community=None):
    return post(text, targets, community)


def community_slug(value) -> str:
    """The slug the forum composer uses for a community (same rule as the forum pages)."""
    raw = str(value or "").strip().lower().replace("\\", "/")
    if not raw:
        return ""
    match = re.search(r"(?:^|/)r/([a-z0-9_]+)", raw)
    if match:
        return match.group(1)
    raw = re.sub(r"[^a-z0-9_/-]+", "-", raw.removeprefix("r/").strip("/")).strip("-_/")
    return raw.split("/")[0] if raw else ""


def topics(exp, session=None, server_config=None) -> List[dict]:
    """``[{id, name}]`` the forum server lists at ``GET /get_topics`` (read-only, no token); ``[]`` when it cannot be reached
    (a stopped server: the thread then goes to the default board)."""
    try:
        from y_web.sdk.federation import human_outbox as _ho

        cfg = json.loads(
            Path(server_config or _ho.service.server_config_path(exp)).read_text(
                encoding="utf-8"
            )
        )
        host, port = cfg.get("host") or "127.0.0.1", int(cfg["port"])
        resp = (session or requests).get(f"http://{host}:{port}/get_topics", timeout=5)
        data = resp.json()
        return (
            [t for t in data if isinstance(t, dict) and t.get("name")]
            if resp.status_code == 200 and isinstance(data, list)
            else []
        )
    except Exception:  # noqa: BLE001 - no community known: the default one
        return []


def community_of(exp, slug, session=None) -> Optional[str]:
    """The forum topic name behind the community the person chose in the composer (its slug), or ``None`` (default board:
    a community that is not a topic, an empty choice, or a server that cannot be reached).
    """
    wanted = community_slug(slug)
    if not wanted:
        return None
    for topic in topics(exp, session):
        if community_slug(topic["name"]) == wanted:
            return str(topic["name"])
    return None


HUMAN_GATEWAY = HumanGatewayTraits(
    repo="YServerReddit",
    token_header="X-YGateway-Token",
    ensure_path="/register",
    actions_path="/act",
    ensure_body=ensure_body,
    actions_request=actions_request,
    offline_ensure=offline_ensure,
    offline_actions=offline_actions,
    start_process=start_process,
    build_action=build_action,
    community_of=community_of,
)
