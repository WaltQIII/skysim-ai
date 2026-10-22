# SkySim AI — Autonomous Drone Flight Pre-Visualization & Trajectory Engine

SkySim AI is a spatial pre-visualization and trajectory-planning platform for commercial drone operators, real estate videographers, and aerial inspection teams. It transforms site imagery and property boundaries into 3D cubic B-spline flight paths, audits visual framing and obstacle clearance, and exports FAA Part 107 compliant mission files (DJI Waypoint 3.0 `.kmz`/`.kml`, PX4 QGroundControl `.plan`, and Three.js WebGL `.json`).

---

## Live Production Deployment (Google Cloud Run)

- **Live Frontend Application:** [https://skysim-ai-frontend-745110910494.us-east1.run.app](https://skysim-ai-frontend-745110910494.us-east1.run.app)
- **Live Backend API:** [https://skysim-ai-backend-745110910494.us-east1.run.app](https://skysim-ai-backend-745110910494.us-east1.run.app)
- **Live Interactive OpenAPI / Swagger Docs:** [https://skysim-ai-backend-745110910494.us-east1.run.app/docs](https://skysim-ai-backend-745110910494.us-east1.run.app/docs)
- **GitHub Repository:** [https://github.com/WaltQIII/skysim-ai](https://github.com/WaltQIII/skysim-ai)

---

## Architecture & Core Modules

- **`backend/` (Python 3.11+ / FastAPI / SciPy / NumPy)**:
  - [`backend/app/services/trajectory.py`](backend/app/services/trajectory.py): Cubic B-spline (`k=3`) 3D trajectory interpolation in local ENU & WGS84 coordinates, smooth gimbal pitch/yaw subject tracking, 3D obstacle collision buffer avoidance, and hard velocity clamps (`<= 15.0 m/s` horizontal, `<= 5.0 m/s` vertical/ascent).
  - [`backend/app/services/exporter.py`](backend/app/services/exporter.py): Multi-format mission compiler supporting DJI Waypoint 3.0 (`.kml` / `.kmz`), PX4 QGroundControl (`.plan`), and Three.js WebGL preview (`.json`), embedding the mandatory FAA Part 107 Remote Pilot in Command (RPIC) disclaimer in XML/ZIP/JSON metadata comments.
  - [`backend/app/api/uploads.py`](backend/app/api/uploads.py): Direct-to-Storage upload pipeline generating time-limited Google Cloud Storage V4 Signed `PUT` URLs (`generate_signed_upload_url`) so raw client imagery never buffers in backend memory.
  - [`backend/app/services/world_model.py`](backend/app/services/world_model.py): Local deterministic simulator and connector layer for Google Project Genie 3D spatial synthesis and Gemini Ultra multimodal framing & clearance auditing.
  - [`backend/app/services/quota_guard.py`](backend/app/services/quota_guard.py): Fail-closed GCP budget and quota circuit breaker monitoring `$50` (50%) and `$90` (90%) alert thresholds on the `$100` monthly credit.
  - [`backend/app/services/billing.py`](backend/app/services/billing.py): Florida B2B SaaS tax compliance module classifying subscriptions as cloud-hosted SaaS without tangible personal property (exempt under FL DOR rules).
- **`frontend/` (Next.js 14 App Router / TypeScript / Tailwind CSS)**:
  - [`frontend/src/app/page.tsx`](frontend/src/app/page.tsx): Interactive trajectory builder featuring the Controlled Airspace & FAA LAANC pre-flight verification banner/checklist, direct-to-GCS signed URL upload workflow, 3D orbital B-spline visualizer, Gemini Ultra scorecard, and blocking RPIC Part 107 liability acknowledgment modal prior to export.

---

## Running Locally

### 1. Backend (FastAPI)

```bash
cd backend
python3 -m venv --copies .venv
./.venv/bin/pip install -r requirements.txt

# Run test suite
PYTHONPATH=. ./.venv/bin/pytest tests -v

# Start FastAPI server on http://localhost:8000
PYTHONPATH=. ./.venv/bin/uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### 2. Frontend (Next.js 14)

```bash
cd frontend
npm install
npm run dev
```

Open `http://localhost:3000` in your browser.
