# DRISHTI-NAV Ground Control Station

A standalone field GCS web app for the DRISHTI-NAV UAV visual navigation system. Runs entirely on a laptop connected to the same WiFi as the RPi; no cloud dependency. Provides pre-flight checks, live telemetry monitoring, post-flight replay, and pipeline benchmark review in a single browser tab.

**Stack:** FastAPI (Python 3.10+) · React 18 · Vite · Tailwind CSS · Leaflet.js · Recharts · SSE (Server-Sent Events)

---

## Panels

**Panel 1 — Logging Control**
Connect to the RPi over a real SSH connection (host / user / key path), then start and stop a named recording session. On Start the backend opens a persistent SSH channel and tails the active session's `timestamps.csv` with `tail -F`, parsing each per-frame record (`unix_ms, frame_path, lat, lon, altitude_m, heading_deg`) and pushing it to the 1 Hz SSE status stream. The status section shows frame count, a rotating compass rose, and a per-frame strip with **altitude (m)**, latitude, longitude, and last-frame time, plus a **connection-status badge** (`connected` / `reconnecting` / `waiting for logger` / `error`). The SSH link reconnects with exponential backoff (1→2→4 … capped) on drop, and reports `waiting for logger` until the CSV appears. HDOP / disk / satellite tiles are sourced from the mock status walk and show `—` on the real path (those fields are not in `timestamps.csv`).

**Panel 2 — Live Map**
Streams GPS fixes from the RPi via SSE and plots a rolling track (500-point ring buffer) on an Esri World Imagery satellite tile layer (zoom 19). Each fix is drawn as a colour-coded CircleMarker matching the current HDOP. The map auto-pans to the latest position; the first fix triggers a `flyTo` at zoom 16. A connection-status pill and a live overlay card (HDOP, fix count, heading) sit over the map.

**Panel 3 — Post-flight Replay**
Load any session directory (or the auto-generated mock) to replay 100 JPEG frames in sync with the GPS track. A timeline scrubber and Play/Pause button step through frames at 100 ms/frame; arrow keys also navigate. A GPS track polyline covers the full flight; a blue CircleMarker marks the current position. After load, a **Quick Verify** card appears automatically showing a large GOOD (green) or RE-FLY (red) verdict badge, plus six stat rows — frame count, duration, GPS track points, HDOP median, recording gaps > 1 s, and danger-zone frames (210°–240° heading). RE-FLY reasons are bulleted. Dismiss collapses the card.

**Panel 4 — Algorithm Overlay**
Loads pipeline results and lets you scrub through frames with a 40-entry LRU image cache and AbortController-based fetch cancellation. The 3-column layout shows the live camera frame (left), a metrics card (centre), and the FAISS-matched map tile (right). The metrics card shows retrieval rank, inlier count, position error, camera GSD, pre-filter status, confidence score (colour-coded green/amber/red), and a **Solver** section with Embed / FAISS / LightGlue / Total ms and a "Last fix" timer (green < 2 s, amber < 5 s, red ≥ 5 s). A dual-track Leaflet map below shows GPS truth (blue) vs pipeline estimate (red) with a dashed grey error vector.

**Panel 5 — Benchmark Summary**
Displays Cut A (all non-rejected frames) and Cut B (inliers ≥ gate) statistics tables side by side with colour-coded diffs against the IIT-B baseline (median 32.5 m, p75 48.2 m, p90 67.8 m). Below, a 3×2 chart grid shows: Position Error Distribution (Cut A grey / Cut B blue bars), Inlier Count Distribution (amber below gate, blue above), Heading vs Inlier scatter with 210°–240° danger-zone shading, Frame Confidence Distribution (amber→teal gradient), **Solver Time histogram** (5 bins: 0–50 / 50–100 / 100–200 / 200–500 / 500+ ms), and **Fix Gap histogram** (5 bins colour-coded green/green/amber/red/red). An Export PNG button captures the full panel via html2canvas.

**Panel 6 — Pre-flight Check**
A live GO / CAUTION / NO-GO dashboard driven by the `/preflight/status` SSE stream. The backend `PreflightEvaluator` consumes the same per-frame records the Panel 1 SSH tail emits (no CSV re-parsing) and grades six **mandatory** checks — `rpi_connection`, `logger_active`, `frame_rate`, `gps_fix`, `altitude_sane`, `heading_present` — each `pass` / `warn` / `fail`. The overall badge is the worst mandatory state: **GO** (all pass), **CAUTION** (any warn, no fail), **NO-GO** (any fail). Six **stubbed** checks (`gps_hdop`, `satellite_count`, `disk_free`, `camera_exposure`, `fc_link`, `tile_db_loaded`) render a grey "data unavailable" tile with hover text naming the awaited field; they never block GO. A bottom strip shows stream connection, last-update time, and a data-lag readout. Thresholds live in `backend/config/preflight.yaml`.

**Panel 7 — Live Feed**
Polls `GET /telemetry/status` at 2 Hz via SSE and shows the last-known UAV state. A top status bar displays a large CONNECTED (green) / WEAK SIGNAL (amber) / LOST (red) pill, a session timer, and a reconnect countdown when WiFi is lost. The left column shows altitude and groundspeed with 60-point SVG sparklines, a rotating compass rose, and a colour-coded battery bar. The right column shows frames captured, disk free, GPS HDOP, satellite count, and session duration. A mini Leaflet map auto-pans to the latest GPS fix; it goes greyscale with a "Last known position" overlay when the connection drops.

---

## Setup

### Backend

```bash
cd backend
uv sync
cp .env.example .env       # set DRISHTI_NAV_PATH to your drishti-nav-v3 clone
uv run uvicorn main:app --reload
```

API runs at **http://localhost:8000**. Docs at http://localhost:8000/docs.

### Frontend

```bash
source ~/.nvm/nvm.sh       # WSL: nvm must be sourced per shell
cd frontend
npm install
npm run dev
```

App runs at **http://localhost:5173**.

### RPi connection (Panel 1)

Panel 1 tails the live RPi logger over SSH. Connection defaults live in `backend/config/rpi.yaml`:

```yaml
hostname: 192.168.1.100
username: pi
key_path: ~/.ssh/drishti_rpi
session_dir: ~/drishti_sessions      # base dir; active session is session_dir/<session_name>
reconnect_max_backoff_s: 30
```

Precedence is: **values entered in the Panel 1 UI** → **environment variables** → **`rpi.yaml`**. The env overrides let a field laptop point at a fresh RPi without editing files:

```bash
export DRISHTI_RPI_HOST=192.168.4.20
export DRISHTI_RPI_USER=pi
export DRISHTI_RPI_KEY_PATH=~/.ssh/drishti_rpi
export DRISHTI_RPI_SESSION_DIR=~/drishti_sessions
```

**Pointing the GCS at a fresh RPi:**

1. Generate a key pair if you don't have one: `ssh-keygen -t ed25519 -f ~/.ssh/drishti_rpi -N ""`.
2. Install the public key on the RPi: `ssh-copy-id -i ~/.ssh/drishti_rpi.pub pi@<rpi-ip>` (the RPi must allow key auth).
3. Set `hostname`/`key_path` in `rpi.yaml` (or the env vars above), or just type the host/user/key path into the Panel 1 form.
4. Connect → Start. The panel shows `waiting for logger` until `record_session.py` on the RPi begins writing `timestamps.csv`, then streams per-frame altitude/heading/position.

Key contents and resolved key paths are never logged.

### Pre-flight thresholds (Panel 6)

Panel 6 grades the live `/logger/status` record stream against `backend/config/preflight.yaml`:

```yaml
preflight:
  altitude_min_m: 50.0        # cruise ~80 m AGL for Deolali; 50–200 gives margin
  altitude_max_m: 200.0
  frame_rate_window_s: 10.0   # rolling window for the Hz average
  frame_rate_pass_hz: 5.0     # ≥ pass, [warn, pass) warns, < warn fails
  frame_rate_warn_hz: 3.0
  frame_max_age_pass_s: 5.0   # last-frame age: ≤ pass, ≤ warn warns, else fails
  frame_max_age_warn_s: 15.0
  heading_stuck_count: 10     # N identical consecutive headings → warn (frozen mag)
```

Edit these before a flight to match the mission profile, then restart the backend.

**Checks stubbed pending a drishti-rpi-logger CSV update** (rendered as grey "data unavailable" tiles, do not block GO): `gps_hdop`, `satellite_count`, `disk_free` (await new CSV columns), `camera_exposure` (no field defined), `fc_link` (Phase C MAVLink), `tile_db_loaded` (no endpoint yet).

### Tests

```bash
cd backend
uv run pytest tests/ -v          # backend (pytest)

cd ../frontend
npm run test                     # frontend (Vitest)
```

---

## Field Workflow

1. Power on RPi, Arducam B0385 (USB), GEPRC M1025I GPS (USB-UART), Pixhawk (USB MAVLink).
2. Open GCS app → **Panel 6** pre-flight check → confirm GO verdict.
3. **Panel 1** → enter RPi IP → Connect → set session name → Start Recording.
4. **Panel 7** → monitor live telemetry (altitude ramp, GPS quality, battery); WiFi will drop after takeoff — the panel switches to "LOST" and shows last known state.
5. UAV flies the preloaded Mission Planner mission autonomously and returns to home.
6. Reconnect WiFi → **Panel 1** → Stop Recording.
7. **Panel 3** → load session directory → Quick Verify card: GOOD means proceed; RE-FLY means check the listed reasons before flying again.
8. **Panel 4** → enter session dir and tile index → Run Pipeline (requires `DRISHTI_NAV_PATH` set) → scrub through frames to review algorithm overlay.
9. **Panel 5** → Load Results → review Cut A / Cut B benchmark against IIT-B baseline; Export PNG for the report.

---

## Mocked Components

The following are not yet wired to real hardware and run on mock data:

| Component | Status | Notes |
|---|---|---|
| Panel 1 SSH connect/start/stop | **Real** | Persistent SSH + `tail -F` of `timestamps.csv`; HDOP/disk/sats tiles still mock (not in the CSV) |
| Panel 1 idle status walk | Mock | When no session is recording, the status stream random-walks GPS so the Live Map keeps animating in dev |
| Panel 7 telemetry SSE | Mock animation | Real MAVLink telemetry bridge planned for Phase C |
| Pipeline subprocess | Graceful stub | Set `DRISHTI_NAV_PATH` in `backend/.env` to enable; otherwise Panel 4 shows an error message |
| All panels on startup | Mock data | Backend generates a 100-frame mock session and mock pipeline results at startup via `lifespan` |

---

## Next Planned Features

- **Trigger `record_session.py` over SSH** — Panel 1 now tails a running logger; remote start/stop of the logger process itself is the next step
- **Session comparison view** — two Panel 5 benchmark summaries side by side for before/after tuning
- **Tile DB inspector** — click map tile in Panel 4 to view FAISS coverage and retrieval candidates
- **MAVLink telemetry bridge** — replace mock Panel 7 SSE with live Pixhawk telemetry via pymavlink (Phase C)

---

## Project Structure

```
drishti-gcs-app/
├── backend/
│   ├── config/rpi.yaml        # Panel 1 SSH connection defaults
│   ├── drishti/perception/solver_timer.py   # SolverTiming dataclass
│   ├── config/
│   │   ├── rpi.yaml           # Panel 1 SSH connection defaults
│   │   └── preflight.yaml     # Panel 6 GO/NO-GO thresholds
│   ├── routers/
│   │   ├── logger.py      # Panel 1: real SSH tail + status SSE + record fan-out
│   │   ├── preflight.py   # Panel 6: /preflight/status GO/NO-GO SSE
│   │   ├── session.py     # Panel 3: load session, serve frames, verify
│   │   ├── pipeline.py    # Panel 4 + 5: run pipeline, frame-pair, benchmark
│   │   └── telemetry.py   # Panel 7: live telemetry SSE
│   ├── services/
│   │   ├── ssh_client.py      # RpiSshClient: persistent SSH tail -F + reconnect
│   │   ├── mock_ssh.py        # FakeSSHClient for tests
│   │   ├── timestamps_csv.py  # per-frame timestamps.csv parser (shared)
│   │   ├── preflight.py       # PreflightEvaluator: rolling checks + verdict
│   │   ├── nmea_parser.py     # $GPRMC/$GPGGA/$HCHDG parser
│   │   └── session_loader.py  # frame↔GPS sync (1500 ms); merges CSV altitude
│   ├── scripts/benchmark.py   # CLI benchmark report with solver timing
│   ├── tests/
│   │   ├── test_solver_timer.py
│   │   ├── test_ssh_client.py
│   │   ├── test_panel1_integration.py
│   │   ├── test_preflight.py
│   │   └── test_preflight_endpoint.py
│   └── main.py
└── frontend/src/components/
    ├── LoggingPanel.jsx          # + LoggingPanel.test.jsx (Vitest)
    ├── LiveMapPanel.jsx
    ├── ReplayPanel.jsx
    ├── AlgorithmPanel.jsx
    ├── BenchmarkPanel.jsx
    ├── ChecklistPanel.jsx
    └── LiveFeedPanel.jsx
```

## Environment

Copy `backend/.env.example` to `backend/.env` and set:

```
DRISHTI_NAV_PATH=~/projects/drishti-nav-v3
TILE_INDEX_PATH=~/datasets/faiss_index/deolali_z19
```
