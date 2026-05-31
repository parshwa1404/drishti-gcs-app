import threading

import paramiko
import pytest

from services.mock_ssh import FakeSSHClient
from services.ssh_client import (
    STATUS_CONNECTED,
    STATUS_DISCONNECTED,
    STATUS_WAITING,
    RpiSshClient,
    load_rpi_config,
)
from services.timestamps_csv import parse_timestamps_line

_GOOD = "1717490000000,frames/1.jpg,19.9170,73.8270,80.5,210.5\n"


def _client(fake, **kw):
    return RpiSshClient("rpi.local", "pi", "~/.ssh/k", "/sess", ssh_factory=lambda: fake, **kw)


# ─── connect / disconnect happy path ──────────────────────────────────────────

def test_connect_then_disconnect():
    fake = FakeSSHClient()
    client = _client(fake)
    assert client.is_connected is False

    client.connect()
    assert client.is_connected is True
    assert client.connection_status == STATUS_CONNECTED
    assert fake.connect_calls == 1

    client.disconnect()
    assert client.is_connected is False
    assert client.connection_status == STATUS_DISCONNECTED
    assert fake.closed is True


# ─── missing timestamps.csv → waiting for logger ──────────────────────────────

def test_missing_csv_reports_waiting():
    fake = FakeSSHClient(file_exists=False)
    client = _client(fake, poll_interval_s=0.01)
    client.connect()

    seen = threading.Event()

    def sleep_fn(_s):
        seen.set()
        client._stop.set()  # break the loop after one poll

    client._sleep = sleep_fn
    client.tail_timestamps(lambda _r: None)

    assert seen.is_set()
    assert client.connection_status == STATUS_WAITING


# ─── malformed line skipped, error counter increments, altitude is float ──────

def test_malformed_line_skipped_and_altitude_parsed():
    lines = [
        "garbage,too,few\n",                                       # < 6 fields
        _GOOD,                                                     # valid
        "unix_ms,frame_path,lat,lon,altitude_m,heading_deg\n",     # header row
    ]
    fake = FakeSSHClient(tail_lines=lines)
    client = _client(fake)
    client.connect()

    records: list[dict] = []
    client._sleep = lambda _s: client._stop.set()
    client.tail_timestamps(records.append)

    assert len(records) == 1
    assert client.error_count == 2
    assert records[0]["altitude_m"] == 80.5
    assert isinstance(records[0]["altitude_m"], float)


# ─── three frames stream through to the callback ──────────────────────────────

def test_tail_streams_three_records():
    lines = [
        "1717490000000,frames/1.jpg,19.9170,73.8270,80.5,210.5\n",
        "1717490000500,frames/2.jpg,19.9171,73.8271,81.2,215.0\n",
        "1717490001000,frames/3.jpg,19.9172,73.8272,82.3,220.0\n",
    ]
    fake = FakeSSHClient(tail_lines=lines)
    client = _client(fake)
    client.connect()

    records: list[dict] = []
    done = threading.Event()

    def cb(rec):
        records.append(rec)
        if len(records) == 3:
            done.set()

    t = threading.Thread(target=client.tail_timestamps, args=(cb,), daemon=True)
    t.start()
    assert done.wait(2.0)
    client.disconnect()
    t.join(2.0)

    assert fake.connect_calls == 1   # single persistent connection
    assert [r["unix_ms"] for r in records] == [1717490000000, 1717490000500, 1717490001000]
    assert records[-1]["altitude_m"] == 82.3


# ─── reconnect with exponential backoff when connect keeps failing ────────────

def test_reconnect_exponential_backoff():
    fake = FakeSSHClient(connect_error=paramiko.SSHException("no route to host"))
    client = _client(fake, reconnect_max_backoff_s=30)

    sleeps: list[float] = []

    def sleep_fn(s):
        sleeps.append(s)
        if len(sleeps) >= 3:
            client._stop.set()

    client._sleep = sleep_fn
    client.tail_timestamps(lambda _r: None)

    assert sleeps[:3] == [1, 2, 4]
    assert fake.connect_calls >= 3


# ─── a dropped channel triggers a reconnect ───────────────────────────────────

def test_dropped_channel_triggers_reconnect():
    fake = FakeSSHClient(tail_error=paramiko.SSHException("channel closed"))
    client = _client(fake)
    client.connect()  # connect_calls == 1

    sleeps: list[float] = []

    def sleep_fn(s):
        sleeps.append(s)
        if len(sleeps) >= 2:
            client._stop.set()

    client._sleep = sleep_fn
    client.tail_timestamps(lambda _r: None)

    assert fake.connect_calls >= 2  # reconnected after the drop
    assert sleeps  # backoff sleep was invoked
    assert client.is_connected is False


# ─── config loader + env overrides ────────────────────────────────────────────

def test_load_rpi_config_defaults(tmp_path):
    cfg = tmp_path / "rpi.yaml"
    cfg.write_text("hostname: 10.0.0.5\nusername: pi\nsession_dir: ~/s\n")
    data = load_rpi_config(str(cfg))
    assert data["hostname"] == "10.0.0.5"
    assert data["session_dir"] == "~/s"


def test_load_rpi_config_env_override(tmp_path, monkeypatch):
    cfg = tmp_path / "rpi.yaml"
    cfg.write_text("hostname: 10.0.0.5\nusername: pi\n")
    monkeypatch.setenv("DRISHTI_RPI_HOST", "192.168.4.4")
    data = load_rpi_config(str(cfg))
    assert data["hostname"] == "192.168.4.4"  # env wins over yaml


# ─── parser unit checks ───────────────────────────────────────────────────────

def test_parse_header_and_blank_return_none():
    assert parse_timestamps_line("unix_ms,frame_path,lat,lon,altitude_m,heading_deg") is None
    assert parse_timestamps_line("") is None
    assert parse_timestamps_line("   ") is None


def test_parse_good_line():
    rec = parse_timestamps_line(_GOOD)
    assert rec == {
        "unix_ms": 1717490000000,
        "frame_path": "frames/1.jpg",
        "lat": 19.9170,
        "lon": 73.8270,
        "altitude_m": 80.5,
        "heading_deg": 210.5,
        "hdop": None,
        "satellite_count": None,
        "disk_free_gb": None,
    }
