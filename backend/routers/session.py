import asyncio
import json
import time
import random
from fastapi import APIRouter
from sse_starlette.sse import EventSourceResponse
from routers.logger import _state as logger_state

router = APIRouter()

_DEOLALI_LAT = 19.9175
_DEOLALI_LON = 73.8278


@router.get("/live")
async def live():
    """SSE stream of GPS fixes from the active logger mock."""
    async def event_generator():
        while True:
            payload = {
                "lat": round(logger_state["lat"], 7),
                "lon": round(logger_state["lon"], 7),
                "hdop": round(random.uniform(0.6, 1.8), 2),
                "timestamp_ms": int(time.time() * 1000),
            }
            yield {"data": json.dumps(payload)}
            await asyncio.sleep(1.0)

    return EventSourceResponse(event_generator())


@router.post("/load")
async def load_session():
    return {"status": "not_implemented"}


@router.get("/frame/{timestamp_ms}")
async def get_frame(timestamp_ms: int):
    return {"status": "not_implemented"}
