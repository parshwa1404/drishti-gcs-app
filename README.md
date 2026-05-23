# DRISHTI-NAV Ground Control Station

GCS web app for UAV data logging control, flight replay, and algorithm visualization.

## Stack
- **Backend** — FastAPI (Python 3.10+), uv
- **Frontend** — React 18 + Vite + Tailwind CSS
- **Maps** — Leaflet.js + Esri World Imagery tiles
- **Charts** — Recharts

## Development

### Backend
```bash
cd backend
uv sync
uv run uvicorn main:app --reload
```

### Frontend
```bash
cd frontend
npm install
npm run dev
```

App: http://localhost:5173  
API docs: http://localhost:8000/docs

## Panels

| Panel | Status | Description |
|---|---|---|
| Logging Control | ✅ | SSH control of RPi logger, live SSE status stream |
| Live Map | 🔜 | Real-time GPS track on Leaflet satellite map |
| Post-Flight Replay | 🔜 | Frame-by-frame playback with GPS map |
| Algorithm Overlay | 🔜 | Pipeline results: matched tiles, error metrics |
| Benchmark Summary | 🔜 | Cut A/B stats, error histogram, scatter chart |

## Environment
Copy `backend/.env.example` to `backend/.env` and set paths for the external tools:
```
DRISHTI_NAV_PATH=~/projects/drishti-nav-v3
TILE_INDEX_PATH=~/datasets/faiss_index/deolali_z19
```
