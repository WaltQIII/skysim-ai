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

from app.api.auth import router as auth_router
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

app.include_router(auth_router)
app.include_router(missions_router)
app.include_router(uploads_router)
app.include_router(billing_router)


@app.get("/")
def root_api_index():
    """Root API index providing service metadata, active guardrails, and documentation links."""
    return {
        "service": "SkySim AI Autonomous Trajectory & Pre-Visualization API",
        "version": "1.0.0",
        "status": "online",
        "frontend_dashboard_url": "http://localhost:3000",
        "interactive_docs_url": "http://localhost:8000/docs",
        "openapi_schema_url": "http://localhost:8000/openapi.json",
        "health_check_url": "http://localhost:8000/api/health",
        "endpoints": {
            "missions": "/api/missions",
            "trajectory_compute": "/api/trajectory/compute",
            "gcs_signed_uploads": "/api/uploads/signed-url",
            "auth_profile": "/api/auth/me",
            "stripe_checkout": "/api/billing/checkout-session",
            "florida_saas_tax": "/api/billing/calculate-tax",
        },
    }


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
