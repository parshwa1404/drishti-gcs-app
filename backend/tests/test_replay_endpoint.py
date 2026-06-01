"""
Integration tests for /replay/* endpoints.
"""
import pytest
from fastapi.testclient import TestClient
from pathlib import Path

from main import app

client = TestClient(app)


def _make_session(tmp_path: Path, n_rows: int = 5) -> Path:
    sd = tmp_path / "ep_session"
    sd.mkdir()
    frames_dir = sd / "frames"
    frames_dir.mkdir()
    base_ms = 1_717_490_000_000
    lines = []
    for i in range(n_rows):
        ms = base_ms + i * 200
        lines.append(f"{ms},frames/{ms}.jpg,19.91,73.82,82.4,210.5,1.1,7,10.0\n")
        (frames_dir / f"{ms}.jpg").write_bytes(b"\xff\xd8\xff\xe0")
    (sd / "timestamps.csv").write_text("".join(lines))
    return sd


@pytest.fixture
def session_path(tmp_path):
    return _make_session(tmp_path)


@pytest.fixture
def opened_session(session_path):
    r = client.post(f"/replay/open/ep_session?path={session_path}")
    assert r.status_code == 200
    return r.json()


def test_open_session(session_path):
    r = client.post(f"/replay/open/ep_session?path={session_path}")
    assert r.status_code == 200
    body = r.json()
    assert body["session_id"] == "ep_session"
    assert body["total_rows"] == 5
    assert body["has_overlay"] is False


def test_open_session_unknown_path():
    r = client.post("/replay/open/nosuch?path=/tmp/__no_such_dir__")
    assert r.status_code == 404


def test_get_frame_metadata(opened_session, session_path):
    r = client.get("/replay/ep_session/frame/0")
    assert r.status_code == 200
    body = r.json()
    assert body["row"] == 0
    assert body["unix_ms"] == 1_717_490_000_000
    assert body["jpeg_available"] is True
    assert body["lat"] == pytest.approx(19.91)
    assert body["hdop"] == pytest.approx(1.1)
    assert body["satellite_count"] == 7
    assert body["disk_free_gb"] == pytest.approx(10.0)


def test_get_frame_out_of_range(opened_session):
    r = client.get("/replay/ep_session/frame/999")
    assert r.status_code == 404


def test_get_jpeg(opened_session):
    r = client.get("/replay/ep_session/jpeg/0")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/jpeg"


def test_seek_endpoint(opened_session):
    target_ms = 1_717_490_000_000 + 2 * 200
    r = client.get(f"/replay/ep_session/seek?ts_ms={target_ms}")
    assert r.status_code == 200
    assert r.json()["row"] == 2


def test_next_low_inlier_no_overlay(opened_session):
    r = client.get("/replay/ep_session/next-low-inlier?from=0&dir=fwd")
    assert r.status_code == 200
    assert r.json()["row"] is None


def test_unknown_session_404():
    r = client.get("/replay/__unknown__/frame/0")
    assert r.status_code == 404
    r = client.get("/replay/__unknown__/jpeg/0")
    assert r.status_code == 404


def test_sessions_list_empty(monkeypatch):
    monkeypatch.setenv("SESSIONS_ROOT", "/tmp/__no_such_sessions_root__")
    r = client.get("/replay/sessions")
    assert r.status_code == 200
    assert r.json() == []
