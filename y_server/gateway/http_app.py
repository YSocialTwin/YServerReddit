"""HTTP transport of the gateway: a small Flask app on the local interface, one token for every request.

Endpoints (JSON in, JSON out, header `X-YGateway-Token`): GET /health, POST /register {"key", "account": {...}},
POST /state {"username", "email"}, POST /act {"username", "email", "actions": [...]}."""

from __future__ import annotations

import hmac
import json
from typing import Any, Optional, Tuple

from flask import Flask, jsonify, request

from .core import ForumHumanGateway

TOKEN_HEADER = "X-YGateway-Token"


def server_call(base_url: str, timeout: float = 10.0):
    """`call` of the gateway over HTTP to the running forum server."""
    import requests

    base = base_url.rstrip("/")

    def call(method: str, path: str, payload: Optional[dict]) -> Tuple[int, Any]:
        try:
            res = requests.request(method, base + path, data=None if payload is None else json.dumps(payload), timeout=timeout)
        except requests.RequestException:
            return 503, {}
        try:
            body = json.loads(res.text or "{}")
        except ValueError:
            body = {}
        return res.status_code, body

    return call


def create_gateway_app(gateway: ForumHumanGateway, token: str) -> Flask:
    if not token:
        raise ValueError("a token is required")
    app = Flask("y_server_forum_gateway")

    @app.before_request
    def _auth():
        given = request.headers.get(TOKEN_HEADER, "")
        if not hmac.compare_digest(given.encode(), token.encode()):
            return jsonify({"ok": False, "error": "unauthorized"}), 401
        return None

    def body() -> dict:
        data = request.get_json(silent=True)
        return data if isinstance(data, dict) else {}

    @app.get("/health")
    def health():
        return jsonify({"ok": True})

    @app.post("/register")
    def register():
        data = body()
        status, out = gateway.register(data.get("key"), data.get("account"))
        return jsonify(out), status

    @app.post("/state")
    def state():
        status, out = gateway.state(body())
        return jsonify(out), status

    @app.post("/act")
    def act():
        data = body()
        status, out = gateway.act(data, data.get("actions"))
        return jsonify(out), status

    return app
