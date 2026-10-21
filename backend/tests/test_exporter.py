"""Unit tests for SkySim AI Exporter Service & Mandatory FAA Part 107 Disclaimers."""

from __future__ import annotations

import io
import json
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from app.models.schemas import (
    MANDATORY_PIC_DISCLAIMER,
    MissionRecord,
    MissionStatus,
    SubjectFocalPoint,
    TrajectoryRequest,
    WaypointInput,
)
from app.services.exporter import (
    PICLiabilityNotAcknowledgedError,
    export_dji_kml,
    export_dji_kmz,
    export_px4_plan,
    export_webgl_preview_json,
)
from app.services.trajectory import generate_trajectory


@pytest.fixture
def sample_trajectory():
    subject = SubjectFocalPoint(lat=25.7617, lon=-80.1918, alt_agl=5.0)
    waypoints = [
        WaypointInput(lat=25.7612, lon=-80.1924, alt_agl=25.0),
        WaypointInput(lat=25.7618, lon=-80.1912, alt_agl=32.0),
        WaypointInput(lat=25.7623, lon=-80.1920, alt_agl=28.0),
    ]
    req = TrajectoryRequest(waypoints=waypoints, subject_focal_point=subject, num_samples=20)
    return generate_trajectory(req)


def test_export_dji_kml_format_and_disclaimer(sample_trajectory):
    kml_str = export_dji_kml(sample_trajectory, mission_name="742 Evergreen Terrace Luxury Orbit")

    # Verify XML comment contains mandatory FAA Part 107 RPIC disclaimer
    assert "<!--" in kml_str
    assert MANDATORY_PIC_DISCLAIMER in kml_str
    assert "FAA Part 107" in kml_str
    assert "Remote Pilot in Command (RPIC)" in kml_str

    # Verify XML parses cleanly and has DJI Waypoint 3.0 WPML tags
    root = ET.fromstring(kml_str)
    assert "kml" in root.tag
    assert "wpml:missionConfig" in kml_str
    assert "wpml:gimbalPitchRotateAngle" in kml_str
    assert "742 Evergreen Terrace Luxury Orbit" in kml_str


def test_export_dji_kmz_archive_and_disclaimer(sample_trajectory):
    kmz_bytes = export_dji_kmz(sample_trajectory, mission_name="Brickell Bay Inspection")
    assert isinstance(kmz_bytes, bytes)
    assert len(kmz_bytes) > 200

    with zipfile.ZipFile(io.BytesIO(kmz_bytes), mode="r") as zf:
        names = zf.namelist()
        assert "doc.kml" in names
        assert "wpmz/template.kml" in names
        assert "wpmz/waylines.wpml" in names

        # Check ZIP archive comment
        assert MANDATORY_PIC_DISCLAIMER in zf.comment.decode("utf-8")

        # Check embedded KML/WPML files
        for member in ["doc.kml", "wpmz/template.kml", "wpmz/waylines.wpml"]:
            content = zf.read(member).decode("utf-8")
            assert MANDATORY_PIC_DISCLAIMER in content
            ET.fromstring(content)


def test_export_px4_plan_format_and_disclaimer(sample_trajectory):
    plan_str = export_px4_plan(sample_trajectory, mission_name="Solar Array Survey")
    data = json.loads(plan_str)

    assert data["fileType"] == "Plan"
    assert data["groundStation"] == "QGroundControl"
    assert MANDATORY_PIC_DISCLAIMER in data["_comment"]
    assert data["legalDisclaimer"] == MANDATORY_PIC_DISCLAIMER
    assert data["rpicComplianceMetadata"]["disclaimer"] == MANDATORY_PIC_DISCLAIMER

    mission = data["mission"]
    assert mission["cruiseSpeed"] <= 15.0
    assert len(mission["items"]) == len(sample_trajectory.points) * 2
    assert mission["items"][0]["command"] == 22  # MAV_CMD_NAV_TAKEOFF
    assert mission["items"][1]["command"] == 205  # MAV_CMD_DO_MOUNT_CONTROL


def test_export_webgl_preview_json_format_and_disclaimer(sample_trajectory):
    preview_str = export_webgl_preview_json(sample_trajectory, mission_name="ThreeJS Preview Test")
    data = json.loads(preview_str)

    assert MANDATORY_PIC_DISCLAIMER in data["_comment"]
    assert data["metadata"]["legal_disclaimer"] == MANDATORY_PIC_DISCLAIMER
    assert len(data["keyframes"]) == len(sample_trajectory.points)

    first_kf = data["keyframes"][0]
    assert "position_threejs" in first_kf
    assert "x" in first_kf["position_threejs"]
    assert "y" in first_kf["position_threejs"]
    assert "z" in first_kf["position_threejs"]
    assert "gimbal" in first_kf


def test_pic_acknowledgment_shield_blocks_unacknowledged_export(sample_trajectory):
    unacked_mission = MissionRecord(
        id=uuid4(),
        user_id=uuid4(),
        name="Unacknowledged Mission",
        status=MissionStatus.READY,
        bounding_box={"min_lat": 25.76, "min_lon": -80.19, "max_lat": 25.77, "max_lon": -80.18},
        pic_acknowledged=False,
        pic_acknowledged_at=None,
    )

    with pytest.raises(PICLiabilityNotAcknowledgedError):
        export_dji_kml(sample_trajectory, mission=unacked_mission, require_pic_ack=True)

    with pytest.raises(PICLiabilityNotAcknowledgedError):
        export_px4_plan(sample_trajectory, mission=unacked_mission, require_pic_ack=True)

    # Once acknowledged with timestamp, export succeeds and embeds the timestamp
    ack_time = datetime(2026, 10, 20, 18, 30, 0, tzinfo=timezone.utc)
    unacked_mission.pic_acknowledged = True
    unacked_mission.pic_acknowledged_at = ack_time

    kml_out = export_dji_kml(sample_trajectory, mission=unacked_mission, require_pic_ack=True)
    assert ack_time.isoformat() in kml_out
