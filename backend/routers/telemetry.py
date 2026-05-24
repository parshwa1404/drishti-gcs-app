import asyncio
import json
import random
import time

from fastapi import APIRouter
from sse_starlette.sse import EventSourceResponse

router = APIRouter()

_DEOLALI_LAT = 19.9175
_DEOLALI_LON = 73.8278

# WiFi cycle lengths (in ticks at 2 Hz):
#   0–59 (30 s): strong + connected
#  60–69  (5 s): weak   + connected
#  70–89 (10 s): lost   + disconnected  → server polls every 5 s
# total cycle = 90 ticks = 45 s
_CYCLE      = 90
_WEAK_START = 60
_LOST_START = 70


@router.get("/status")
async def telemetry_status():
    async def stream():
        tick            = 0
        altitude_m      = 10.0
        groundspeed     = 8.0
        heading         = 47.3
        battery_pct     = 95.0
        frames_captured = 0
        disk_free_gb    = 11.2
        lat             = _DEOLALI_LAT
        lon             = _DEOLALI_LON
        gps_hdop        = 0.8
        gps_satellites  = 12
        session_start   = time.time()

        while True:
            phase = tick % _CYCLE

            if phase >= _LOST_START:
                # WiFi lost — emit minimal payload and poll slowly
                yield {"data": json.dumps({"connected": False})}
                await asyncio.sleep(5.0)
                tick += 1
                continue

            wifi_strength = "weak" if phase >= _WEAK_START else "strong"

            # Altitude: ramp to 84 m over first 40 ticks (20 s), then hold
            if tick < 40:
                altitude_m = 10.0 + 74.0 * (tick / 40.0)
            else:
                altitude_m += random.uniform(-0.5, 0.5)
                altitude_m = max(60.0, min(90.0, altitude_m))

            groundspeed = max(6.0, min(15.0, groundspeed + random.uniform(-0.5, 0.5)))
            heading     = (heading + random.uniform(-2.0, 2.0)) % 360.0
            battery_pct = max(0.0, battery_pct - random.uniform(0.05, 0.15))
            frames_captured += random.randint(4, 6)
            disk_free_gb = max(0.0, disk_free_gb - random.uniform(0.001, 0.003))
            lat += random.uniform(-0.00004, 0.00004)
            lon += random.uniform(-0.00004, 0.00004)
            gps_hdop       = round(random.uniform(0.6, 1.2), 2)
            gps_satellites = random.randint(10, 14)
            session_dur    = round(time.time() - session_start, 1)

            payload = {
                "connected":        True,
                "timestamp_ms":     int(time.time() * 1000),
                "altitude_m":       round(altitude_m, 1),
                "groundspeed_ms":   round(groundspeed, 1),
                "heading_deg":      round(heading, 1),
                "gps_hdop":         gps_hdop,
                "gps_satellites":   gps_satellites,
                "battery_pct":      round(battery_pct, 1),
                "frames_captured":  frames_captured,
                "disk_free_gb":     round(disk_free_gb, 2),
                "session_duration_s": session_dur,
                "wifi_strength":    wifi_strength,
                "lat":              round(lat, 7),
                "lon":              round(lon, 7),
            }
            yield {"data": json.dumps(payload)}
            await asyncio.sleep(0.5)   # 2 Hz
            tick += 1

    return EventSourceResponse(stream())
