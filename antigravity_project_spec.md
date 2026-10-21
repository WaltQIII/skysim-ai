# PROJECT SPEC: SkySim AI (Autonomous Drone Flight Pre-Visualization & Trajectory Engine)

## 1. System Overview
SkySim AI is a specialized spatial pre-visualization and trajectory-planning platform for commercial drone operators, real estate videographers, and aerial inspection teams. It transforms site photos and satellite imagery into interactive 3D flight paths, validates framing and collision hazards, and exports production-ready waypoint missions (DJI Waypoint 3.0, KML, PX4).

## 2. Technical Stack & Architectural Constraints
* **Language & Runtime:** Python 3.11+ (FastAPI backend), Next.js / TypeScript (frontend), Tailwind CSS.
* **Core Intelligence:**
  * **World Model Pipeline:** Google Project Genie API / Spatial preview connectors.
  * **Spatial Validation & Scoring:** Gemini Ultra (multimodal visual framing, obstacle clearance audit).
  * **Trajectory Math:** Google Colab / SciPy & NumPy (B-spline interpolation, gimbal yaw/pitch smoothing, speed profiling).
* **Infrastructure (Google Cloud Platform):**
  * **Compute:** Cloud Run (containerized stateless backend).
  * **Database:** Firestore / Cloud SQL (PostgreSQL for user accounts, mission logs, waypoints).
  * **Storage:** Google Cloud Storage (signed URLs for uploading raw photos, rendering 3D assets).
* **Development Tooling:** Google Antigravity IDE & Antigravity CLI (Agent Mode).

---

## 3. Core MVP Modules & Data Flow

```
[User Uploads Site Photos/KML]
          │
          ▼
[GCS Direct Upload via Signed URL]
          │
          ▼
[Trajectory Generator Service] ──► [Project Genie 3D Spatial Pre-Viz]
          │                                  │
          ▼                                  ▼
[B-Spline Smoothing / Physics Engine] ◄── [Gemini Ultra Visual Framing Audit]
          │
          ▼
[Export Formats: DJI Waypoint 3.0 / KML / QGroundControl]
```

### Module A: Project Ingestion & GCS Storage
* **Endpoint:** `POST /api/missions/create`
* Generates time-limited GCS Signed URLs for client-side image uploads (minimizes backend bandwidth).
* Accepts property boundary coordinates (GeoJSON or standard lat/long bounding box).

### Module B: Trajectory Engine & Spline Interpolation
* Accepts key user waypoints (Start, Subject Focal Point, Orbit Radius, Altitude AGL, End).
* Computes smooth Bezier / B-Spline cubic curves ensuring drone velocity limits (max horizontal: 15 m/s, max ascent: 5 m/s) and camera gimbal pitch transitions are cinematographically fluid.
* Formats data into WGS84 coordinates with AGL (Above Ground Level) safety margins.

### Module C: Gemini Multimodal Framing Auditor
* Ingests trajectory frames / preview screenshots into Gemini Ultra.
* Evaluates framing compliance: rule-of-thirds alignment, solar glare angles, and safety buffer clearance from trees or rooflines.
* Returns a structured JSON validation scorecard:
  ```json
  {
    "safety_score": 94,
    "clearance_warnings": ["Waypoint 4 passes within 3.5m of northern tree canopy."],
    "cinematic_score": 88,
    "framing_tips": ["Pitch gimbal down 6 degrees between W3 and W5 to maintain property center."]
  }
  ```

### Module D: Exporter Service
* Compiles validated flight path into industry-standard mission files:
  * **DJI Waypoint 3.0 (`.kmz` / `.kml`):** Compatible with DJI Pilot 2 and DJI Fly.
  * **PX4 / QGroundControl (`.plan`):** Compatible with commercial and open-source drones.
  * **Mission Preview Data (`.json`):** For client-side Three.js / WebGL replay.

---

## 4. Database Schema (Firestore / PostgreSQL)

### `users`
* `id` (UUID / Google Auth UID)
* `email` (string)
* `plan` (`trial` | `pro_monthly` | `enterprise`)
* `created_at` (timestamp)

### `missions`
* `id` (UUID)
* `user_id` (UUID)
* `name` (string, e.g., "742 Evergreen Terrace Luxury Orbit")
* `status` (`draft` | `processing` | `ready` | `failed`)
* `bounding_box` (JSON / Polygon)
* `flight_metrics` (duration_seconds, total_distance_meters, max_altitude_agl)
* `export_urls` (GCS URLs for DJI .kmz, .kml, .plan)
* `audit_report` (JSON from Gemini Ultra)

---

## 5. Security, Disclaimers & Legal Compliance
* **Pilot-in-Command (PIC) Mandatory Disclaimer:** All exported files and flight plans must embed the legal notice:
  > *"SkySim AI outputs are assistive pre-visualization calculations only. The Remote Pilot in Command (RPIC) retains sole regulatory responsibility under FAA Part 107 for visual line-of-sight (VLOS), obstacle clearance, airspace authorizations (LAANC), and aircraft operation."*
* **GCP Security:** Service account credentials stored strictly in Google Secret Manager; zero plaintext API keys in client-side bundles.

---

## 6. Antigravity Agent Execution Directives
1. **Scaffold Structure:** Set up a clean monorepo with `frontend/` (Next.js 14 App Router) and `backend/` (FastAPI).
2. **Prioritize the Waypoint Math:** Implement `backend/app/services/trajectory.py` first with unit tests covering speed clamping and spline smoothing before styling UI components.
3. **Mock External World Models during Initial Testing:** Create a local mock simulator interface so the application can be tested end-to-end without burning external API quotas.
4. **Deploy Ready:** Include `Dockerfile` and `cloudbuild.yaml` configured for single-command deployment to Cloud Run.

---

## 7. Critical Items for Antigravity Implementation

Antigravity must implement and respect these five core guardrails throughout development:

### 1. Mandatory Pilot-in-Command (PIC) Liability Shielding
* **Implementation:** 
  * Add a blocking confirmation modal prior to any waypoint export (`.kmz`, `.kml`, `.plan`) that requires the user to acknowledge that SkySim AI is purely an assistive pre-visualization tool.
  * Embed the legal disclaimer directly as XML/JSON metadata comments inside every generated flight plan file.
  * Ensure user acceptance is logged with a timestamp in the `missions` database record.

### 2. GCP Hard Budget Cap & Alerting Architecture
* **Implementation:**
  * Configure environment variables and health check routes to gracefully fail closed if API quotas approach limits.
  * In the deployment setup documentation (`DEPLOY.md`), provide the exact `gcloud` CLI commands or Terraform snippet to set automated Google Cloud Billing alerts at **$50 (50%)** and **$90 (90%)** of the $100 monthly credit.
  * Set max Cloud Run instance scaling to `max-instances=3` to prevent runaway cold-start cost spikes.

### 3. Direct-to-Storage Upload Pipeline via GCS Signed URLs
* **Implementation:**
  * Do NOT route raw client photos or drone telemetry files (50 MB–200 MB+) through FastAPI backend request bodies.
  * Implement `backend/app/api/uploads.py` with `generate_signed_upload_url(bucket_name, blob_name, expiration=15)` using the Google Cloud Storage SDK.
  * The frontend must upload files directly to GCS via HTTP `PUT`, only pinging the backend once the upload finishes.

### 4. Controlled Airspace Awareness & LAANC Verification UI
* **Implementation:**
  * Add an airspace advisory banner within the trajectory builder UI.
  * Display a prominent pre-flight checklist item prompting the pilot to verify local airspace classification (Class B, C, D, surface E) and secure FAA LAANC clearance via [FAA DroneZone](https://www.google.com/search?q=https%3A%2F%2Ffaadronezone-access.faa.gov%2F) before field deployment.

### 5. Florida B2B SaaS Tax & Payment Compliance Setup
* **Implementation:**
  * In the payment integration module (Stripe / Lemon Squeezy integration), configure product metadata to classify SkySim AI subscriptions strictly as cloud-hosted SaaS (digital software access without tangible personal property).
  * Under Florida Department of Revenue rules, web-based SaaS access without downloaded desktop media is non-taxable, ensuring tax calculation webhooks do not erroneously bill or remit Florida sales tax.
