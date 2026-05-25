"""
End-to-end Panel 1: a fake SSH transport streams three frames; the records
fold into the router's shared live state and surface through the status payload
the frontend SSE endpoint serves.
"""

import threading

import routers.logger as logger_mod
from services.mock_ssh import FakeSSHClient
from services.ssh_client import RpiSshClient

_LINES = [
    "1717490000000,frames/1.jpg,19.9170,73.8270,80.5,210.5\n",
    "1717490000500,frames/2.jpg,19.9171,73.8271,81.2,215.0\n",
    "1717490001000,frames/3.jpg,19.9172,73.8272,82.3,220.0\n",
]


def test_three_frames_reach_status_payload():
    fake = FakeSSHClient(tail_lines=list(_LINES))
    client = RpiSshClient("rpi.local", "pi", "~/.ssh/k", "/sess", ssh_factory=lambda: fake)
    client.connect()

    logger_mod._reset_live()
    records: list[dict] = []
    done = threading.Event()

    def cb(rec):
        logger_mod._on_record(rec)
        records.append(rec)
        if len(records) == 3:
            done.set()

    t = threading.Thread(target=client.tail_timestamps, args=(cb,), daemon=True)
    t.start()
    assert done.wait(2.0)
    client.disconnect()
    t.join(2.0)

    # one connect, three structured records
    assert fake.connect_calls == 1
    assert len(records) == 3

    # endpoint payload reflects the streamed frames
    with logger_mod._live_lock:
        snap = dict(logger_mod._live)
    payload = logger_mod._live_payload(snap, client.connection_status)

    assert payload["frames_captured"] == 3
    assert payload["lat"] == 19.9172
    assert payload["altitude_m"] == 82.3
    assert payload["heading_deg"] == 220.0
    assert payload["unix_ms"] == 1717490001000
    # HDOP / disk / sats are not in timestamps.csv → null on the real path
    assert payload["gps_quality"] is None
    assert payload["disk_mb_remaining"] is None
