import asyncio
import json

from fastapi import APIRouter
from sse_starlette.sse import EventSourceResponse

import routers.logger as logger
from services.preflight import PreflightEvaluator, load_preflight_config

router = APIRouter()

_INTERVAL_S = 1.0

# Single evaluator subscribed to the Panel 1 record stream at import/startup so
# its rolling window is already populated whenever an operator opens Panel 6.
_evaluator = PreflightEvaluator(load_preflight_config())
logger.subscribe_records(_evaluator.observe)


def _payload() -> dict:
    return _evaluator.build_report(connection_status=logger.current_connection_status())


@router.get("/status")
async def status():
    async def event_generator():
        while True:
            yield {"data": json.dumps(_payload())}
            await asyncio.sleep(_INTERVAL_S)

    return EventSourceResponse(event_generator())
