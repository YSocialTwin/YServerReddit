import json
import threading
import time as pytime
from flask import request
from y_server import app, db
from sqlalchemy import desc
from y_server.modals import (
    Rounds,
    SimulationClient,
)

_SYNC_LOCK = threading.RLock()


def _ensure_sync_schema():
    with app.app_context():
        SimulationClient.__table__.create(bind=db.engine, checkfirst=True)


def _sync_timeout_seconds():
    try:
        configured = float(app.config.get("sync_timeout_seconds", 300))
        if app.config.get("TESTING"):
            return max(0.0, configured)
        return max(5.0, configured)
    except Exception:
        return 300.0


def _compute_next_time(day, hour, slots_per_day=24):
    if int(hour) < int(slots_per_day) - 1:
        return int(day), int(hour) + 1
    return int(day) + 1, 0


def _get_current_round_locked():
    cround = Rounds.query.order_by(desc(Rounds.id)).first()
    if cround is None:
        cround = Rounds(day=0, hour=0)
        db.session.add(cround)
        db.session.commit()
        cround = Rounds.query.order_by(desc(Rounds.id)).first()
    return cround


def _get_or_create_round_locked(day, hour):
    cround = Rounds.query.filter_by(day=int(day), hour=int(hour)).first()
    if cround is None:
        cround = Rounds(day=int(day), hour=int(hour))
        db.session.add(cround)
        db.session.commit()
        cround = Rounds.query.filter_by(day=int(day), hour=int(hour)).first()
    return cround


def _cleanup_stale_clients_locked(now_ts=None):
    timeout_s = _sync_timeout_seconds()
    now_ts = float(now_ts if now_ts is not None else pytime.time())
    stale_clients = (
        SimulationClient.query.filter_by(status="active")
        .filter(SimulationClient.last_heartbeat < (now_ts - timeout_s))
        .all()
    )
    for client in stale_clients:
        client.status = "stale"
        client.submitted_round_id = None
        client.updated_at = now_ts
    if stale_clients:
        db.session.commit()
    return stale_clients


def _active_clients_locked():
    return list(SimulationClient.query.filter_by(status="active").all())


def _try_advance_round_locked():
    current_round = _get_current_round_locked()
    _cleanup_stale_clients_locked()
    active_clients = _active_clients_locked()
    if not active_clients:
        return {
            "advanced": False,
            "round": current_round,
            "active_clients": 0,
            "submitted_clients": 0,
        }

    submitted_clients = [
        client for client in active_clients if int(client.submitted_round_id or -1) == int(current_round.id)
    ]
    if len(submitted_clients) < len(active_clients):
        return {
            "advanced": False,
            "round": current_round,
            "active_clients": len(active_clients),
            "submitted_clients": len(submitted_clients),
        }

    next_day, next_hour = _compute_next_time(current_round.day, current_round.hour)
    next_round = _get_or_create_round_locked(next_day, next_hour)
    now_ts = pytime.time()
    for client in active_clients:
        client.submitted_round_id = None
        client.updated_at = now_ts
    db.session.commit()
    return {
        "advanced": True,
        "round": next_round,
        "active_clients": len(active_clients),
        "submitted_clients": len(active_clients),
    }


def _round_payload(cround, **extra):
    payload = {"id": cround.id, "day": cround.day, "round": cround.hour}
    payload.update(extra)
    return payload


@app.route("/current_time", methods=["GET"])
def current_time():
    """
    Get the current time of the simulation.

    :return: a json object with the current time
    """
    _ensure_sync_schema()
    with _SYNC_LOCK:
        cround = _get_current_round_locked()

    return json.dumps({"id": cround.id, "day": cround.day, "round": cround.hour})


@app.route("/update_time", methods=["POST"])
def update_time():
    """
    Update the time of the simulation.

    :return: a json object with the updated time
    """
    data = json.loads(request.get_data())
    day = int(data["day"])
    hour = int(data["round"])
    force = bool(data.get("force"))

    _ensure_sync_schema()
    with _SYNC_LOCK:
        _cleanup_stale_clients_locked()
        active_clients = _active_clients_locked()
        if active_clients and not force:
            current_round = _get_current_round_locked()
            return json.dumps(
                _round_payload(
                    current_round,
                    status=409,
                    error="sync_barrier_active",
                    active_clients=len(active_clients),
                )
            ), 409

        cround = _get_or_create_round_locked(day, hour)

    return json.dumps({"id": cround.id, "day": cround.day, "round": cround.hour})


@app.route("/register_client", methods=["POST"])
def register_client():
    data = json.loads(request.get_data() or "{}")
    client_id = str(data.get("client_id") or "").strip()
    if not client_id:
        return json.dumps({"status": 400, "error": "client_id_required"}), 400

    _ensure_sync_schema()
    with _SYNC_LOCK:
        current_round = _get_current_round_locked()
        now_ts = pytime.time()
        client = SimulationClient.query.filter_by(client_id=client_id).first()
        if client is None:
            client = SimulationClient(
                client_id=client_id,
                status="active",
                last_heartbeat=now_ts,
                created_at=now_ts,
                updated_at=now_ts,
            )
            db.session.add(client)
        else:
            client.status = "active"
            client.last_heartbeat = now_ts
            client.submitted_round_id = None
            client.updated_at = now_ts
        db.session.commit()
        active_clients = len(_active_clients_locked())

    return json.dumps(_round_payload(current_round, status=200, client_id=client_id, active_clients=active_clients))


@app.route("/heartbeat", methods=["POST"])
def heartbeat():
    data = json.loads(request.get_data() or "{}")
    client_id = str(data.get("client_id") or "").strip()
    if not client_id:
        return json.dumps({"status": 400, "error": "client_id_required"}), 400

    _ensure_sync_schema()
    with _SYNC_LOCK:
        client = SimulationClient.query.filter_by(client_id=client_id).first()
        if client is None:
            return json.dumps({"status": 404, "error": "client_not_registered"}), 404
        client.last_heartbeat = pytime.time()
        if client.status == "stale":
            client.status = "active"
        client.updated_at = client.last_heartbeat
        db.session.commit()
        current_round = _get_current_round_locked()

    return json.dumps(_round_payload(current_round, status=200, client_id=client_id))


@app.route("/submit_round", methods=["POST"])
def submit_round():
    data = json.loads(request.get_data() or "{}")
    client_id = str(data.get("client_id") or "").strip()
    round_id = data.get("round_id")
    if not client_id:
        return json.dumps({"status": 400, "error": "client_id_required"}), 400
    try:
        round_id = int(round_id)
    except Exception:
        return json.dumps({"status": 400, "error": "round_id_required"}), 400

    _ensure_sync_schema()
    with _SYNC_LOCK:
        _cleanup_stale_clients_locked()
        current_round = _get_current_round_locked()
        client = SimulationClient.query.filter_by(client_id=client_id).first()
        if client is None:
            return json.dumps(_round_payload(current_round, status=404, error="client_not_registered")), 404
        if client.status != "active":
            return json.dumps(_round_payload(current_round, status=409, error="client_not_active")), 409

        client.last_heartbeat = pytime.time()
        client.updated_at = client.last_heartbeat

        if int(current_round.id) != round_id:
            db.session.commit()
            return json.dumps(
                _round_payload(
                    current_round,
                    status=409,
                    error="round_mismatch",
                    submitted_round_id=round_id,
                )
            ), 409

        client.submitted_round_id = round_id
        db.session.commit()
        result = _try_advance_round_locked()

    return json.dumps(
        _round_payload(
            result["round"],
            status=200,
            advanced=bool(result["advanced"]),
            active_clients=int(result["active_clients"]),
            submitted_clients=int(result["submitted_clients"]),
        )
    )


@app.route("/complete_client", methods=["POST"])
def complete_client():
    data = json.loads(request.get_data() or "{}")
    client_id = str(data.get("client_id") or "").strip()
    if not client_id:
        return json.dumps({"status": 400, "error": "client_id_required"}), 400

    _ensure_sync_schema()
    with _SYNC_LOCK:
        current_round = _get_current_round_locked()
        client = SimulationClient.query.filter_by(client_id=client_id).first()
        if client is None:
            return json.dumps(_round_payload(current_round, status=404, error="client_not_registered")), 404
        now_ts = pytime.time()
        client.status = "completed"
        client.submitted_round_id = None
        client.last_heartbeat = now_ts
        client.updated_at = now_ts
        db.session.commit()
        result = _try_advance_round_locked()
        active_clients = len(_active_clients_locked())

    return json.dumps(
        _round_payload(
            result["round"],
            status=200,
            advanced=bool(result["advanced"]),
            active_clients=active_clients,
        )
    )


@app.route("/deregister_client", methods=["POST"])
def deregister_client():
    data = json.loads(request.get_data() or "{}")
    client_id = str(data.get("client_id") or "").strip()
    if not client_id:
        return json.dumps({"status": 400, "error": "client_id_required"}), 400

    _ensure_sync_schema()
    with _SYNC_LOCK:
        current_round = _get_current_round_locked()
        client = SimulationClient.query.filter_by(client_id=client_id).first()
        if client is not None:
            db.session.delete(client)
            db.session.commit()
        result = _try_advance_round_locked()

    return json.dumps(_round_payload(result["round"], status=200, advanced=bool(result["advanced"])))
