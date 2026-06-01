# Post-flight analysis workflow

For June 4 Deolali sortie. Written for 8 PM in a hotel room on patchy wifi.

---

## Step 1 — Copy the session directory to your laptop

If you already downloaded it via the GCS panel ("Select Bag ▾"), skip to Step 2.

```bash
# SCP from RPi if not using the GCS panel
scp -r pi@10.0.0.5:~/bags/sortie_1 ~/datasets/deolali_phase_b/
```

The session directory must contain:
- `frames/` — JPEG files named `{unix_ms}.jpg`
- `timestamps.csv` — per-frame GPS log

---

## Step 2 — One command

```bash
cd ~/projects/drishti-gcs-app/backend

uv run python scripts/postflight_report.py \
  --session-dir ~/datasets/deolali_phase_b/sortie_1 \
  --tile-db    ~/projects/drishti-nav-v3/configs/milestone_1b/deolali_tiledb.yaml \
  --output-dir results/deolali_phase_b/sortie_1
```

If you already have pipeline results saved from the GCS panel:

```bash
uv run python scripts/postflight_report.py \
  --session-dir ~/datasets/deolali_phase_b/sortie_1 \
  --tile-db    ~/projects/drishti-nav-v3/configs/milestone_1b/deolali_tiledb.yaml \
  --output-dir results/deolali_phase_b/sortie_1 \
  --pipeline-json ~/datasets/deolali_phase_b/sortie_1/pipeline_results.json
```

Re-running is free. If `results/.../per_frame.json` is already there and newer than the
CSV, the pipeline is skipped automatically.

---

## Step 3 — Output files

```
results/deolali_phase_b/sortie_1/
  summary.txt      ← paste this into the PR / email to IIT-B
  per_frame.json   ← one JSON record per frame (for further analysis)
  comparison.json  ← comparison with IIT-B baseline (machine-readable)
```

Open `summary.txt`:

```bash
cat results/deolali_phase_b/sortie_1/summary.txt
```

---

## Common errors

### `FATAL: gsd_normalisation is OFF`
`embedder.gsd_normalisation.enabled` is `false` in the tile-DB config.
Set it to `true` — the entire run is suspect without it.

```yaml
# deolali_tiledb.yaml
embedder:
  gsd_normalisation:
    enabled: true   # ← must be true
```

### `timestamps.csv not found`
The session directory doesn't have a `timestamps.csv`. Either:
- The RPi logger crashed before writing it (check `dmesg` on RPi)
- You pointed `--session-dir` at the wrong path

### `No JPEG frames found`
There's no `frames/` subdirectory, or it's empty.
Check the ROS2 bag extraction: `ls ~/datasets/deolali_phase_b/sortie_1/frames/ | head`.

### `GPS-validity breakdown unavailable: required columns missing`
The logger is running an old version (pre-June 4) that doesn't emit
`hdop`, `satellite_count`, `disk_free_gb`. The analysis still runs;
GPS-quality breakdown is just skipped. Update drishti-rpi-logger after landing.

### `run_gcs_pipeline.py not found`
Set `DRISHTI_NAV_PATH` in `backend/.env`, or pass `--nav-path`:

```bash
export DRISHTI_NAV_PATH=~/projects/drishti-nav-v3
# or
uv run python scripts/postflight_report.py ... --nav-path ~/projects/drishti-nav-v3
```

---

## Comparing two sorties

Each sortie produces its own `comparison.json`. Load both and diff the `cut_b_cruise` block:

```python
import json
a = json.load(open("results/sortie_1/comparison.json"))
b = json.load(open("results/sortie_2/comparison.json"))
print(a["cut_b_cruise"], b["cut_b_cruise"])
```

The `comparison.json` schema is compatible with `phase4_cruise.json` from IIT-B:
`cut_b.median_error_m` and `cut_b.p90_error_m` are directly comparable.
