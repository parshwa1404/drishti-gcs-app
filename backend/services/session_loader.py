"""
Load a session directory into memory.

Expected layout:
  session_dir/
    frames/          *.jpg files named {unix_ms}.jpg
    gps.nmea         raw NMEA sentences
    timestamps.csv   optional per-frame log (drishti-rpi-logger); adds altitude
"""

from pathlib import Path
from services.nmea_parser import parse_nmea_file
from services.timestamps_csv import read_timestamps_csv


def load_session(session_dir: str) -> dict:
    root = Path(session_dir)

    if not root.exists():
        raise FileNotFoundError(f"Session directory not found: {session_dir}")

    frames_dir = root / "frames"
    if not frames_dir.exists():
        raise FileNotFoundError(f"No frames/ subdirectory in {session_dir}")

    jpg_files = sorted(frames_dir.glob("*.jpg"), key=lambda p: int(p.stem))
    if not jpg_files:
        raise ValueError("No .jpg frames found in session")

    frame_timestamps = [int(p.stem) for p in jpg_files]
    frame_map = {int(p.stem): str(p) for p in jpg_files}

    nmea_path = root / "gps.nmea"
    gps_fixes = parse_nmea_file(str(nmea_path)) if nmea_path.exists() else []

    gps_track = [{"lat": f["lat"], "lon": f["lon"]} for f in gps_fixes]

    frames = []
    for ts_ms in frame_timestamps:
        best: dict | None = None
        best_dt = float("inf")
        for fix in gps_fixes:
            dt = abs(fix["timestamp_ms"] - ts_ms)
            if dt < best_dt:
                best_dt = dt
                best = fix

        if best and best_dt <= 1500:
            frames.append({
                "timestamp_ms": ts_ms,
                "lat": best["lat"],
                "lon": best["lon"],
                "hdop": best["hdop"],
                "heading_deg": best["heading_deg"],
                "frame_path": f"frames/{ts_ms}.jpg",
            })
        else:
            frames.append({
                "timestamp_ms": ts_ms,
                "lat": None,
                "lon": None,
                "hdop": None,
                "heading_deg": None,
                "frame_path": f"frames/{ts_ms}.jpg",
            })

    # Per-frame altitude from timestamps.csv when the logger wrote one.
    ts_path = root / "timestamps.csv"
    if ts_path.exists():
        alt_by_ts = {r["unix_ms"]: r["altitude_m"] for r in read_timestamps_csv(str(ts_path))}
        for f in frames:
            alt = alt_by_ts.get(f["timestamp_ms"])
            if alt is not None:
                f["altitude_m"] = alt

    duration_s = 0.0
    if len(frame_timestamps) > 1:
        duration_s = round((frame_timestamps[-1] - frame_timestamps[0]) / 1000.0, 1)

    return {
        "session_name": root.name,
        "frame_count": len(frames),
        "duration_s": duration_s,
        "frames": frames,
        "gps_track": gps_track,
        "frame_map": frame_map,      # int(ts_ms) → absolute path; internal use only
        "session_dir": str(root),
    }
