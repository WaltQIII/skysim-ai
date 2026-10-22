"""
Missions API Router for SkySim AI.

Implements:
- `POST /api/missions/create` (Module A: generates GCS Signed URLs for direct client uploads,
  runs Project Genie 3D synthesis, computes Cubic B-Spline trajectory, and runs Gemini Ultra
  framing audit).
- `POST /api/missions/{mission_id}/acknowledge-pic` (Guardrail #1: logs RPIC Part 107
  liability acceptance with timestamp in the `missions` record).
- `GET /api/missions/{mission_id}/export/{fmt}` (Module D: exports `.kml`, `.kmz`, `.plan`, `.json`
  with embedded FAA Part 107 disclaimer after verifying RPIC acknowledgment).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import List
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Response, status

from app.api.uploads import DEFAULT_GCS_BUCKET, generate_batch_signed_urls
from app.models.schemas import (
    MissionCreateRequest,
    MissionRecord,
    MissionStatus,
    Obstacle3D,
    PICAcknowledgmentRequest,
    SubjectFocalPoint,
    TrajectoryRequest,
    TrajectoryResult,
    WaypointInput,
)
from app.services.exporter import (
    PICLiabilityNotAcknowledgedError,
    export_dji_kml,
    export_dji_kmz,
    export_px4_plan,
    export_webgl_preview_json,
)
from app.services.store import db_store
from app.services.trajectory import generate_trajectory
from app.services.world_model import gemini_auditor_service, project_genie_service

router = APIRouter(prefix="/api/missions", tags=["missions"])


def _default_waypoints_from_bbox(
    min_lat: float,
    min_lon: float,
    max_lat: float,
    max_lon: float,
) -> tuple[List[WaypointInput], SubjectFocalPoint]:
    center_lat = 0.5 * (min_lat + max_lat)
    center_lon = 0.5 * (min_lon + max_lon)
    d_lat = max((max_lat - min_lat) * 0.35, 0.0004)
    d_lon = max((max_lon - min_lon) * 0.35, 0.0004)

    subject = SubjectFocalPoint(lat=center_lat, lon=center_lon, alt_agl=6.0)
    waypoints = [
        WaypointInput(lat=center_lat - d_lat, lon=center_lon - d_lon, alt_agl=22.0),
        WaypointInput(lat=center_lat + d_lat, lon=center_lon - d_lon, alt_agl=32.0),
        WaypointInput(lat=center_lat + d_lat, lon=center_lon + d_lon, alt_agl=36.0),
        WaypointInput(lat=center_lat - d_lat, lon=center_lon + d_lon, alt_agl=25.0),
    ]
    return waypoints, subject


@router.post("/create", response_model=MissionRecord, status_code=status.HTTP_201_CREATED)
def create_mission(req: MissionCreateRequest) -> MissionRecord:
    """
    Create a new mission, generate time-limited GCS Signed URLs for direct client image
    uploads, compute the 3D B-spline trajectory, and run the Gemini Ultra framing audit.
    """
    mission_id = uuid4()

    # 1. Generate time-limited GCS Signed URLs for client-side direct uploads
    signed_urls = generate_batch_signed_urls(
        filenames=req.photo_filenames,
        mission_id=str(mission_id),
        bucket_name=DEFAULT_GCS_BUCKET,
        expiration=15,
    )

    # 2. Synthesize 3D environment via Project Genie service
    genie_scene = project_genie_service.synthesize_3d_environment(
        mission_name=req.name,
        bounding_box=req.bounding_box,
        photo_filenames=req.photo_filenames,
        obstacles=req.obstacles,
    )
    detected_obstacles = [Obstacle3D(**obs) for obs in genie_scene["detected_obstacles"]]

    # 3. Prepare waypoints & subject focal point
    default_wps, default_subject = _default_waypoints_from_bbox(
        req.bounding_box.min_lat,
        req.bounding_box.min_lon,
        req.bounding_box.max_lat,
        req.bounding_box.max_lon,
    )
    waypoints = req.waypoints if req.waypoints and len(req.waypoints) >= 2 else default_wps
    subject = req.subject_focal_point or default_subject

    traj_req = TrajectoryRequest(
        waypoints=waypoints,
        subject_focal_point=subject,
        orbit_radius_m=req.orbit_radius_m,
        num_samples=60,
        obstacles=detected_obstacles,
    )
    trajectory = generate_trajectory(traj_req)

    # 4. Run Gemini Ultra Multimodal Framing & Clearance Auditor
    audit_report = gemini_auditor_service.audit_trajectory(
        trajectory=trajectory,
        obstacles=detected_obstacles,
    )

    export_urls = {
        "dji_kmz": f"https://storage.googleapis.com/{DEFAULT_GCS_BUCKET}/missions/{mission_id}/exports/mission.kmz",
        "dji_kml": f"https://storage.googleapis.com/{DEFAULT_GCS_BUCKET}/missions/{mission_id}/exports/mission.kml",
        "px4_plan": f"https://storage.googleapis.com/{DEFAULT_GCS_BUCKET}/missions/{mission_id}/exports/mission.plan",
        "webgl_json": f"/api/missions/{mission_id}/export/json",
    }

    mission = MissionRecord(
        id=mission_id,
        user_id=req.user_id or UUID("00000000-0000-4000-8000-000000000001"),
        name=req.name,
        status=MissionStatus.READY,
        bounding_box=req.bounding_box.model_dump(),
        flight_metrics=trajectory.metrics,
        export_urls=export_urls,
        audit_report=audit_report,
        signed_upload_urls=signed_urls,
        trajectory=trajectory,
        pic_acknowledged=False,
        pic_acknowledged_at=None,
    )
    return db_store.save_mission(mission)


@router.get("", response_model=List[MissionRecord])
def list_missions() -> List[MissionRecord]:
    return db_store.list_missions()


@router.get("/{mission_id}", response_model=MissionRecord)
def get_mission(mission_id: UUID) -> MissionRecord:
    mission = db_store.get_mission(mission_id)
    if not mission:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Mission not found")
    return mission


@router.post("/{mission_id}/acknowledge-pic", response_model=MissionRecord)
def acknowledge_pic_liability(mission_id: UUID, req: PICAcknowledgmentRequest) -> MissionRecord:
    """
    Record mandatory Remote Pilot-in-Command (RPIC) FAA Part 107 liability acknowledgment
    with a UTC timestamp in the `missions` database record (Section 7, Guardrail #1).
    """
    mission = db_store.get_mission(mission_id)
    if not mission:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Mission not found")

    if not req.acknowledged:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="RPIC FAA Part 107 acknowledgment must be explicitly set to True.",
        )

    now_utc = datetime.now(timezone.utc)
    mission.pic_acknowledged = True
    mission.pic_acknowledged_at = now_utc
    mission.pic_acknowledged_by = req.pilot_name_or_cert
    db_store.log_pic_compliance_event(
        mission_id=mission.id,
        user_id=mission.user_id,
        pilot_name_or_cert=req.pilot_name_or_cert,
        laanc_verified=req.laanc_verified,
        acknowledged_at=now_utc,
    )
    return db_store.save_mission(mission)


@router.get("/{mission_id}/export/{fmt}")
def export_mission_file(mission_id: UUID, fmt: str) -> Response:
    """
    Export validated flight path as `.kml`, `.kmz`, `.plan`, or `.json`.
    Enforces blocking RPIC Part 107 liability acknowledgment before exporting `.kml`, `.kmz`, or `.plan`.
    """
    mission = db_store.get_mission(mission_id)
    if not mission or not mission.trajectory:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Mission or trajectory not found")

    fmt_clean = fmt.lower().lstrip(".")
    require_ack = fmt_clean in ("kml", "kmz", "plan")

    try:
        if fmt_clean == "kml":
            content = export_dji_kml(
                mission.trajectory,
                mission_name=mission.name,
                mission=mission,
                require_pic_ack=require_ack,
            )
            return Response(
                content=content,
                media_type="application/vnd.google-earth.kml+xml",
                headers={"Content-Disposition": f'attachment; filename="mission_{mission_id}.kml"'},
            )
        elif fmt_clean == "kmz":
            content_bytes = export_dji_kmz(
                mission.trajectory,
                mission_name=mission.name,
                mission=mission,
                require_pic_ack=require_ack,
            )
            return Response(
                content=content_bytes,
                media_type="application/vnd.google-earth.kmz",
                headers={"Content-Disposition": f'attachment; filename="mission_{mission_id}.kmz"'},
            )
        elif fmt_clean == "plan":
            content = export_px4_plan(
                mission.trajectory,
                mission_name=mission.name,
                mission=mission,
                require_pic_ack=require_ack,
            )
            return Response(
                content=content,
                media_type="application/json",
                headers={"Content-Disposition": f'attachment; filename="mission_{mission_id}.plan"'},
            )
        elif fmt_clean == "json":
            content = export_webgl_preview_json(
                mission.trajectory,
                mission_name=mission.name,
                mission=mission,
                require_pic_ack=False,
            )
            return Response(content=content, media_type="application/json")
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unsupported export format '{fmt}'. Supported: kml, kmz, plan, json.",
            )
    except PICLiabilityNotAcknowledgedError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        ) from exc
