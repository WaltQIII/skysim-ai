"""
SkySim AI FastAPI Backend Entrypoint.

Includes:
- Trajectory Engine & B-Spline Math endpoints
- Direct-to-GCS Signed URL Upload Pipeline (`/api/uploads`)
- Mission Lifecycle & Exporter with Mandatory RPIC Part 107 Liability Shield (`/api/missions`)
- Florida B2B SaaS Tax Compliance Webhook (`/api/billing`)
- Fail-Closed GCP Budget & Quota Health Check (`/health` & `/api/health`)
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.billing import router as billing_router
from app.api.missions import router as missions_router
from app.api.uploads import router as uploads_router
from app.models.schemas import TrajectoryRequest, TrajectoryResult
from app.services.quota_guard import quota_guard
from app.services.trajectory import generate_trajectory

app = FastAPI(
    title="SkySim AI API",
    description="Autonomous Drone Flight Pre-Visualization & Cubic B-Spline Trajectory Engine",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(missions_router)
app.include_router(uploads_router)
app.include_router(billing_router)


@app.get("/health")
@app.get("/api/health")
def health_check():
    """
    Health check endpoint that monitors GCP API quotas and the $100 monthly budget cap.
    Fails closed (HTTP 503) if API quotas approach or exceed hard limits (Section 7, Guardrail #2).
    """
    status_report = quota_guard.evaluate_status()
    if not status_report["healthy"]:
        return JSONResponse(status_code=503, content=status_report)
    return status_report


@app.post("/api/trajectory/compute", response_model=TrajectoryResult, tags=["trajectory"])
def compute_trajectory_endpoint(req: TrajectoryRequest) -> TrajectoryResult:
    """Directly compute a 3D cubic B-spline trajectory and gimbal profile."""
    quota_guard.enforce_quota_or_fail_closed()
    return generate_trajectory(req)
