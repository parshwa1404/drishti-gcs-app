import asyncio
import json
import random
import threading
import time

import paramiko
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from services.ssh_client import (
    STATUS_WAITING,
    RpiSshClient,
    load_rpi_config,
)

router = APIRouter()

# Deolali Cantonment reference point
_DEOLALI_LAT = 19.9175
_DEOLALI_LON = 73.8278


class ConnectRequest(BaseModel):
    host: str
    user: str
    key_path: str


class StartRequest(BaseModel):
    altitude_m: float = 80.0
    session_name: str


# Module-level state shared across requests.
# `lat`/`lon` are read by routers.session (/session/live, Panel 2) — keep them.
_state = {
    "connected": False,
    "running": False,
    "host": None,
    # GPS position mirrored from the live tail (or random-walked in dev so the
    # Live Map keeps animating without a real RPi).
    "lat": _DEOLALI_LAT,
    "lon": _DEOLALI_LON,
    "client": None,          # RpiSshClient once connected to a real RPi
    "creds": None,           # kwargs to rebuild the client after a stop
    "tail_thread": None,
    "config": None,
}

# Latest per-frame record from the live SSH tail, updated by the tail thread.
_live = {
    "frames_captured": 0,
    "lat": None,
    "lon": None,
    "altitude_m": None,
    "heading_deg": None,
    "unix_ms": None,
}
_live_lock = threading.Lock()

# In-process fan-out of per-frame records to other panels (e.g. Panel 6
# preflight) so they consume the same SSH tail without re-parsing the CSV.
_record_subscribers: list = []


def subscribe_records(callback) -> None:
    """Register a callback invoked with each per-frame record from the tail."""
    if callback not in _record_subscribers:
        _record_subscribers.append(callback)


def unsubscribe_records(callback) -> None:
    if callback in _record_subscribers:
        _record_subscribers.remove(callback)


def current_connection_status():
    """SSH connection state from the active client, or None if never connected."""
    client = _state.get("client")
    return client.connection_status if client is not None else None


def _on_record(rec: dict) -> None:
    """Tail-thread callback: fold one timestamps.csv record into shared state."""
    with _live_lock:
        _live["frames_captured"] += 1
        _live["lat"] = rec["lat"]
        _live["lon"] = rec["lon"]
        _live["altitude_m"] = rec["altitude_m"]
        _live["heading_deg"] = rec["heading_deg"]
        _live["unix_ms"] = rec["unix_ms"]
    # Mirror position so Panel 2 (/session/live) tracks the real flight.
    _state["lat"] = rec["lat"]
    _state["lon"] = rec["lon"]
    # Fan out to subscribers (preflight, etc.). One bad subscriber must not
    # break the tail loop.
    for cb in list(_record_subscribers):
        try:
            cb(rec)
        except Exception:  # noqa: BLE001 - isolate subscriber failures
            pass


def _reset_live() -> None:
    with _live_lock:
        _live["frames_captured"] = 0
        for k in ("lat", "lon", "altitude_m", "heading_deg", "unix_ms"):
            _live[k] = None


def _build_client(creds: dict) -> RpiSshClient:
    cfg = _state.get("config") or {}
    return RpiSshClient(
        hostname=creds["host"] or cfg.get("hostname", ""),
        username=creds["user"] or cfg.get("username", "pi"),
        key_path=creds["key_path"] or cfg.get("key_path", ""),
        session_dir=cfg.get("session_dir", "~/drishti_sessions"),
        reconnect_max_backoff_s=cfg.get("reconnect_max_backoff_s", 30),
    )


def _live_payload(snap: dict, connection_status: str) -> dict:
    """Status payload built from the live SSH tail. HDOP/disk/sats are not in
    timestamps.csv, so they are null on the real path (frontend renders '—')."""
    return {
        "frames_captured": snap["frames_captured"],
        "gps_quality": None,
        "heading_deg": snap["heading_deg"],
        "disk_mb_remaining": None,
        "fix_count": None,
        "lat": snap["lat"],
        "lon": snap["lon"],
        "altitude_m": snap["altitude_m"],
        "unix_ms": snap["unix_ms"],
        "timestamp_ms": snap["unix_ms"] or int(time.time() * 1000),
        "connection_status": connection_status,
    }


@router.post("/connect")
async def connect(body: ConnectRequest):
    _state["config"] = load_rpi_config()
    creds = {"host": body.host, "user": body.user, "key_path": body.key_path}
    client = _build_client(creds)
    try:
        await asyncio.to_thread(client.connect)
    except (paramiko.SSHException, OSError) as exc:
        _state["connected"] = False
        _state["client"] = None
        raise HTTPException(
            status_code=502,
            detail=f"SSH connection failed ({type(exc).__name__})",
        )
    _state["client"] = client
    _state["creds"] = creds
    _state["connected"] = True
    _state["host"] = body.host
    return {"status": "connected", "host": body.host}


@router.post("/start")
async def start(body: StartRequest):
    if not _state["connected"] or _state.get("creds") is None:
        raise HTTPException(status_code=409, detail="not connected")

    client = _state.get("client")
    if client is None or not client.is_connected:
        # Rebuild + reconnect after a prior stop closed the link.
        client = _build_client(_state["creds"])
        try:
            await asyncio.to_thread(client.connect)
        except (paramiko.SSHException, OSError) as exc:
            raise HTTPException(status_code=502, detail=f"SSH reconnect failed ({type(exc).__name__})")
        _state["client"] = client

    cfg = _state.get("config") or {}
    base = cfg.get("session_dir", "~/drishti_sessions").rstrip("/")
    client.session_dir = f"{base}/{body.session_name}"
    client._stop.clear()
    _reset_live()

    thread = threading.Thread(
        target=client.tail_timestamps, args=(_on_record,), daemon=True
    )
    _state["tail_thread"] = thread
    _state["running"] = True
    thread.start()
    return {"status": "started", "session_name": body.session_name, "altitude_m": body.altitude_m}


@router.post("/stop")
async def stop():
    _state["running"] = False
    client = _state.get("client")
    if client is not None:
        await asyncio.to_thread(client.disconnect)
    return {"status": "stopped"}


@router.get("/preflight")
async def preflight(fail_demo: bool = False):
    if fail_demo:
        return {
            "gps_fix": {
                "pass": False,
                "hdop": 2.8,
                "satellites": 5,
                "message": "Poor fix, HDOP 2.8 (threshold: 2.0)",
            },
            "heading": {
                "pass": False,
                "heading_deg": 47.3,
                "variance_deg": 18.0,
                "message": "Unstable heading, variance 18.0° (threshold: 5°)",
            },
            "frame_counter": {"pass": True, "fps": 10.2, "message": "Camera streaming at 10.2 fps"},
            "tile_db": {"pass": True, "tile_count": 480, "index_path": "~/datasets/faiss_index/deolali_z19", "message": "480 tiles loaded"},
            "disk_space": {"pass": True, "free_gb": 12.4, "message": "12.4 GB free"},
            "gsd_norm": {"pass": True, "enabled": True, "message": "GSD normalisation enabled (target 0.28 m/px)"},
        }
    return {
        "gps_fix": {"pass": True, "hdop": 0.8, "satellites": 14, "message": "Fix acquired, HDOP 0.8"},
        "heading": {"pass": True, "heading_deg": 47.3, "variance_deg": 2.1, "message": "Stable at 47.3°"},
        "frame_counter": {"pass": True, "fps": 10.2, "message": "Camera streaming at 10.2 fps"},
        "tile_db": {"pass": True, "tile_count": 480, "index_path": "~/datasets/faiss_index/deolali_z19", "message": "480 tiles loaded"},
        "disk_space": {"pass": True, "free_gb": 12.4, "message": "12.4 GB free"},
        "gsd_norm": {"pass": True, "enabled": True, "message": "GSD normalisation enabled (target 0.28 m/px)"},
    }


@router.get("/status")
async def status():
    async def event_generator():
        # Dev/idle fallback state — keeps the Live Map animating without a real RPi.
        disk_mb = 48_000
        heading = 45.0

        while True:
            client = _state.get("client")
            if _state["running"] and client is not None:
                with _live_lock:
                    snap = dict(_live)
                payload = _live_payload(snap, client.connection_status)
            else:
                # GPS random walk so logger_state (→ /session/live, Panel 2) keeps moving.
                _state["lat"] += random.uniform(-0.00005, 0.00005)
                _state["lon"] += random.uniform(-0.00005, 0.00005)
                heading = (heading + random.uniform(-5.0, 5.0)) % 360.0
                conn = client.connection_status if (client is not None and client.is_connected) else None
                payload = {
                    "frames_captured": 0,
                    "gps_quality": round(random.uniform(1.8, 2.8), 2),
                    "heading_deg": round(heading, 1),
                    "disk_mb_remaining": disk_mb,
                    "fix_count": random.randint(4, 7),
                    "lat": round(_state["lat"], 7),
                    "lon": round(_state["lon"], 7),
                    "altitude_m": None,
                    "unix_ms": None,
                    "timestamp_ms": int(time.time() * 1000),
                    "connection_status": conn,
                }
            yield {"data": json.dumps(payload)}
            await asyncio.sleep(1.0)

    return EventSourceResponse(event_generator())
