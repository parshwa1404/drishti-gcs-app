from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response

from services.replay import (
    ReplaySession,
    get_session,
    list_sessions,
    open_session,
)

router = APIRouter()


# ── Session management ────────────────────────────────────────────────────────

@router.get("/sessions")
def sessions():
    return list_sessions()


@router.post("/open/{session_id}")
def open_session_endpoint(session_id: str, path: str = Query(...)):
    try:
        sess = open_session(path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {
        "session_id": sess.session_id,
        "total_rows":  sess.total_rows,
        "has_overlay": sess.has_overlay(),
        "unix_ms_start": sess._unix_ms_list[0] if sess._unix_ms_list else None,
        "unix_ms_end":   sess._unix_ms_list[-1] if sess._unix_ms_list else None,
    }


# ── Frame access ──────────────────────────────────────────────────────────────

def _require_session(session_id: str) -> ReplaySession:
    sess = get_session(session_id)
    if sess is None:
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not open — POST /replay/open/{session_id}?path=… first")
    return sess


@router.get("/{session_id}/frame/{row}")
def get_frame(session_id: str, row: int):
    sess = _require_session(session_id)
    try:
        rec = sess.get_frame(row)
    except IndexError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {
        "row":             rec.row,
        "unix_ms":         rec.unix_ms,
        "frame_path":      rec.frame_path,
        "lat":             rec.lat,
        "lon":             rec.lon,
        "altitude_m":      rec.altitude_m,
        "heading_deg":     rec.heading_deg,
        "hdop":            rec.hdop,
        "satellite_count": rec.satellite_count,
        "disk_free_gb":    rec.disk_free_gb,
        "jpeg_available":  rec.jpeg_available,
        "overlay":         rec.overlay,
    }


@router.get("/{session_id}/jpeg/{row}")
def get_jpeg(session_id: str, row: int):
    sess = _require_session(session_id)
    try:
        p = sess.get_jpeg_path(row)
    except (IndexError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    if p is None:
        raise HTTPException(status_code=404, detail="JPEG not found on disk")
    return Response(content=p.read_bytes(), media_type="image/jpeg")


@router.get("/{session_id}/seek")
def seek(session_id: str, ts_ms: int = Query(...)):
    sess = _require_session(session_id)
    row = sess.seek_by_ts(ts_ms)
    return {"row": row}


@router.get("/{session_id}/next-low-inlier")
def next_low_inlier(
    session_id: str,
    from_row: int = Query(..., alias="from"),
    dir: str = Query("fwd"),
):
    if dir not in ("fwd", "back"):
        raise HTTPException(status_code=422, detail="dir must be 'fwd' or 'back'")
    sess = _require_session(session_id)
    row = sess.find_next_low_inlier(from_row, dir)
    return {"row": row}
