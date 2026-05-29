#!/usr/bin/env python3
"""
DRISHTI ROS2 bag extractor — runs on the remote Jetson/RPi.

Reads a ROS2 SQLite3 bag (.db3) and writes DRISHTI session format:
  <output_dir>/frames/<timestamp_ms>.jpg
  <output_dir>/gps.nmea
  <output_dir>/timestamps.csv

Topics consumed:
  /camera/color/image_raw   sensor_msgs/msg/Image
  /mavros/global_position/raw/fix  sensor_msgs/msg/NavSatFix

Usage: python3 extract_ros2_bag.py <db3_path> <output_dir>
"""
import sys
import struct
import sqlite3
from pathlib import Path
from datetime import datetime, timezone


# ─── CDR reader ──────────────────────────────────────────────────────────────

class CDRReader:
    """Minimal CDR deserialiser (always little-endian — Jetson ARM64 default)."""

    def __init__(self, data: bytes):
        self.data = data
        self.pos = 4  # skip 4-byte encapsulation header

    def _align(self, n):
        cdr_pos = self.pos - 4
        rem = cdr_pos % n
        if rem:
            self.pos += n - rem

    def read_int8(self):
        v = struct.unpack_from('<b', self.data, self.pos)[0]
        self.pos += 1
        return v

    def read_uint8(self):
        v = self.data[self.pos]
        self.pos += 1
        return v

    def read_uint16(self):
        self._align(2)
        v = struct.unpack_from('<H', self.data, self.pos)[0]
        self.pos += 2
        return v

    def read_int32(self):
        self._align(4)
        v = struct.unpack_from('<i', self.data, self.pos)[0]
        self.pos += 4
        return v

    def read_uint32(self):
        self._align(4)
        v = struct.unpack_from('<I', self.data, self.pos)[0]
        self.pos += 4
        return v

    def read_float64(self):
        self._align(8)
        v = struct.unpack_from('<d', self.data, self.pos)[0]
        self.pos += 8
        return v

    def read_string(self):
        length = self.read_uint32()
        s = self.data[self.pos:self.pos + length]
        self.pos += length
        return s.rstrip(b'\x00').decode('utf-8', errors='replace')

    def read_byte_array(self):
        length = self.read_uint32()
        b = self.data[self.pos:self.pos + length]
        self.pos += length
        return b


# ─── Message parsers ─────────────────────────────────────────────────────────

def parse_image(raw: bytes):
    r = CDRReader(raw)
    sec      = r.read_int32()
    nanosec  = r.read_uint32()
    _frame_id = r.read_string()
    height   = r.read_uint32()
    width    = r.read_uint32()
    encoding = r.read_string()
    _is_be   = r.read_uint8()
    step     = r.read_uint32()
    pixels   = r.read_byte_array()
    ts_ms    = sec * 1000 + nanosec // 1_000_000
    return ts_ms, height, width, encoding, step, pixels


def parse_navsat(raw: bytes):
    r = CDRReader(raw)
    sec      = r.read_int32()
    nanosec  = r.read_uint32()
    _frame_id = r.read_string()
    _status  = r.read_int8()
    _service = r.read_uint16()
    lat      = r.read_float64()
    lon      = r.read_float64()
    alt      = r.read_float64()
    ts_ms    = sec * 1000 + nanosec // 1_000_000
    return ts_ms, lat, lon, alt


# ─── NMEA helpers ────────────────────────────────────────────────────────────

def _nmea_cs(body: str) -> str:
    chk = 0
    for c in body:
        chk ^= ord(c)
    return f"{chk:02X}"


def to_gprmc(ts_ms: int, lat: float, lon: float) -> str:
    dt = datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc)
    t, d = dt.strftime('%H%M%S'), dt.strftime('%d%m%y')
    la_d, lo_d = int(abs(lat)), int(abs(lon))
    la_m = (abs(lat) - la_d) * 60
    lo_m = (abs(lon) - lo_d) * 60
    la_h = 'N' if lat >= 0 else 'S'
    lo_h = 'E' if lon >= 0 else 'W'
    body = (f"GPRMC,{t}.000,A,{la_d:02d}{la_m:09.6f},{la_h},"
            f"{lo_d:03d}{lo_m:09.6f},{lo_h},0.0,0.0,{d},,")
    return f"${body}*{_nmea_cs(body)}"


def to_gpgga(ts_ms: int, lat: float, lon: float, alt: float) -> str:
    dt = datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc)
    t = dt.strftime('%H%M%S')
    la_d, lo_d = int(abs(lat)), int(abs(lon))
    la_m = (abs(lat) - la_d) * 60
    lo_m = (abs(lon) - lo_d) * 60
    la_h = 'N' if lat >= 0 else 'S'
    lo_h = 'E' if lon >= 0 else 'W'
    body = (f"GPGGA,{t}.000,{la_d:02d}{la_m:09.6f},{la_h},"
            f"{lo_d:03d}{lo_m:09.6f},{lo_h},1,10,1.0,{alt:.1f},M,,,,0000")
    return f"${body}*{_nmea_cs(body)}"


# ─── Extraction ───────────────────────────────────────────────────────────────

PLACEHOLDER_JPEG = bytes([
    0xFF,0xD8,0xFF,0xE0,0x00,0x10,0x4A,0x46,0x49,0x46,0x00,0x01,0x01,0x00,
    0x00,0x01,0x00,0x01,0x00,0x00,0xFF,0xDB,0x00,0x43,0x00,0x08,0x06,0x06,
    0x07,0x06,0x05,0x08,0x07,0x07,0x07,0x09,0x09,0x08,0x0A,0x0C,0x14,0x0D,
    0x0C,0x0B,0x0B,0x0C,0x19,0x12,0x13,0x0F,0x14,0x1D,0x1A,0x1F,0x1E,0x1D,
    0x1A,0x1C,0x1C,0x20,0x24,0x2E,0x27,0x20,0x22,0x2C,0x23,0x1C,0x1C,0x28,
    0x37,0x29,0x2C,0x30,0x31,0x34,0x34,0x34,0x1F,0x27,0x39,0x3D,0x38,0x32,
    0x3C,0x2E,0x33,0x34,0x32,0xFF,0xC0,0x00,0x0B,0x08,0x00,0x01,0x00,0x01,
    0x01,0x01,0x11,0x00,0xFF,0xC4,0x00,0x1F,0x00,0x00,0x01,0x05,0x01,0x01,
    0x01,0x01,0x01,0x01,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x01,0x02,
    0x03,0x04,0x05,0x06,0x07,0x08,0x09,0x0A,0x0B,0xFF,0xC4,0x00,0x35,0x10,
    0x00,0x02,0x01,0x03,0x03,0x02,0x04,0x03,0x05,0x05,0x04,0x04,0x00,0x00,
    0x01,0x7D,0x01,0x02,0x03,0x00,0x04,0x11,0x05,0x12,0x21,0x31,0x41,0xFF,
    0xDA,0x00,0x08,0x01,0x01,0x00,0x00,0x3F,0x00,0xF5,0x00,0xFF,0xD9,
])


def extract_bag(db3_path: str, output_dir: str) -> None:
    out = Path(output_dir)
    frames_dir = out / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db3_path)
    cur = conn.cursor()

    cur.execute("SELECT id, name FROM topics")
    topics = {name: tid for tid, name in cur.fetchall()}

    img_id = topics.get('/camera/color/image_raw')
    gps_id = topics.get('/mavros/global_position/raw/fix')

    if not img_id:
        print("ERROR: /camera/color/image_raw not found in bag", file=sys.stderr)
        sys.exit(1)

    try:
        import numpy as np
        from PIL import Image as PILImage
        has_pil = True
    except ImportError:
        has_pil = False
        print("WARNING: PIL/numpy not available — writing placeholder JPEGs", file=sys.stderr)

    # GPS
    gps_fixes, nmea_lines = [], []
    if gps_id:
        cur.execute(
            "SELECT timestamp, data FROM messages WHERE topic_id = ? ORDER BY timestamp",
            (gps_id,)
        )
        for _, data in cur.fetchall():
            try:
                ts_ms, lat, lon, alt = parse_navsat(bytes(data))
                gps_fixes.append((ts_ms, lat, lon, alt))
                nmea_lines.append(to_gprmc(ts_ms, lat, lon))
                nmea_lines.append(to_gpgga(ts_ms, lat, lon, alt))
            except Exception as e:
                print(f"GPS parse error: {e}", file=sys.stderr)

    def nearest_gps(ts_ms):
        if not gps_fixes:
            return None
        best = min(gps_fixes, key=lambda x: abs(x[0] - ts_ms))
        return best if abs(best[0] - ts_ms) <= 5000 else None

    # Images
    ts_rows = ["unix_ms,frame_path,lat,lon,altitude_m,heading_deg"]
    frame_count = 0

    cur.execute(
        "SELECT timestamp, data FROM messages WHERE topic_id = ? ORDER BY timestamp",
        (img_id,)
    )
    for _, data in cur.fetchall():
        try:
            ts_ms, height, width, encoding, _step, pixels = parse_image(bytes(data))
            fpath = frames_dir / f"{ts_ms}.jpg"

            if has_pil:
                if encoding in ('bgr8', 'rgb8'):
                    arr = np.frombuffer(pixels, dtype=np.uint8).reshape((height, width, 3))
                    if encoding == 'bgr8':
                        arr = arr[:, :, ::-1]
                    PILImage.fromarray(arr, 'RGB').save(str(fpath), format='JPEG', quality=80)
                elif encoding == 'mono8':
                    arr = np.frombuffer(pixels, dtype=np.uint8).reshape((height, width))
                    PILImage.fromarray(arr, 'L').save(str(fpath), format='JPEG', quality=80)
                else:
                    print(f"Unsupported encoding {encoding!r}, skipping", file=sys.stderr)
                    continue
            else:
                fpath.write_bytes(PLACEHOLDER_JPEG)

            gps = nearest_gps(ts_ms)
            if gps:
                _, lat, lon, alt = gps
                ts_rows.append(f"{ts_ms},frames/{ts_ms}.jpg,{lat:.6f},{lon:.6f},{alt:.1f},")
            else:
                ts_rows.append(f"{ts_ms},frames/{ts_ms}.jpg,,,,")

            frame_count += 1
            if frame_count % 50 == 0:
                print(f"  {frame_count} frames...", file=sys.stderr, flush=True)

        except Exception as e:
            print(f"Frame error at frame {frame_count}: {e}", file=sys.stderr)

    conn.close()

    (out / "timestamps.csv").write_text('\n'.join(ts_rows) + '\n')
    if nmea_lines:
        (out / "gps.nmea").write_text('\n'.join(nmea_lines) + '\n')

    print(
        f"Extracted {frame_count} frames, {len(gps_fixes)} GPS fixes → {output_dir}",
        flush=True
    )


if __name__ == '__main__':
    if len(sys.argv) != 3:
        print("Usage: extract_ros2_bag.py <db3_path> <output_dir>", file=sys.stderr)
        sys.exit(1)
    extract_bag(sys.argv[1], sys.argv[2])
