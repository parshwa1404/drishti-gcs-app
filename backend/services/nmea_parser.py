"""
Parse $GPRMC, $GPGGA, $HCHDG NMEA sentences from a .nmea file.
Returns list of dicts: {timestamp_ms, lat, lon, hdop, heading_deg}
"""

from datetime import datetime, timezone


def _checksum_valid(sentence: str) -> bool:
    if '*' not in sentence:
        return True
    body, chk = sentence.rsplit('*', 1)
    body = body.lstrip('$')
    expected = 0
    for c in body:
        expected ^= ord(c)
    try:
        return expected == int(chk[:2], 16)
    except ValueError:
        return False


def _lat_decimal(raw: str, hemi: str) -> float:
    deg = float(raw[:2])
    mins = float(raw[2:])
    dd = deg + mins / 60.0
    return -dd if hemi == 'S' else dd


def _lon_decimal(raw: str, hemi: str) -> float:
    deg = float(raw[:3])
    mins = float(raw[3:])
    dd = deg + mins / 60.0
    return -dd if hemi == 'W' else dd


def parse_nmea_file(path: str) -> list[dict]:
    """
    Parse an NMEA log file.
    Sentence ordering assumed: $GPRMC creates a fix, $GPGGA and $HCHDG
    annotate the most recent fix (standard per-epoch ordering).
    """
    fixes: list[dict] = []

    with open(path, 'r', errors='replace') as f:
        for line in f:
            line = line.strip()
            if not line.startswith('$'):
                continue
            if not _checksum_valid(line):
                continue
            parts = line.split(',')
            stype = parts[0]

            try:
                if stype == '$GPRMC' and len(parts) >= 10:
                    if parts[2] != 'A':
                        continue
                    time_str = parts[1][:6]   # HHMMSS
                    date_str = parts[9]       # DDMMYY
                    lat = _lat_decimal(parts[3], parts[4])
                    lon = _lon_decimal(parts[5], parts[6])
                    dt = datetime.strptime(date_str + time_str, '%d%m%y%H%M%S')
                    dt = dt.replace(tzinfo=timezone.utc)
                    ts_ms = int(dt.timestamp() * 1000)
                    fixes.append({
                        'timestamp_ms': ts_ms,
                        'lat': lat,
                        'lon': lon,
                        'hdop': None,
                        'heading_deg': None,
                    })

                elif stype == '$GPGGA' and len(parts) >= 10:
                    if not parts[6] or int(parts[6]) == 0:
                        continue
                    lat = _lat_decimal(parts[2], parts[3])
                    lon = _lon_decimal(parts[4], parts[5])
                    hdop = float(parts[8]) if parts[8] else None
                    if fixes:
                        fixes[-1]['lat'] = lat
                        fixes[-1]['lon'] = lon
                        fixes[-1]['hdop'] = hdop

                elif stype in ('$HCHDG', '$HCHDM', '$HEHDG') and len(parts) >= 2:
                    if parts[1]:
                        if fixes:
                            fixes[-1]['heading_deg'] = float(parts[1])

            except (ValueError, IndexError):
                continue

    return fixes
