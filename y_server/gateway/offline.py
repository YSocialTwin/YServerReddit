"""Offline participation: a person plays while the forum server is not running.

The request is applied to the database of the experiment by the very routes of the server, run in process through the Flask
test client, under an exclusive file lock so that two requests never write at once. When the server is running the request
is refused with 409: the person goes through the online gateway instead. A post with `crosspost_to` is recorded exactly as
online (table `federation_crosspost`) and leaves at the first barrier after the server starts again."""

from __future__ import annotations

import fcntl
import json
import socket
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

from .core import ForumHumanGateway

LOCK_FILE = "human_gateway.lock"
OFFLINE_OPS = ("register", "state", "act")


def server_running(host: str, port: int, timeout: float = 0.5) -> bool:
    """True when something accepts connections on the address of the forum server."""
    try:
        with socket.create_connection((host if host not in ("0.0.0.0", "") else "127.0.0.1", int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def client_call(client) -> Callable[[str, str, Optional[dict]], Tuple[int, Any]]:
    """`call` of the gateway over a Flask test client of the server (no network)."""

    def call(method: str, path: str, payload: Optional[dict]):
        res = client.open(path, method=method, data=None if payload is None else json.dumps(payload), content_type="application/json")
        try:
            body = json.loads(res.get_data(as_text=True) or "{}")
        except ValueError:
            body = {}
        return res.status_code, body

    return call


def handle_offline(folder, op: str, body: Dict[str, Any], call_factory: Callable[[Path], Callable],
                   running: Optional[Callable[[], bool]] = None) -> Tuple[int, Dict[str, Any]]:
    """Apply one request; `call_factory(folder)` gives the `call` bound to the database of the experiment."""
    folder = Path(folder)
    if op not in OFFLINE_OPS:
        return 409, {"ok": False, "error": "not available while the server is stopped"}
    if not isinstance(body, dict):
        return 400, {"ok": False, "error": "invalid request"}
    if running is not None and running():
        return 409, {"ok": False, "error": "the server is running"}
    with open(folder / LOCK_FILE, "a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            gateway = ForumHumanGateway(call_factory(folder))
            if op == "register":
                return gateway.register(body.get("key"), body.get("account"))
            if op == "state":
                return gateway.state(body)
            return gateway.act(body, body.get("actions"))
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
