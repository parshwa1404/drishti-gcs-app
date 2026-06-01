"""
Panel 6 pre-flight GO/NO-GO evaluation.

``PreflightEvaluator`` consumes the same per-frame records the Panel 1 SSH tail
emits (subscribed via ``routers.logger.subscribe_records``), keeps a small
rolling state, and produces a report of per-check states plus an overall
verdict. It does not touch the SSH transport or re-parse timestamps.csv.

Check state:   pass | warn | fail | unavailable
Overall:       GO (all mandatory pass) | CAUTION (any warn, no fail) |
               NO-GO (any fail). Unavailable checks never block GO.
"""

import math
import threading
import time
from collections import deque
from pathlib import Path

import yaml

PASS = "pass"
WARN = "warn"
FAIL = "fail"
UNAVAILABLE = "unavailable"

GO = "GO"
CAUTION = "CAUTION"
NO_GO = "NO-GO"

MANDATORY_CHECKS = [
    "rpi_connection",
    "logger_active",
    "frame_rate",
    "gps_fix",
    "altitude_sane",
    "heading_present",
    "gps_hdop",
    "satellite_count",
    "disk_free",
]

# Data not yet flowing — rendered as a grey "unavailable" badge.
STUBBED_CHECKS = [
    "camera_exposure",
    "fc_link",
    "tile_db_loaded",
]

_DEFAULTS = {
    "altitude_min_m": 50.0,
    "altitude_max_m": 200.0,
    "frame_rate_window_s": 10.0,
    "frame_rate_pass_hz": 5.0,
    "frame_rate_warn_hz": 3.0,
    "frame_max_age_pass_s": 5.0,
    "frame_max_age_warn_s": 15.0,
    "heading_stuck_count": 10,
    "hdop_pass_max": 2.0,
    "hdop_warn_max": 5.0,
    "satellite_pass_min": 8,
    "satellite_warn_min": 5,
    "disk_free_pass_min_gb": 5.0,
    "disk_free_warn_min_gb": 2.0,
}


def load_preflight_config(path: str | None = None) -> dict:
    """Load ``config/preflight.yaml`` (``preflight:`` block) over the defaults."""
    cfg_path = Path(path) if path else Path(__file__).resolve().parents[1] / "config" / "preflight.yaml"
    data: dict = {}
    if cfg_path.exists():
        with open(cfg_path) as fh:
            raw = yaml.safe_load(fh) or {}
        data = raw.get("preflight", raw)
    merged = dict(_DEFAULTS)
    merged.update({k: v for k, v in data.items() if v is not None})
    return merged


def _is_nan(x) -> bool:
    return isinstance(x, float) and math.isnan(x)


class PreflightEvaluator:
    def __init__(self, config: dict, now_fn=None):
        self.cfg = dict(_DEFAULTS)
        self.cfg.update(config or {})
        self._now_fn = now_fn or (lambda: time.time() * 1000.0)
        self._lock = threading.Lock()

        self._frames: deque = deque()       # unix_ms of recent frames
        self._last_unix_ms = None
        self._last_lat = None
        self._last_lon = None
        self._last_alt = None
        self._last_heading = None
        self._heading_run = 0
        self._prev_heading = None
        self._last_hdop = None
        self._last_satellite_count = None
        self._last_disk_free_gb = None

    # ─── ingest ──────────────────────────────────────────────────────────────

    def observe(self, rec: dict) -> None:
        with self._lock:
            ts = rec["unix_ms"]
            self._frames.append(ts)
            self._last_unix_ms = ts
            self._last_lat = rec["lat"]
            self._last_lon = rec["lon"]
            self._last_alt = rec["altitude_m"]
            self._last_heading = rec["heading_deg"]
            self._last_hdop = rec.get("hdop")
            self._last_satellite_count = rec.get("satellite_count")
            self._last_disk_free_gb = rec.get("disk_free_gb")

            h = rec["heading_deg"]
            if self._prev_heading is not None and h is not None and h == self._prev_heading:
                self._heading_run += 1
            else:
                self._heading_run = 1
            self._prev_heading = h

            # Bound memory to ~one window worth of frames.
            window_ms = self.cfg["frame_rate_window_s"] * 1000.0
            while self._frames and self._frames[0] < ts - window_ms:
                self._frames.popleft()

    # ─── report ──────────────────────────────────────────────────────────────

    def build_report(self, connection_status: str | None = None, now_ms: float | None = None) -> dict:
        if now_ms is None:
            now_ms = self._now_fn()
        with self._lock:
            frames = list(self._frames)
            last_unix_ms = self._last_unix_ms
            lat, lon = self._last_lat, self._last_lon
            alt, heading = self._last_alt, self._last_heading
            heading_run = self._heading_run
            hdop = self._last_hdop
            satellite_count = self._last_satellite_count
            disk_free_gb = self._last_disk_free_gb

        checks = [
            self._chk_rpi_connection(connection_status),
            self._chk_logger_active(now_ms, last_unix_ms),
            self._chk_frame_rate(now_ms, frames),
            self._chk_gps_fix(lat, lon),
            self._chk_altitude(alt),
            self._chk_heading(heading, heading_run),
            self._chk_gps_hdop(hdop),
            self._chk_satellite_count(satellite_count),
            self._chk_disk_free(disk_free_gb),
        ]
        checks += [self._stub(cid) for cid in STUBBED_CHECKS]

        states = [c["state"] for c in checks if c["check_id"] in MANDATORY_CHECKS]
        if FAIL in states:
            overall = NO_GO
        elif WARN in states:
            overall = CAUTION
        else:
            overall = GO

        return {"overall": overall, "checks": checks, "timestamp_ms": int(now_ms)}

    # ─── individual checks ───────────────────────────────────────────────────

    @staticmethod
    def _report(check_id, state, value, message) -> dict:
        return {"check_id": check_id, "state": state, "value": value, "message": message}

    @staticmethod
    def _stub(check_id) -> dict:
        return PreflightEvaluator._report(
            check_id, UNAVAILABLE, None, f"Awaiting RPi logger field: {check_id}"
        )

    def _chk_rpi_connection(self, cs) -> dict:
        if cs in ("connected", "waiting for logger"):
            return self._report("rpi_connection", PASS, cs, f"SSH {cs}")
        if cs == "reconnecting":
            return self._report("rpi_connection", WARN, cs, "SSH reconnecting")
        return self._report("rpi_connection", FAIL, cs, f"SSH {cs or 'not connected'}")

    def _chk_logger_active(self, now_ms, last_unix_ms) -> dict:
        if last_unix_ms is None:
            return self._report("logger_active", FAIL, None, "No frames received")
        age = round((now_ms - last_unix_ms) / 1000.0, 1)
        if age <= self.cfg["frame_max_age_pass_s"]:
            state = PASS
        elif age <= self.cfg["frame_max_age_warn_s"]:
            state = WARN
        else:
            state = FAIL
        return self._report("logger_active", state, age, f"Last frame {age}s ago")

    def _chk_frame_rate(self, now_ms, frames) -> dict:
        window_s = self.cfg["frame_rate_window_s"]
        cutoff = now_ms - window_s * 1000.0
        count = sum(1 for t in frames if t >= cutoff)
        hz = round(count / window_s, 1)
        if hz >= self.cfg["frame_rate_pass_hz"]:
            state = PASS
        elif hz >= self.cfg["frame_rate_warn_hz"]:
            state = WARN
        else:
            state = FAIL
        return self._report("frame_rate", state, hz, f"{hz} Hz over {window_s:g}s")

    def _chk_gps_fix(self, lat, lon) -> dict:
        if lat is None or lon is None or _is_nan(lat) or _is_nan(lon):
            return self._report("gps_fix", FAIL, None, "GPS lat/lon missing or NaN")
        value = f"{lat:.5f}, {lon:.5f}"
        if lat == 0 and lon == 0:
            return self._report("gps_fix", FAIL, value, "GPS sentinel 0,0 — no fix")
        if abs(lat) < 0.01 or abs(lon) < 0.01:
            return self._report("gps_fix", WARN, value, "GPS suspiciously near zero")
        if abs(lat) < 90 and abs(lon) < 180:
            return self._report("gps_fix", PASS, value, "GPS fix valid")
        return self._report("gps_fix", FAIL, value, "GPS out of range")

    def _chk_altitude(self, alt) -> dict:
        lo, hi = self.cfg["altitude_min_m"], self.cfg["altitude_max_m"]
        if alt is None or _is_nan(alt):
            return self._report("altitude_sane", FAIL, None, "Altitude missing or NaN")
        if alt < lo or alt > hi:
            return self._report("altitude_sane", FAIL, alt, f"{alt} m outside [{lo:g}, {hi:g}]")
        if alt < lo * 1.1 or alt > hi * 0.9:
            return self._report("altitude_sane", WARN, alt, f"{alt} m near bound")
        return self._report("altitude_sane", PASS, alt, f"{alt} m")

    def _chk_heading(self, heading, heading_run) -> dict:
        if heading is None:
            return self._report("heading_present", FAIL, None, "Heading missing")
        if not (0 <= heading < 360):
            return self._report("heading_present", FAIL, heading, f"Heading {heading} out of range")
        if heading_run >= self.cfg["heading_stuck_count"]:
            return self._report("heading_present", WARN, heading,
                                f"Heading stuck at {heading}° for {heading_run} frames")
        return self._report("heading_present", PASS, heading, f"{heading}°")

    def _chk_gps_hdop(self, hdop) -> dict:
        if hdop is None:
            return self._report("gps_hdop", UNAVAILABLE, None, "hdop column absent from log")
        if _is_nan(hdop) or hdop >= self.cfg["hdop_warn_max"]:
            return self._report("gps_hdop", FAIL, hdop, f"HDOP {hdop:.2f} ≥ {self.cfg['hdop_warn_max']}")
        if hdop >= self.cfg["hdop_pass_max"]:
            return self._report("gps_hdop", WARN, hdop, f"HDOP {hdop:.2f} (degraded)")
        return self._report("gps_hdop", PASS, hdop, f"HDOP {hdop:.2f}")

    def _chk_satellite_count(self, sats) -> dict:
        if sats is None:
            return self._report("satellite_count", UNAVAILABLE, None, "satellite_count column absent from log")
        if (_is_nan(sats) if isinstance(sats, float) else False) or sats < self.cfg["satellite_warn_min"]:
            return self._report("satellite_count", FAIL, sats, f"{sats} satellites < {self.cfg['satellite_warn_min']}")
        if sats < self.cfg["satellite_pass_min"]:
            return self._report("satellite_count", WARN, sats, f"{sats} satellites (degraded)")
        return self._report("satellite_count", PASS, sats, f"{sats} satellites")

    def _chk_disk_free(self, disk_gb) -> dict:
        if disk_gb is None:
            return self._report("disk_free", UNAVAILABLE, None, "disk_free_gb column absent from log")
        if _is_nan(disk_gb) or disk_gb < self.cfg["disk_free_warn_min_gb"]:
            return self._report("disk_free", FAIL, disk_gb, f"{disk_gb:.1f} GB free < {self.cfg['disk_free_warn_min_gb']} GB")
        if disk_gb <= self.cfg["disk_free_pass_min_gb"]:
            return self._report("disk_free", WARN, disk_gb, f"{disk_gb:.1f} GB free (low)")
        return self._report("disk_free", PASS, disk_gb, f"{disk_gb:.1f} GB free")
