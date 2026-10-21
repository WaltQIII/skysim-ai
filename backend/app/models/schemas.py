from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, field_validator


MANDATORY_PIC_DISCLAIMER = (
    "SkySim AI outputs are assistive pre-visualization calculations only. "
    "The Remote Pilot in Command (RPIC) retains sole regulatory responsibility "
    "under FAA Part 107 for visual line-of-sight (VLOS), obstacle clearance, "
    "airspace authorizations (LAANC), and aircraft operation."
)


class UserPlan(str, Enum):
    TRIAL = "trial"
    PRO_MONTHLY = "pro_monthly"
    ENTERPRISE = "enterprise"


class MissionStatus(str, Enum):
    DRAFT = "draft"
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"


class WaypointInput(BaseModel):
    lat: float = Field(..., ge=-90.0, le=90.0, description="WGS84 Latitude in degrees")
    lon: float = Field(..., ge=-180.0, le=180.0, description="WGS84 Longitude in degrees")
    alt_agl: float = Field(..., gt=0.0, le=400.0, description="Altitude Above Ground Level in meters")
    gimbal_pitch: Optional[float] = Field(
        default=None, ge=-90.0, le=30.0, description="Optional manual gimbal pitch override in degrees"
    )
    heading: Optional[float] = Field(
        default=None, ge=0.0, le=360.0, description="Optional aircraft/gimbal yaw heading in degrees"
    )
    speed_ms: Optional[float] = Field(
        default=None, gt=0.0, description="Desired segment speed in m/s (will be clamped to safety limits)"
    )


class SubjectFocalPoint(BaseModel):
    lat: float = Field(..., ge=-90.0, le=90.0, description="Target focal point latitude")
    lon: float = Field(..., ge=-180.0, le=180.0, description="Target focal point longitude")
    alt_agl: float = Field(default=5.0, ge=0.0, description="Target focal point height AGL in meters")


class Obstacle3D(BaseModel):
    lat: float = Field(..., ge=-90.0, le=90.0)
    lon: float = Field(..., ge=-180.0, le=180.0)
    radius_m: float = Field(default=3.0, gt=0.0, description="Horizontal obstacle radius in meters")
    height_agl: float = Field(default=15.0, gt=0.0, description="Obstacle top height AGL in meters")
    label: str = Field(default="Obstacle", description="Human-readable label (e.g., northern tree canopy)")


class TrajectoryRequest(BaseModel):
    waypoints: List[WaypointInput] = Field(..., min_length=2, description="Ordered list of key mission waypoints")
    subject_focal_point: SubjectFocalPoint = Field(..., description="Primary target focal point for gimbal tracking")
    orbit_radius_m: Optional[float] = Field(default=None, gt=0.0, description="Optional orbit radius in meters")
    num_samples: int = Field(default=60, ge=10, le=1000, description="Number of interpolated trajectory samples")
    desired_horizontal_speed_ms: float = Field(default=8.0, gt=0.0, description="Target horizontal cruise speed in m/s")
    desired_vertical_speed_ms: float = Field(default=3.0, gt=0.0, description="Target vertical speed in m/s")
    min_clearance_buffer_m: float = Field(
        default=5.0, ge=1.0, description="Minimum safety buffer margin from ground and obstacles in meters"
    )
    obstacles: List[Obstacle3D] = Field(default_factory=list, description="Known site obstacles for collision buffer checks")


class InterpolatedPoint(BaseModel):
    index: int
    timestamp_s: float
    lat: float
    lon: float
    alt_agl: float
    local_x_m: float
    local_y_m: float
    local_z_m: float
    horizontal_speed_ms: float
    vertical_speed_ms: float
    total_speed_ms: float
    gimbal_pitch_deg: float
    gimbal_yaw_deg: float
    clearance_margin_m: float


class FlightMetrics(BaseModel):
    duration_seconds: float
    total_distance_meters: float
    max_altitude_agl: float
    min_altitude_agl: float
    max_horizontal_speed_ms: float
    max_vertical_speed_ms: float
    min_obstacle_clearance_m: float
    collision_buffer_respected: bool


class TrajectoryResult(BaseModel):
    points: List[InterpolatedPoint]
    metrics: FlightMetrics
    subject_focal_point: SubjectFocalPoint
    warnings: List[str] = Field(default_factory=list)
    disclaimer: str = Field(default=MANDATORY_PIC_DISCLAIMER)


class AuditReport(BaseModel):
    safety_score: int = Field(..., ge=0, le=100)
    clearance_warnings: List[str] = Field(default_factory=list)
    cinematic_score: int = Field(..., ge=0, le=100)
    framing_tips: List[str] = Field(default_factory=list)


class BoundingBoxInput(BaseModel):
    min_lat: float = Field(..., ge=-90.0, le=90.0)
    min_lon: float = Field(..., ge=-180.0, le=180.0)
    max_lat: float = Field(..., ge=-90.0, le=90.0)
    max_lon: float = Field(..., ge=-180.0, le=180.0)
    geojson: Optional[Dict[str, Any]] = None

    @field_validator("max_lat")
    @classmethod
    def validate_lat_bounds(cls, v: float, info: Any) -> float:
        min_lat = info.data.get("min_lat")
        if min_lat is not None and v < min_lat:
            raise ValueError("max_lat must be greater than or equal to min_lat")
        return v


class MissionCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=200, examples=["742 Evergreen Terrace Luxury Orbit"])
    user_id: Optional[UUID] = Field(default_factory=uuid4)
    bounding_box: BoundingBoxInput
    photo_filenames: List[str] = Field(
        default_factory=lambda: ["site_north.jpg", "site_south.jpg"],
        description="Client site photo filenames requiring direct-to-GCS signed PUT URLs",
    )
    waypoints: Optional[List[WaypointInput]] = None
    subject_focal_point: Optional[SubjectFocalPoint] = None
    orbit_radius_m: Optional[float] = None
    obstacles: List[Obstacle3D] = Field(default_factory=list)


class SignedUploadUrlItem(BaseModel):
    filename: str
    blob_name: str
    upload_url: str
    method: str = "PUT"
    expires_in_minutes: int = 15
    content_type: str = "application/octet-stream"


class UserRecord(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    email: str
    plan: UserPlan = UserPlan.TRIAL
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class MissionRecord(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    user_id: UUID
    name: str
    status: MissionStatus = MissionStatus.DRAFT
    bounding_box: Dict[str, Any]
    flight_metrics: Optional[FlightMetrics] = None
    export_urls: Dict[str, str] = Field(default_factory=dict)
    audit_report: Optional[AuditReport] = None
    signed_upload_urls: List[SignedUploadUrlItem] = Field(default_factory=list)
    trajectory: Optional[TrajectoryResult] = None
    pic_acknowledged: bool = False
    pic_acknowledged_at: Optional[datetime] = None
    pic_acknowledged_by: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class PICAcknowledgmentRequest(BaseModel):
    acknowledged: bool = Field(..., description="Must be True to unlock mission exports")
    pilot_name_or_cert: str = Field(
        default="RPIC-Part107", min_length=2, description="Remote Pilot in Command identifier or certificate reference"
    )
    laanc_verified: bool = Field(
        default=True, description="Confirmation that local airspace classification and FAA LAANC status were verified"
    )
