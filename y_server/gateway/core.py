"""Gateway of the human participants of the forum: the logic, independent of the transport.

A person acts on the forum only through this gateway. Every action is applied by the routes of the forum server itself
(`/register`, `/post`, `/comment`, `/reaction`, `/follow`, `/report`), so a person goes through the same code as an agent;
the gateway only checks who is asking, drops the fields a person must not set, limits the text and the rate, and adds
the round. `call(method, path, payload)` returns `(http status, decoded body)`: over HTTP when the server is running
(see `http_app.py`), in process when it is stopped (see `offline.py`)."""

from __future__ import annotations

import hashlib
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

Call = Callable[[str, str, Optional[dict]], Tuple[int, Any]]

ALLOWED_ACTIONS = frozenset({"post", "comment", "react", "follow", "unfollow", "report"})
# The fields a person may set on each action; the rest (topics, emotions, agent-only fields, the round...) is dropped.
ACTION_FIELDS = {
    "post": {"text", "crosspost_to"},
    "comment": {"post_id", "text"},
    "react": {"post_id", "type"},
    "follow": {"target"},
    "unfollow": {"target"},
    "report": {"post_id", "type"},
}
REACTIONS = ("like", "dislike")
REPORTS = ("offensive", "toxic")
MAX_TEXT = 4000
MAX_ACTIONS_PER_REQUEST = 20
IDENTITY_FIELDS = ("username", "email")


class RateLimiter:
    """Token bucket per key: `burst` actions at once, refilled at `per_second`."""

    def __init__(self, burst: float = 10, per_second: float = 2, clock=time.monotonic):
        self.burst, self.rate, self.clock = burst, per_second, clock
        self._state: Dict[str, Tuple[float, float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str, cost: float = 1) -> bool:
        with self._lock:
            now = self.clock()
            tokens, last = self._state.get(key, (self.burst, now))
            tokens = min(self.burst, tokens + (now - last) * self.rate)
            if tokens < cost:
                self._state[key] = (tokens, now)
                return False
            self._state[key] = (tokens - cost, now)
            return True


def _fail(status: int, error: str) -> Tuple[int, dict]:
    return status, {"ok": False, "error": error}


class ForumHumanGateway:
    def __init__(self, call: Call, limiter: Optional[RateLimiter] = None):
        self.call = call
        self.limiter = limiter or RateLimiter()

    # -- identity -----------------------------------------------------------------------------
    def _lookup(self, who: Any) -> Tuple[Optional[dict], Optional[Tuple[int, dict]]]:
        """The account of a person: `{"username", "email"}` must name an existing account with user_type "human"."""
        if not isinstance(who, dict) or any(not isinstance(who.get(k), str) or not who.get(k) for k in IDENTITY_FIELDS):
            return None, _fail(400, "invalid request")
        status, body = self.call("POST", "/get_user", {"username": who["username"], "email": who["email"]})
        if status == 404 or not isinstance(body, dict) or "id" not in body:
            return None, _fail(404, "unknown account")
        if body.get("user_type") != "human":
            return None, _fail(403, "not an account of a person")
        return body, None

    def _tid(self) -> Optional[int]:
        status, body = self.call("GET", "/current_time", None)
        return int(body["id"]) if status == 200 and isinstance(body, dict) and "id" in body else None

    # -- operations ---------------------------------------------------------------------------
    def register(self, key: Any, account: Any) -> Tuple[int, dict]:
        """Create the account of a person on the forum (idempotent: the same username and email give the same account)."""
        if not isinstance(key, str) or not key or not isinstance(account, dict):
            return _fail(400, "invalid request")
        if any(not isinstance(account.get(k), str) or not account.get(k) for k in IDENTITY_FIELDS):
            return _fail(400, "invalid request")
        username, email = account["username"], account["email"]
        if len(username) > 15 or len(email) > 50:
            return _fail(400, "username or email too long")
        status, existing = self.call("POST", "/get_user", {"username": username, "email": email})
        if status == 200 and isinstance(existing, dict) and "id" in existing:
            if existing.get("user_type") != "human":
                return _fail(409, "the username belongs to an agent")
            return 200, {"ok": True, "account_id": int(existing["id"]), "created": False}
        tid = self._tid()
        if tid is None:
            return _fail(503, "the server is not available")
        payload = {
            "name": username, "email": email,
            "password": hashlib.sha256(f"{key}:{username}".encode()).hexdigest()[:40],  # never used to log in: YWeb does
            "leaning": str(account.get("leaning") or "neutral")[:10], "age": int(account.get("age") or 0),
            "user_type": "human", "oe": "", "co": "", "ex": "", "ag": "", "ne": "",
            "language": str(account.get("language") or "en")[:10],
            "education_level": str(account.get("education_level") or "")[:10], "joined_on": tid, "round_actions": 0,
            "owner": "human", "gender": str(account.get("gender") or "")[:10],
            "nationality": str(account.get("nationality") or "")[:15], "toxicity": "no",
            "profession": str(account.get("profession") or "")[:50], "daily_activity_level": 1,
        }
        status, body = self.call("POST", "/register", payload)
        if status != 200 or not isinstance(body, dict) or body.get("status") != 200:
            return _fail(502, "the server refused the registration")
        status, found = self.call("POST", "/get_user", {"username": username, "email": email})
        if status != 200 or not isinstance(found, dict) or "id" not in found:
            return _fail(502, "the account was not created")
        return 200, {"ok": True, "account_id": int(found["id"]), "created": True}

    def state(self, who: Any) -> Tuple[int, dict]:
        user, err = self._lookup(who)
        if err:
            return err
        status, body = self.call("GET", "/current_time", None)
        if status != 200:
            return _fail(503, "the server is not available")
        return 200, {"ok": True, "account_id": int(user["id"]), "username": user["username"], "round": body}

    def act(self, who: Any, actions: Any) -> Tuple[int, dict]:
        user, err = self._lookup(who)
        if err:
            return err
        if not isinstance(actions, list) or not actions or len(actions) > MAX_ACTIONS_PER_REQUEST:
            return _fail(400, f"expected 1 to {MAX_ACTIONS_PER_REQUEST} actions")
        uid = int(user["id"])
        if not self.limiter.allow(str(uid), len(actions)):
            return _fail(429, "too many actions")
        tid = self._tid()
        if tid is None:
            return _fail(503, "the server is not available")
        results: List[dict] = [self._one(uid, tid, act) for act in actions]
        return 200, {"ok": all(r["ok"] for r in results), "results": results}

    # -- one action ---------------------------------------------------------------------------
    def _one(self, uid: int, tid: int, act: Any) -> dict:
        kind = act.get("action") if isinstance(act, dict) else None
        if kind not in ALLOWED_ACTIONS:
            return {"action": kind, "ok": False, "status": 400, "error": "unknown action"}
        fields = {k: v for k, v in act.items() if k in ACTION_FIELDS[kind]}
        try:
            path, payload = self._payload(kind, uid, tid, fields)
        except ValueError as exc:
            return {"action": kind, "ok": False, "status": 400, "error": str(exc)}
        status, body = self.call("POST", path, payload)
        ok = status == 200 and not (isinstance(body, dict) and int(body.get("status", 200)) >= 400)
        out = {"action": kind, "ok": ok, "status": status}
        if isinstance(body, dict):
            out.update({k: body[k] for k in ("comment_id", "post_id", "deduped", "error") if k in body})
        return out

    @staticmethod
    def _text(fields: dict) -> str:
        text = fields.get("text")
        if not isinstance(text, str) or not text.strip():
            raise ValueError("text is required")
        if len(text) > MAX_TEXT:
            raise ValueError(f"text longer than {MAX_TEXT} characters")
        return text.strip()

    @staticmethod
    def _int(fields: dict, key: str) -> int:
        v = fields.get(key)
        if isinstance(v, bool) or not isinstance(v, int):
            raise ValueError(f"{key} must be an integer")
        return v

    def _payload(self, kind: str, uid: int, tid: int, fields: dict) -> Tuple[str, dict]:
        if kind == "post":
            text = self._text(fields)
            payload = {"user_id": uid, "tweet": text, "emotions": [], "hashtags": [], "mentions": [], "topics": [], "tid": tid}
            targets = self._targets(fields.get("crosspost_to"), text)
            if targets:  # the person chose where to post it too: same fields as the payload of an agent
                payload.update(crosspost=True, crosspost_to=targets)
            return "/post", payload
        if kind == "comment":
            return "/comment", {"user_id": uid, "post_id": self._int(fields, "post_id"), "text": self._text(fields),
                                "emotions": [], "hashtags": [], "mentions": [], "tid": tid}
        if kind == "react":
            if fields.get("type") not in REACTIONS:
                raise ValueError("type must be like or dislike")
            return "/reaction", {"user_id": uid, "post_id": self._int(fields, "post_id"), "type": fields["type"], "tid": tid}
        if kind in ("follow", "unfollow"):
            return "/follow", {"user_id": uid, "target": self._int(fields, "target"), "action": kind, "tid": tid}
        if fields.get("type") not in REPORTS:
            raise ValueError("type must be offensive or toxic")
        return "/report", {"user_id": uid, "post_id": self._int(fields, "post_id"), "type": fields["type"], "tid": tid}

    @staticmethod
    def _targets(raw: Any, text: str) -> List[dict]:
        """[{"member", "text"}] of the cross-posts the person chose; a missing text is the text of the post."""
        if raw in (None, []):
            return []
        if not isinstance(raw, list):
            raise ValueError("crosspost_to must be a list")
        out, seen = [], set()
        for item in raw:
            member = item.get("member") if isinstance(item, dict) else None
            if not isinstance(member, str) or not member:
                raise ValueError("crosspost_to needs a member")
            if member in seen:
                continue
            seen.add(member)
            body = item.get("text")
            out.append({"member": member, "text": (body if isinstance(body, str) and body.strip() else text).strip()[:4000]})
        return out
