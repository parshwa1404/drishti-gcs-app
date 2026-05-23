from fastapi import APIRouter

router = APIRouter()


@router.post("/load")
async def load_session():
    return {"status": "not_implemented"}


@router.get("/live")
async def live():
    return {"status": "not_implemented"}


@router.get("/frame/{timestamp_ms}")
async def get_frame(timestamp_ms: int):
    return {"status": "not_implemented"}
