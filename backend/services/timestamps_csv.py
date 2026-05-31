"""
Parse the per-frame ``timestamps.csv`` written by drishti-rpi-logger.

Column order (drishti-rpi-logger commit 635edeb):
    unix_ms, frame_path, lat, lon, altitude_m, heading_deg

A header row, blank lines, and malformed rows are all skipped by returning
``None`` from :func:`parse_timestamps_line`; callers decide whether to count
them as errors. This module is intentionally dependency-free (no paramiko) so
it can be shared by the live SSH tail and the offline session loader.
"""

_N_COLUMNS_MIN = 6

# Column indices for the optional extended format (added in rpi-logger after June 4)
_COL_HDOP          = 6
_COL_SAT_COUNT     = 7
_COL_DISK_FREE_GB  = 8


def parse_timestamps_line(line: str) -> dict | None:
    """
    Parse one ``timestamps.csv`` line into a structured record.

    Returns ``None`` for blank lines, the header row, or any line that does not
    have at least the six required fields with numeric values.

    Columns 7–9 (hdop, satellite_count, disk_free_gb) are optional; older logs
    that omit them will have those fields set to ``None``.

    >>> parse_timestamps_line("1717490000123,frames/1717490000123.jpg,19.91,73.82,82.4,210.5")
    {'unix_ms': 1717490000123, 'frame_path': 'frames/1717490000123.jpg', 'lat': 19.91, 'lon': 73.82, 'altitude_m': 82.4, 'heading_deg': 210.5, 'hdop': None, 'satellite_count': None, 'disk_free_gb': None}
    >>> parse_timestamps_line("unix_ms,frame_path,lat,lon,altitude_m,heading_deg") is None
    True
    """
    line = line.strip()
    if not line:
        return None

    parts = line.split(",")
    if len(parts) < _N_COLUMNS_MIN:
        return None

    def _f(s: str):
        s = s.strip()
        return float(s) if s else None

    def _opt_f(idx: int):
        if idx >= len(parts):
            return None
        return _f(parts[idx])

    def _opt_i(idx: int):
        if idx >= len(parts):
            return None
        s = parts[idx].strip()
        try:
            return int(s) if s else None
        except ValueError:
            return None

    try:
        unix_ms = int(parts[0])
    except ValueError:
        return None

    return {
        "unix_ms":          unix_ms,
        "frame_path":       parts[1].strip(),
        "lat":              _f(parts[2]),
        "lon":              _f(parts[3]),
        "altitude_m":       _f(parts[4]),
        "heading_deg":      _f(parts[5]),
        "hdop":             _opt_f(_COL_HDOP),
        "satellite_count":  _opt_i(_COL_SAT_COUNT),
        "disk_free_gb":     _opt_f(_COL_DISK_FREE_GB),
    }


def read_timestamps_csv(path: str) -> list[dict]:
    """Read a ``timestamps.csv`` file and return all valid per-frame records."""
    records: list[dict] = []
    with open(path, "r", errors="replace") as fh:
        for line in fh:
            rec = parse_timestamps_line(line)
            if rec is not None:
                records.append(rec)
    return records
