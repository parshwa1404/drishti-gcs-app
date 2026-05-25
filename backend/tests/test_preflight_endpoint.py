"""
Panel 6 endpoint: the /preflight/status payload is well-formed and reflects the
RPi connection state within one update cycle (each call rebuilds the report).
"""

import routers.preflight as pf
from services.preflight import (
    FAIL,
    GO,
    MANDATORY_CHECKS,
    NO_GO,
    STUBBED_CHECKS,
)

_ALL_CHECK_IDS = set(MANDATORY_CHECKS) | set(STUBBED_CHECKS)


def _rec(unix_ms, lat=19.9, lon=73.8, alt=80.0, hdg=120.0):
    return {
        "unix_ms": unix_ms,
        "frame_path": "f.jpg",
        "lat": lat,
        "lon": lon,
        "altitude_m": alt,
        "heading_deg": hdg,
    }


def test_payload_is_well_formed(monkeypatch):
    monkeypatch.setattr(pf.logger, "current_connection_status", lambda: "connected")
    payload = pf._payload()

    assert payload["overall"] in (GO, "CAUTION", NO_GO)
    assert isinstance(payload["timestamp_ms"], int)

    ids = {c["check_id"] for c in payload["checks"]}
    assert ids == _ALL_CHECK_IDS
    for c in payload["checks"]:
        assert set(c.keys()) == {"check_id", "state", "value", "message"}
        assert c["state"] in ("pass", "warn", "fail", "unavailable")


def test_update_rate_is_one_second():
    assert pf._INTERVAL_S == 1.0


def test_disconnected_propagates_to_nogo(monkeypatch):
    # Populate a healthy rolling window first.
    for i in range(50):
        pf._evaluator.observe(_rec(i * 200, hdg=100.0 + i * 0.3))

    monkeypatch.setattr(pf.logger, "current_connection_status", lambda: "error")
    payload = pf._payload()

    rpi = next(c for c in payload["checks"] if c["check_id"] == "rpi_connection")
    assert rpi["state"] == FAIL
    assert payload["overall"] == NO_GO
