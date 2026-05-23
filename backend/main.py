from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from routers import session, pipeline, logger

app = FastAPI(title="DRISHTI GCS API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(logger.router, prefix="/logger", tags=["logger"])
app.include_router(session.router, prefix="/session", tags=["session"])
app.include_router(pipeline.router, prefix="/pipeline", tags=["pipeline"])


@app.get("/health")
def health():
    return {"status": "ok"}
