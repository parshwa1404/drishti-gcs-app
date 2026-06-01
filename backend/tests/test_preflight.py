import math

import pytest

from services.preflight import (
    CAUTION,
    FAIL,
    GO,
    NO_GO,
    PASS,
    STUBBED_CHECKS,
    UNAVAILABLE,
    WARN,
    PreflightEvaluator,
    load_preflight_config,
)

CFG = {
    "altitude_min_m": 50.0,
    "altitude_max_m": 200.0,
    "frame_rate_window_s": 10.0,
    "frame_rate_pass_hz": 5.0,
    "frame_rate_warn_hz": 3.0,
    "frame_max_age_pass_s": 5.0,
    "frame_max_age_warn_s": 15.0,
    "heading_stuck_count": 10,
}


def _rec(unix_ms, lat=19.9, lon=73.8, alt=80.0, hdg=120.0):
    return {
        "unix_ms": unix_ms,
        "frame_path": "f.jpg",
        "lat": lat,
        "lon": lon,
        "altitude_m": alt,
        "heading_deg": hdg,
    }


def _by_id(report, cid):
    return next(c for c in report["checks"] if c["check_id"] == cid)


def _state(ev, cid, **build_kw):
    return _by_id(ev.build_report(**build_kw), cid)["state"]


def _feed_good(ev, n=50, base=0, step=200):
    """n good frames, headings varied so the stuck detector stays quiet."""
    for i in range(n):
        ev.observe(_rec(base + i * step, hdg=100.0 + i * 0.3))
    return base + (n - 1) * step  # last unix_ms


# ─── rpi_connection ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("cs,expected", [
    ("connected", PASS),
    ("waiting for logger", PASS),
    ("reconnecting", WARN),
    ("error", FAIL),
    ("disconnected", FAIL),
    (None, FAIL),
])
def test_rpi_connection_states(cs, expected):
    ev = PreflightEvaluator(CFG)
    assert _state(ev, "rpi_connection", connection_status=cs, now_ms=0) == expected


# ─── logger_active ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("age_s,expected", [(2, PASS), (10, WARN), (20, FAIL)])
def test_logger_active_age(age_s, expected):
    ev = PreflightEvaluator(CFG)
    ev.observe(_rec(1000))
    assert _state(ev, "logger_active", now_ms=1000 + age_s * 1000) == expected


def test_logger_active_never_received_fails():
    ev = PreflightEvaluator(CFG)
    assert _state(ev, "logger_active", now_ms=5000) == FAIL


# ─── frame_rate ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("n,expected", [(50, PASS), (40, WARN), (30, WARN), (20, FAIL)])
def test_frame_rate_buckets(n, expected):
    ev = PreflightEvaluator(CFG)
    last = _feed_good(ev, n=n, step=200)
    assert _state(ev, "frame_rate", now_ms=last) == expected


def test_frame_rate_rolling_window_excludes_old_frames():
    ev = PreflightEvaluator(CFG)
    # 100 frames over 20 s; only the last 10 s should count.
    last = _feed_good(ev, n=100, step=200)  # 0 .. 19800
    rep = ev.build_report(now_ms=last)
    fr = _by_id(rep, "frame_rate")
    # frames in [9800, 19800] = 51 → 5.1 Hz, NOT 10 Hz (all 100)
    assert fr["value"] == 5.1


# ─── gps_fix ──────────────────────────────────────────────────────────────────

def test_gps_fix_valid_pass():
    ev = PreflightEvaluator(CFG)
    ev.observe(_rec(1000, lat=19.9, lon=73.8))
    assert _state(ev, "gps_fix", now_ms=1000) == PASS


def test_gps_fix_zero_sentinel_fails_not_warn():
    ev = PreflightEvaluator(CFG)
    ev.observe(_rec(1000, lat=0.0, lon=0.0))
    assert _state(ev, "gps_fix", now_ms=1000) == FAIL


def test_gps_fix_nan_fails():
    ev = PreflightEvaluator(CFG)
    ev.observe(_rec(1000, lat=float("nan"), lon=73.8))
    assert _state(ev, "gps_fix", now_ms=1000) == FAIL


def test_gps_fix_near_zero_warns():
    ev = PreflightEvaluator(CFG)
    ev.observe(_rec(1000, lat=0.005, lon=73.8))
    assert _state(ev, "gps_fix", now_ms=1000) == WARN


# ─── altitude_sane ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("alt,expected", [
    (80.0, PASS),
    (52.0, WARN),    # within 10% of min (50→55)
    (190.0, WARN),   # within 10% of max (200→180)
    (40.0, FAIL),
    (210.0, FAIL),
])
def test_altitude_buckets(alt, expected):
    ev = PreflightEvaluator(CFG)
    ev.observe(_rec(1000, alt=alt))
    assert _state(ev, "altitude_sane", now_ms=1000) == expected


def test_altitude_nan_fails():
    ev = PreflightEvaluator(CFG)
    ev.observe(_rec(1000, alt=float("nan")))
    assert _state(ev, "altitude_sane", now_ms=1000) == FAIL


# ─── heading_present ──────────────────────────────────────────────────────────

def test_heading_valid_pass():
    ev = PreflightEvaluator(CFG)
    ev.observe(_rec(1000, hdg=123.4))
    assert _state(ev, "heading_present", now_ms=1000) == PASS


def test_heading_null_fails():
    ev = PreflightEvaluator(CFG)
    ev.observe(_rec(1000, hdg=None))
    assert _state(ev, "heading_present", now_ms=1000) == FAIL


def test_heading_stuck_ten_identical_warns():
    ev = PreflightEvaluator(CFG)
    for i in range(10):
        ev.observe(_rec(1000 + i * 100, hdg=200.0))
    assert _state(ev, "heading_present", now_ms=1900) == WARN


def test_heading_not_stuck_below_threshold_passes():
    ev = PreflightEvaluator(CFG)
    for i in range(9):
        ev.observe(_rec(1000 + i * 100, hdg=200.0))
    assert _state(ev, "heading_present", now_ms=1900) == PASS


# ─── overall verdict ──────────────────────────────────────────────────────────

def test_overall_go_when_all_pass():
    ev = PreflightEvaluator(CFG)
    last = _feed_good(ev)
    assert ev.build_report(connection_status="connected", now_ms=last)["overall"] == GO


def test_overall_caution_on_warn_no_fail():
    ev = PreflightEvaluator(CFG)
    last = _feed_good(ev)
    # reconnecting → rpi_connection warn, everything else passes
    assert ev.build_report(connection_status="reconnecting", now_ms=last)["overall"] == CAUTION


def test_overall_nogo_on_any_fail():
    ev = PreflightEvaluator(CFG)
    last = _feed_good(ev)
    assert ev.build_report(connection_status="error", now_ms=last)["overall"] == NO_GO


def test_unavailable_checks_do_not_block_go():
    ev = PreflightEvaluator(CFG)
    last = _feed_good(ev)
    rep = ev.build_report(connection_status="connected", now_ms=last)
    stubbed = [c for c in rep["checks"] if c["check_id"] in STUBBED_CHECKS]
    assert len(stubbed) == len(STUBBED_CHECKS)
    assert all(c["state"] == UNAVAILABLE for c in stubbed)
    assert rep["overall"] == GO  # GO despite all stubbed being unavailable


def test_stub_message_names_awaited_field():
    # gps_hdop is now a real check; verify a remaining stub still uses the old message
    ev = PreflightEvaluator(CFG)
    rep = ev.build_report(connection_status="connected", now_ms=0)
    cam = _by_id(rep, "camera_exposure")
    assert cam["message"] == "Awaiting RPi logger field: camera_exposure"


# ─── configurable thresholds ──────────────────────────────────────────────────

def test_thresholds_are_respected():
    cfg = dict(CFG, altitude_max_m=100.0, frame_rate_pass_hz=8.0)
    ev = PreflightEvaluator(cfg)
    last = _feed_good(ev, n=50, step=200)  # 5 Hz
    ev.observe(_rec(last + 200, alt=95.0, hdg=130.0))  # 95 m: pass under 200, warn under 100
    rep = ev.build_report(connection_status="connected", now_ms=last + 200)
    # 5 Hz now only warns (pass threshold raised to 8)
    assert _by_id(rep, "frame_rate")["state"] == WARN
    # altitude 95 inside [50,100] but >90% of the lowered max (100→90) → warn
    assert _by_id(rep, "altitude_sane")["state"] == WARN


# ─── config loader ────────────────────────────────────────────────────────────

def test_load_preflight_config_defaults_and_file(tmp_path):
    cfg = tmp_path / "preflight.yaml"
    cfg.write_text("preflight:\n  altitude_max_m: 150.0\n")
    data = load_preflight_config(str(cfg))
    assert data["altitude_max_m"] == 150.0      # from file
    assert data["altitude_min_m"] == 50.0       # default fills the rest
    assert data["heading_stuck_count"] == 10
    assert data["hdop_pass_max"] == 2.0         # new defaults present


# ─── gps_hdop ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("hdop,expected", [
    (1.2, PASS),
    (3.5, WARN),
    (7.0, FAIL),
    (float("nan"), FAIL),
])
def test_gps_hdop_states(hdop, expected):
    ev = PreflightEvaluator(CFG)
    ev.observe({**_rec(1000), "hdop": hdop, "satellite_count": 8, "disk_free_gb": 10.0})
    assert _state(ev, "gps_hdop", now_ms=1000) == expected


def test_gps_hdop_none_is_unavailable():
    ev = PreflightEvaluator(CFG)
    ev.observe({**_rec(1000), "hdop": None, "satellite_count": None, "disk_free_gb": None})
    assert _state(ev, "gps_hdop", now_ms=1000) == UNAVAILABLE


def test_gps_hdop_absent_key_is_unavailable():
    # Record has none of the new keys (old-format log)
    ev = PreflightEvaluator(CFG)
    ev.observe(_rec(1000))
    assert _state(ev, "gps_hdop", now_ms=1000) == UNAVAILABLE


# ─── satellite_count ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("sats,expected", [
    (10, PASS),
    (6,  WARN),
    (3,  FAIL),
])
def test_satellite_count_states(sats, expected):
    ev = PreflightEvaluator(CFG)
    ev.observe({**_rec(1000), "hdop": 1.0, "satellite_count": sats, "disk_free_gb": 10.0})
    assert _state(ev, "satellite_count", now_ms=1000) == expected


def test_satellite_count_none_is_unavailable():
    ev = PreflightEvaluator(CFG)
    ev.observe({**_rec(1000), "hdop": 1.0, "satellite_count": None, "disk_free_gb": 10.0})
    assert _state(ev, "satellite_count", now_ms=1000) == UNAVAILABLE


# ─── disk_free ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("disk_gb,expected", [
    (50.0, PASS),
    (3.0,  WARN),
    (0.5,  FAIL),
    (float("nan"), FAIL),
])
def test_disk_free_states(disk_gb, expected):
    ev = PreflightEvaluator(CFG)
    ev.observe({**_rec(1000), "hdop": 1.0, "satellite_count": 8, "disk_free_gb": disk_gb})
    assert _state(ev, "disk_free", now_ms=1000) == expected


def test_disk_free_none_is_unavailable():
    ev = PreflightEvaluator(CFG)
    ev.observe({**_rec(1000), "hdop": 1.0, "satellite_count": 8, "disk_free_gb": None})
    assert _state(ev, "disk_free", now_ms=1000) == UNAVAILABLE


# ─── rollup with new checks ───────────────────────────────────────────────────

def _full_rec(unix_ms, hdop=1.0, sats=10, disk_gb=20.0, **kw):
    return {**_rec(unix_ms, **kw), "hdop": hdop, "satellite_count": sats, "disk_free_gb": disk_gb}


def test_rollup_go_with_new_checks_all_pass():
    ev = PreflightEvaluator(CFG)
    last = max(_feed_good(ev, n=50, step=200),
               max(ev._last_unix_ms or 0, 0))
    # Replace last frame with one that includes the new fields
    ev.observe(_full_rec(last, hdop=1.0, sats=10, disk_gb=20.0, hdg=130.0))
    assert ev.build_report(connection_status="connected", now_ms=last)["overall"] == GO


def test_rollup_nogo_when_hdop_fails():
    ev = PreflightEvaluator(CFG)
    last = _feed_good(ev)
    ev.observe(_full_rec(last + 200, hdop=7.0, sats=10, disk_gb=20.0, hdg=130.0))
    assert ev.build_report(connection_status="connected", now_ms=last + 200)["overall"] == NO_GO


def test_rollup_nogo_when_satellites_fail():
    ev = PreflightEvaluator(CFG)
    last = _feed_good(ev)
    ev.observe(_full_rec(last + 200, hdop=1.0, sats=3, disk_gb=20.0, hdg=130.0))
    assert ev.build_report(connection_status="connected", now_ms=last + 200)["overall"] == NO_GO


def test_rollup_nogo_when_disk_fails():
    ev = PreflightEvaluator(CFG)
    last = _feed_good(ev)
    ev.observe(_full_rec(last + 200, hdop=1.0, sats=10, disk_gb=0.5, hdg=130.0))
    assert ev.build_report(connection_status="connected", now_ms=last + 200)["overall"] == NO_GO


# ─── backward compatibility ───────────────────────────────────────────────────

def test_backward_compat_old_format_log_all_unavailable_go():
    """Old-format records (no hdop/sats/disk keys) → new checks unavailable, overall GO."""
    ev = PreflightEvaluator(CFG)
    last = _feed_good(ev)  # uses _rec() which has no new keys
    rep = ev.build_report(connection_status="connected", now_ms=last)
    assert _by_id(rep, "gps_hdop")["state"] == UNAVAILABLE
    assert _by_id(rep, "satellite_count")["state"] == UNAVAILABLE
    assert _by_id(rep, "disk_free")["state"] == UNAVAILABLE
    assert rep["overall"] == GO  # unavailable does not block GO
