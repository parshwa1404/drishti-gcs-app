"""
Stream-based replay service for post-flight review.

ReplaySession opens timestamps.csv once, builds an (row → file_offset) index
in a single pass, then serves individual rows via O(1) seeks.  No entire CSV
is held in memory beyond that index.
"""

from __future__ import annotations

import json
import os
from bisect import bisect_left
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from services.timestamps_csv import parse_timestamps_line

_LOW_INLIER_THRESHOLD = 10  # inlier_count < this → "low inlier frame"


@dataclass
class ReplayRecord:
    row: int
    unix_ms: int
    frame_path: str          # relative path within session dir
    lat: Optional[float]
    lon: Optional[float]
    altitude_m: Optional[float]
    heading_deg: Optional[float]
    hdop: Optional[float]
    satellite_count: Optional[int]
    disk_free_gb: Optional[float]
    jpeg_available: bool
    overlay: Optional[dict] = field(default=None)


class ReplaySession:
    def __init__(self, session_dir: Path) -> None:
        self.session_dir = session_dir
        self.session_id = session_dir.name

        csv_path = session_dir / "timestamps.csv"
        if not csv_path.exists():
            raise FileNotFoundError(f"timestamps.csv not found in {session_dir}")

        # Single-pass index build: row → byte offset of that line in the file.
        self._offsets: list[int] = []
        self._unix_ms_list: list[int] = []   # parallel list for seek-by-ts

        with open(csv_path, "rb") as fh:
            while True:
                offset = fh.tell()
                raw = fh.readline()
                if not raw:
                    break
                rec = parse_timestamps_line(raw.decode("utf-8", errors="replace"))
                if rec is not None:
                    self._offsets.append(offset)
                    self._unix_ms_list.append(rec["unix_ms"])

        self._csv_path = csv_path

        # Load overlay JSON once (indexed by unix_ms then by row as fallback).
        self._overlay_by_ms: dict[int, dict] = {}
        self._overlay_by_row: dict[int, dict] = {}
        self._low_inlier_rows: list[int] = []   # sorted list of row indices
        self._load_overlay()

    # ── public API ────────────────────────────────────────────────────────────

    @property
    def total_rows(self) -> int:
        return len(self._offsets)

    def get_frame(self, row: int) -> ReplayRecord:
        if row < 0 or row >= len(self._offsets):
            raise IndexError(f"Row {row} out of range (0–{len(self._offsets)-1})")

        with open(self._csv_path, "rb") as fh:
            fh.seek(self._offsets[row])
            raw = fh.readline()

        rec = parse_timestamps_line(raw.decode("utf-8", errors="replace"))
        if rec is None:
            raise ValueError(f"Corrupt row at index {row}")

        jpeg_path = self.session_dir / rec["frame_path"]
        jpeg_available = jpeg_path.exists()

        overlay = self._overlay_by_ms.get(rec["unix_ms"]) or self._overlay_by_row.get(row)

        return ReplayRecord(
            row=row,
            unix_ms=rec["unix_ms"],
            frame_path=rec["frame_path"],
            lat=rec.get("lat"),
            lon=rec.get("lon"),
            altitude_m=rec.get("altitude_m"),
            heading_deg=rec.get("heading_deg"),
            hdop=rec.get("hdop"),
            satellite_count=rec.get("satellite_count"),
            disk_free_gb=rec.get("disk_free_gb"),
            jpeg_available=jpeg_available,
            overlay=overlay,
        )

    def get_jpeg_path(self, row: int) -> Optional[Path]:
        rec = self.get_frame(row)
        p = self.session_dir / rec.frame_path
        return p if p.exists() else None

    def seek_by_ts(self, ts_ms: int) -> int:
        """Return the row index closest to the given unix_ms timestamp."""
        if not self._unix_ms_list:
            return 0
        idx = bisect_left(self._unix_ms_list, ts_ms)
        if idx >= len(self._unix_ms_list):
            return len(self._unix_ms_list) - 1
        if idx > 0:
            before = abs(self._unix_ms_list[idx - 1] - ts_ms)
            after  = abs(self._unix_ms_list[idx] - ts_ms)
            if before < after:
                return idx - 1
        return idx

    def find_next_low_inlier(self, current_row: int, direction: str) -> Optional[int]:
        """Return the next low-inlier row in fwd or back direction, or None."""
        if not self._low_inlier_rows:
            return None
        if direction == "fwd":
            idx = bisect_left(self._low_inlier_rows, current_row + 1)
            return self._low_inlier_rows[idx] if idx < len(self._low_inlier_rows) else None
        else:
            idx = bisect_left(self._low_inlier_rows, current_row) - 1
            return self._low_inlier_rows[idx] if idx >= 0 else None

    def has_overlay(self) -> bool:
        return bool(self._overlay_by_ms or self._overlay_by_row)

    # ── internal ──────────────────────────────────────────────────────────────

    def _load_overlay(self) -> None:
        candidates = sorted(self.session_dir.glob("*pipeline*.json"))
        if not candidates:
            return
        try:
            data = json.loads(candidates[0].read_text(encoding="utf-8"))
        except Exception:
            return

        frames = data.get("frames") or data.get("results") or []
        for i, f in enumerate(frames):
            ts = f.get("timestamp_ms") or f.get("unix_ms")
            row = f.get("row", i)
            entry = dict(f)
            if ts is not None:
                self._overlay_by_ms[int(ts)] = entry
            self._overlay_by_row[row] = entry

        # Build sorted list of low-inlier row indices.
        low: list[int] = []
        for row_idx, entry in self._overlay_by_row.items():
            if (entry.get("inlier_count") or 0) < _LOW_INLIER_THRESHOLD:
                if entry.get("reject_reason") is None:  # skip hard-rejected
                    low.append(row_idx)
        # Also check ms-indexed entries not already covered.
        covered = set(self._overlay_by_row)
        for ts_ms, entry in self._overlay_by_ms.items():
            if ts_ms in self._unix_ms_list:
                r = self._unix_ms_list.index(ts_ms)
                if r not in covered:
                    if (entry.get("inlier_count") or 0) < _LOW_INLIER_THRESHOLD:
                        if entry.get("reject_reason") is None:
                            low.append(r)
        self._low_inlier_rows = sorted(set(low))


# ── Module-level session registry ─────────────────────────────────────────────

_sessions: dict[str, ReplaySession] = {}


def open_session(session_dir: str | Path) -> ReplaySession:
    p = Path(session_dir)
    sid = p.name
    sess = ReplaySession(p)
    _sessions[sid] = sess
    return sess


def get_session(session_id: str) -> Optional[ReplaySession]:
    return _sessions.get(session_id)


def list_sessions() -> list[dict]:
    """Return sessions found under SESSIONS_ROOT, sorted newest-first by mtime."""
    root = _sessions_root()
    if root is None or not root.exists():
        return []
    entries = []
    for d in root.iterdir():
        if not d.is_dir():
            continue
        csv = d / "timestamps.csv"
        if not csv.exists():
            continue
        stat = d.stat()
        entries.append({
            "session_id": d.name,
            "path": str(d),
            "mtime": stat.st_mtime,
        })
    entries.sort(key=lambda e: e["mtime"], reverse=True)
    for e in entries:
        del e["mtime"]
    return entries


def _sessions_root() -> Optional[Path]:
    val = os.getenv("SESSIONS_ROOT", "")
    if not val:
        return None
    return Path(os.path.expanduser(val))
