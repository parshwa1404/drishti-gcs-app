"""
Parse the per-frame ``timestamps.csv`` written by drishti-rpi-logger.

Column order (drishti-rpi-logger commit 635edeb):
    unix_ms, frame_path, lat, lon, altitude_m, heading_deg

A header row, blank lines, and malformed rows are all skipped by returning
``None`` from :func:`parse_timestamps_line`; callers decide whether to count
them as errors. This module is intentionally dependency-free (no paramiko) so
it can be shared by the live SSH tail and the offline session loader.
"""

_N_COLUMNS = 6


def parse_timestamps_line(line: str) -> dict | None:
    """
    Parse one ``timestamps.csv`` line into a structured record.

    Returns ``None`` for blank lines, the header row, or any line that does not
    have the six expected fields with numeric values.

    >>> parse_timestamps_line("1717490000123,frames/1717490000123.jpg,19.91,73.82,82.4,210.5")
    {'unix_ms': 1717490000123, 'frame_path': 'frames/1717490000123.jpg', 'lat': 19.91, 'lon': 73.82, 'altitude_m': 82.4, 'heading_deg': 210.5}
    >>> parse_timestamps_line("unix_ms,frame_path,lat,lon,altitude_m,heading_deg") is None
    True
    """
    line = line.strip()
    if not line:
        return None

    parts = line.split(",")
    if len(parts) < _N_COLUMNS:
        return None

    def _f(s: str):
        s = s.strip()
        return float(s) if s else None

    try:
        unix_ms = int(parts[0])
    except ValueError:
        return None

    return {
        "unix_ms":      unix_ms,
        "frame_path":   parts[1].strip(),
        "lat":          _f(parts[2]),
        "lon":          _f(parts[3]),
        "altitude_m":   _f(parts[4]),
        "heading_deg":  _f(parts[5]),
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
