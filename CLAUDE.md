# DRISHTI-NAV GCS App — CLAUDE.md

Local path: `~/projects/drishti-gcs-app`
GitHub: https://github.com/parshwa1404/drishti-gcs-app

Stack: FastAPI (Python 3.10+, uv) + React 18 + Vite + Tailwind CSS + Leaflet + Recharts

## Dev commands

```bash
# Backend
cd backend && uv run uvicorn main:app --reload

# Frontend (source nvm first in WSL2)
source ~/.nvm/nvm.sh && cd frontend && npm run dev

# All backend tests
cd backend && uv run pytest

# Frontend tests + build
source ~/.nvm/nvm.sh && cd frontend && npm test && npm run build
```

## Post-flight harness shipped

One-command sortie analysis:
```bash
cd backend
uv run python scripts/postflight_report.py \
  --session-dir ~/datasets/deolali_phase_b/sortie_1 \
  --tile-db    ~/projects/drishti-nav-v3/configs/milestone_1b/deolali_tiledb.yaml \
  --output-dir results/deolali_phase_b/sortie_1
```

Produces: `summary.txt`, `per_frame.json`, `comparison.json`.
Full operator guide: `docs/postflight_workflow.md`.
Synthetic fixture for testing: `backend/tests/fixtures/synthetic_deolali_session/`.

## Panels (7 total)

1. Logging Control — SSH connect/start/stop (real Paramiko wiring)
2. Live Map
3. Post-Flight Replay — stream-based, lazy JPEG cache, timestamp-driven playback
4. Algorithm Overlay
5. Benchmark Summary
6. Preflight — GO/NO-GO badge
7. Live Feed — mock telemetry SSE

## Key env vars (backend/.env)

```
DRISHTI_NAV_PATH=~/projects/drishti-nav-v3
TILE_INDEX_PATH=~/datasets/faiss_index/deolali_z19
SESSIONS_ROOT=~/bags
```

## Constraints

- `uv` for backend deps, `npm` for frontend
- No localStorage — all state in memory (module-level Python dicts; React useState)
- Leaflet maps use Esri World Imagery z=19
- Backend tests: pytest; frontend tests: Vitest
