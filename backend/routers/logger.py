import asyncio
import json
import random
import shlex
import threading
import time
from datetime import datetime, timezone

import paramiko
import yaml
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
    key_path: str = ''
    password: str = ''


class StartRequest(BaseModel):
    altitude_m: float = 80.0
    session_name: str


# Module-level state shared across requests.
# `lat`/`lon` are read by routers.session (/session/live, Panel 2) — keep them.
_state = {
    "connected": False,
    "running": False,
    "host": None,
    # GPS position mirrored from the live tail; None until first real fix arrives.
    "lat": None,
    "lon": None,
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
    password = creds.get("password") or None
    # Don't fall back to the yaml key_path when the user supplies a password —
    # they have explicitly chosen password auth.
    key_path = creds.get("key_path") or ('' if password else cfg.get("key_path", ""))
    return RpiSshClient(
        hostname=creds["host"] or cfg.get("hostname", ""),
        username=creds["user"] or cfg.get("username", "pi"),
        key_path=key_path,
        password=password,
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
    creds = {"host": body.host, "user": body.user, "key_path": body.key_path, "password": body.password}
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
        while True:
            client = _state.get("client")
            if _state["running"] and client is not None:
                with _live_lock:
                    snap = dict(_live)
                payload = _live_payload(snap, client.connection_status)
            else:
                conn = client.connection_status if (client is not None and client.is_connected) else None
                payload = {
                    "frames_captured": 0,
                    "gps_quality":      None,
                    "heading_deg":      None,
                    "disk_mb_remaining": None,
                    "fix_count":        None,
                    "lat":              None,
                    "lon":              None,
                    "altitude_m":       None,
                    "unix_ms":          None,
                    "timestamp_ms":     int(time.time() * 1000),
                    "connection_status": conn,
                }
            yield {"data": json.dumps(payload)}
            await asyncio.sleep(1.0)

    return EventSourceResponse(event_generator())


@router.get("/state")
async def state():
    """Current SSH connection state — used by panels to restore UI on mount."""
    client = _state.get("client")
    conn_status = client.connection_status if client is not None else None
    return {
        "connected": _state["connected"],
        "host":      _state["host"],
        "running":   _state["running"],
        "connection_status": conn_status,
    }


def _parse_bag_meta(client, bag_path: str) -> dict:
    """Read metadata.yaml from a ROS2 bag dir and return display-friendly fields."""
    # Check whether the actual .db3 data file exists (metadata.yaml can exist alone
    # when a recording was started but the data was never written).
    _, stdout, _ = client._client.exec_command(
        f"find {shlex.quote(bag_path)} -maxdepth 1 -name '*.db3' 2>/dev/null | head -1"
    )
    has_data = bool(stdout.read().decode().strip())

    _, stdout, _ = client._client.exec_command(
        f"cat {shlex.quote(bag_path + '/metadata.yaml')} 2>/dev/null"
    )
    raw = stdout.read().decode("utf-8", errors="replace")
    if not raw.strip():
        return {"has_data": has_data}
    try:
        meta = yaml.safe_load(raw) or {}
        bag_info = meta.get("rosbag2_bagfile_information", {})
        duration_ns = bag_info.get("duration", {}).get("nanoseconds", 0)
        start_ns    = bag_info.get("starting_time", {}).get("nanoseconds_since_epoch", 0)
        message_count = bag_info.get("message_count", 0)
        topics = {
            t["topic_metadata"]["name"]: t["message_count"]
            for t in bag_info.get("topics_with_message_count", [])
        }
        frame_count = topics.get("/camera/color/image_raw", 0)
        gps_count   = topics.get("/mavros/global_position/raw/fix", 0)
        start_time  = ""
        if start_ns:
            start_ms = start_ns // 1_000_000
            start_time = datetime.fromtimestamp(
                start_ms / 1000.0, tz=timezone.utc
            ).strftime("%Y-%m-%d %H:%M:%S UTC")
        return {
            "has_data":      has_data,
            "duration_s":    round(duration_ns / 1e9, 1) if duration_ns else 0,
            "frame_count":   frame_count,
            "gps_count":     gps_count,
            "message_count": message_count,
            "start_time":    start_time,
            "topics":        topics,
        }
    except Exception:
        return {"has_data": has_data}


@router.get("/sessions")
async def list_sessions():
    """List session/bag directories in the remote session_dir, with metadata."""
    client = _state.get("client")
    if client is None or not client.is_connected:
        raise HTTPException(status_code=409, detail="not connected — connect in the Logging panel first")
    cfg = _state.get("config") or {}
    base = cfg.get("session_dir", "~/bags")

    # Expand ~ on the remote side (shlex.quote prevents tilde expansion)
    _, stdout, _ = client._client.exec_command(f"echo {base}")
    abs_base = stdout.read().decode("utf-8", errors="replace").strip() or base

    _, stdout, _ = client._client.exec_command(
        f"find {shlex.quote(abs_base)} -maxdepth 1 -mindepth 1 -type d 2>/dev/null; true"
    )
    raw = stdout.read().decode("utf-8", errors="replace").strip()
    paths = sorted(line for line in raw.splitlines() if line.strip())

    sessions = []
    for p in paths:
        meta = _parse_bag_meta(client, p)
        sessions.append({"path": p, "name": p.rsplit("/", 1)[-1], **meta})

    return {"sessions": sessions, "base_dir": abs_base}
