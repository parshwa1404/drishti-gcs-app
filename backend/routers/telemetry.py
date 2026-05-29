import asyncio
import json
import time

from fastapi import APIRouter
from sse_starlette.sse import EventSourceResponse

router = APIRouter()


@router.get("/status")
async def telemetry_status():
    """SSE stub — returns not-connected until MAVLink bridge (Phase C) is wired."""
    async def stream():
        while True:
            yield {"data": json.dumps({
                "connected": False,
                "timestamp_ms": int(time.time() * 1000),
            })}
            await asyncio.sleep(2.0)
    return EventSourceResponse(stream())
