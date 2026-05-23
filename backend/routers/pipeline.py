from fastapi import APIRouter

router = APIRouter()


@router.post("/run")
async def run_pipeline():
    return {"status": "not_implemented"}


@router.get("/results/{session_name}")
async def get_results(session_name: str):
    return {"status": "not_implemented"}


@router.get("/benchmark/{session_name}")
async def get_benchmark(session_name: str):
    return {"status": "not_implemented"}
