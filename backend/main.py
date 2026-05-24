from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from routers import session, pipeline, logger, telemetry


@asynccontextmanager
async def lifespan(app: FastAPI):
    mock_dir = session.generate_mock_session()
    session.load_session_state(mock_dir)
    from pathlib import Path
    pipeline.generate_mock_results(Path(mock_dir).name)
    yield


app = FastAPI(title="DRISHTI GCS API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(logger.router,    prefix="/logger",    tags=["logger"])
app.include_router(session.router,   prefix="/session",   tags=["session"])
app.include_router(pipeline.router,  prefix="/pipeline",  tags=["pipeline"])
app.include_router(telemetry.router, prefix="/telemetry", tags=["telemetry"])


@app.get("/health")
def health():
    return {"status": "ok"}
