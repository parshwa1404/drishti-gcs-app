"""
Unit tests for services/replay.py.

All tests use a synthetic session directory created in a tmp_path fixture;
no real flight data required.
"""
import json
import time
import pytest
from pathlib import Path

from services.replay import ReplaySession, open_session, get_session, list_sessions


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_session(tmp_path: Path, n_rows: int = 10, *, malformed_row: int = -1,
                  extra_cols: bool = False, create_jpegs: bool = True,
                  overlay: dict | None = None) -> Path:
    sd = tmp_path / "test_session"
    sd.mkdir()
    frames_dir = sd / "frames"
    frames_dir.mkdir()

    base_ms = 1_717_490_000_000
    lines = []
    for i in range(n_rows):
        ms = base_ms + i * 200  # 5 Hz
        fp = f"frames/{ms}.jpg"
        if i == malformed_row:
            lines.append("BADROW\n")
            continue
        if extra_cols:
            lines.append(f"{ms},{fp},19.91,73.82,82.4,210.5,1.2,8,12.34\n")
        else:
            lines.append(f"{ms},{fp},19.91,73.82,82.4,210.5\n")
        if create_jpegs:
            (frames_dir / f"{ms}.jpg").write_bytes(b"\xff\xd8\xff\xe0")  # minimal JPEG header

    (sd / "timestamps.csv").write_text("".join(lines))

    if overlay is not None:
        (sd / "pipeline_results.json").write_text(json.dumps(overlay))

    return sd


# ── tests ─────────────────────────────────────────────────────────────────────

def test_load_10k_rows_under_2s(tmp_path):
    sd = _make_session(tmp_path, n_rows=10_000)
    t0 = time.perf_counter()
    sess = ReplaySession(sd)
    elapsed = time.perf_counter() - t0
    assert elapsed < 2.0, f"Indexing 10k rows took {elapsed:.2f}s"
    assert sess.total_rows == 10_000


def test_random_seek_correct_record(tmp_path):
    sd = _make_session(tmp_path, n_rows=500)
    sess = ReplaySession(sd)
    for row in [0, 1, 249, 499]:
        rec = sess.get_frame(row)
        assert rec.row == row
        expected_ms = 1_717_490_000_000 + row * 200
        assert rec.unix_ms == expected_ms
        assert rec.jpeg_available is True


def test_missing_jpeg_still_returns_record(tmp_path):
    sd = _make_session(tmp_path, n_rows=5, create_jpegs=False)
    sess = ReplaySession(sd)
    rec = sess.get_frame(2)
    assert rec.jpeg_available is False
    assert rec.unix_ms == 1_717_490_000_000 + 2 * 200


def test_old_format_csv_extra_fields_null(tmp_path):
    sd = _make_session(tmp_path, n_rows=5, extra_cols=False)
    sess = ReplaySession(sd)
    rec = sess.get_frame(0)
    assert rec.hdop is None
    assert rec.satellite_count is None
    assert rec.disk_free_gb is None


def test_extended_csv_extra_fields_present(tmp_path):
    sd = _make_session(tmp_path, n_rows=5, extra_cols=True)
    sess = ReplaySession(sd)
    rec = sess.get_frame(0)
    assert rec.hdop == pytest.approx(1.2)
    assert rec.satellite_count == 8
    assert rec.disk_free_gb == pytest.approx(12.34)


def test_malformed_row_skipped_neighbors_accessible(tmp_path):
    sd = _make_session(tmp_path, n_rows=10, malformed_row=5)
    sess = ReplaySession(sd)
    # 9 valid rows (row 5 was malformed and skipped)
    assert sess.total_rows == 9
    # Rows around the gap are accessible
    rec = sess.get_frame(4)
    assert rec.unix_ms == 1_717_490_000_000 + 4 * 200


def test_find_next_low_inlier_no_overlay_returns_none(tmp_path):
    sd = _make_session(tmp_path, n_rows=10)
    sess = ReplaySession(sd)
    assert sess.find_next_low_inlier(0, "fwd") is None
    assert sess.find_next_low_inlier(5, "back") is None


def test_find_next_low_inlier_with_overlay(tmp_path):
    overlay = {
        "frames": [
            {"row": i, "timestamp_ms": 1_717_490_000_000 + i * 200,
             "inlier_count": 20 if i % 3 != 0 else 5, "reject_reason": None}
            for i in range(10)
        ]
    }
    sd = _make_session(tmp_path, n_rows=10, overlay=overlay)
    sess = ReplaySession(sd)

    # Low-inlier rows are 0, 3, 6, 9 (inlier_count=5)
    assert sess.find_next_low_inlier(0, "fwd") == 3   # next after 0
    assert sess.find_next_low_inlier(3, "fwd") == 6
    assert sess.find_next_low_inlier(6, "back") == 3
    assert sess.find_next_low_inlier(3, "back") == 0
    assert sess.find_next_low_inlier(9, "fwd") is None


def test_seek_by_ts_constant_time(tmp_path):
    sd = _make_session(tmp_path, n_rows=1000)
    sess = ReplaySession(sd)
    exact_ms = 1_717_490_000_000 + 400 * 200
    assert sess.seek_by_ts(exact_ms) == 400
    # Slightly off — still resolves to nearest
    assert sess.seek_by_ts(exact_ms + 50) == 400


def test_list_sessions(tmp_path, monkeypatch):
    # Create two session directories under a root
    root = tmp_path / "sessions_root"
    root.mkdir()
    for name in ("session_a", "session_b"):
        d = root / name
        d.mkdir()
        (d / "timestamps.csv").write_text("1000,frames/1000.jpg,19.91,73.82,82.4,210.5\n")

    monkeypatch.setenv("SESSIONS_ROOT", str(root))
    result = list_sessions()
    names = [e["session_id"] for e in result]
    assert "session_a" in names
    assert "session_b" in names
