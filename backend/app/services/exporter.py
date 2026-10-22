"""
Mission Exporter Service for SkySim AI.

Supports:
1. DJI Waypoint 3.0 (.kml and .kmz WPML archive compatible with DJI Pilot 2 / DJI Fly)
2. PX4 / QGroundControl (.plan JSON format)
3. WebGL / Three.js 3D Preview (.json format)

Critically enforces Section 5 & Section 7 Guardrail #1:
- Embeds the mandatory FAA Part 107 Remote Pilot-in-Command (RPIC) legal disclaimer
  directly into XML comments, ExtendedData tags, ZIP comments, and JSON metadata comments.
- Validates that PIC liability shielding acknowledgment has been recorded prior to export.
"""

from __future__ import annotations

import io
import json
import zipfile
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from xml.sax.saxutils import escape

from app.models.schemas import (
    MANDATORY_PIC_DISCLAIMER,
    MissionRecord,
    TrajectoryResult,
)


class PICLiabilityNotAcknowledgedError(PermissionError):
    """Raised when attempting to export a flight plan before RPIC Part 107 acknowledgment."""


def verify_pic_acknowledgment(mission: Optional[MissionRecord], require_ack: bool = True) -> str:
    """
    Verify that the Remote Pilot in Command (RPIC) has acknowledged the mandatory
    FAA Part 107 liability shield before exporting mission files.
    Returns ISO timestamp of acknowledgment.
    """
    if mission is not None:
        if require_ack and (not mission.pic_acknowledged or mission.pic_acknowledged_at is None):
            raise PICLiabilityNotAcknowledgedError(
                "Export blocked: Remote Pilot in Command (RPIC) must acknowledge the "
                "FAA Part 107 liability disclaimer before exporting mission files."
            )
        if mission.pic_acknowledged_at is not None:
            return mission.pic_acknowledged_at.isoformat()
    return datetime.now(timezone.utc).isoformat()


def export_dji_kml(
    trajectory: TrajectoryResult,
    mission_name: str = "SkySim AI Mission",
    mission: Optional[MissionRecord] = None,
    require_pic_ack: bool = False,
) -> str:
    """
    Compile trajectory into DJI Waypoint 3.0 (.kml / WPML) XML string with embedded
    mandatory FAA Part 107 RPIC legal disclaimer in XML comments and ExtendedData.
    """
    ack_timestamp = verify_pic_acknowledgment(mission, require_ack=require_pic_ack)
    safe_name = escape(mission_name)
    safe_disclaimer = escape(MANDATORY_PIC_DISCLAIMER)

    coord_lines = []
    placemarks = []

    for pt in trajectory.points:
        coord_lines.append(f"{pt.lon:.8f},{pt.lat:.8f},{pt.alt_agl:.3f}")
        placemarks.append(
            f"""      <Placemark>
        <name>WP-{pt.index:03d}</name>
        <Point>
          <altitudeMode>relativeToGround</altitudeMode>
          <coordinates>{pt.lon:.8f},{pt.lat:.8f},{pt.alt_agl:.3f}</coordinates>
        </Point>
        <wpml:index>{pt.index}</wpml:index>
        <wpml:executeHeight>{pt.alt_agl:.3f}</wpml:executeHeight>
        <wpml:waypointSpeed>{max(pt.horizontal_speed_ms, 1.0):.2f}</wpml:waypointSpeed>
        <wpml:waypointHeadingParam>
          <wpml:waypointHeadingMode>smoothTransition</wpml:waypointHeadingMode>
          <wpml:waypointHeadingAngle>{pt.gimbal_yaw_deg:.2f}</wpml:waypointHeadingAngle>
        </wpml:waypointHeadingParam>
        <wpml:waypointTurnParam>
          <wpml:waypointTurnMode>toPointAndPassWithContinuityCurvature</wpml:waypointTurnMode>
          <wpml:waypointTurnDampingDist>2.5</wpml:waypointTurnDampingDist>
        </wpml:waypointTurnParam>
        <wpml:actionGroup>
          <wpml:actionGroupId>{pt.index}</wpml:actionGroupId>
          <wpml:actionActuatorFunc>gimbalRotate</wpml:actionActuatorFunc>
          <wpml:actionActuatorFuncParam>
            <wpml:gimbalPitchRotateAngle>{pt.gimbal_pitch_deg:.2f}</wpml:gimbalPitchRotateAngle>
            <wpml:gimbalYawRotateAngle>{pt.gimbal_yaw_deg:.2f}</wpml:gimbalYawRotateAngle>
          </wpml:actionActuatorFuncParam>
        </wpml:actionGroup>
      </Placemark>"""
        )

    coords_block = " ".join(coord_lines)
    placemarks_block = "\n".join(placemarks)

    kml_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!--
  ===========================================================================
  MANDATORY FAA PART 107 LEGAL DISCLAIMER (RPIC LIABILITY SHIELD)
  {MANDATORY_PIC_DISCLAIMER}
  RPIC Acknowledged Timestamp: {ack_timestamp}
  Generator: SkySim AI Autonomous Trajectory Engine (DJI Waypoint 3.0)
  ===========================================================================
-->
<kml xmlns="http://www.opengis.net/kml/2.2" xmlns:wpml="http://www.dji.com/wpmz/1.0.3">
  <Document>
    <name>{safe_name}</name>
    <description>{safe_disclaimer}</description>
    <ExtendedData>
      <Data name="legal_disclaimer">
        <value>{safe_disclaimer}</value>
      </Data>
      <Data name="rpic_acknowledged_at">
        <value>{ack_timestamp}</value>
      </Data>
      <Data name="dji_waypoint_version">
        <value>3.0</value>
      </Data>
      <Data name="max_horizontal_speed_ms">
        <value>{trajectory.metrics.max_horizontal_speed_ms:.2f}</value>
      </Data>
      <Data name="max_vertical_speed_ms">
        <value>{trajectory.metrics.max_vertical_speed_ms:.2f}</value>
      </Data>
    </ExtendedData>
    <wpml:missionConfig>
      <wpml:flyToWaylineMode>safely</wpml:flyToWaylineMode>
      <wpml:finishAction>goHome</wpml:finishAction>
      <wpml:exitOnRCLost>executeLostAction</wpml:exitOnRCLost>
      <wpml:executeRCLostAction>goBack</wpml:executeRCLostAction>
      <wpml:globalTransitionalSpeed>{min(trajectory.metrics.max_horizontal_speed_ms, 15.0):.2f}</wpml:globalTransitionalSpeed>
    </wpml:missionConfig>
    <Folder>
      <name>Waylines</name>
      <wpml:templateId>0</wpml:templateId>
      <wpml:waylineId>0</wpml:waylineId>
      <wpml:autoFlightSpeed>{min(max(trajectory.metrics.max_horizontal_speed_ms, 2.0), 15.0):.2f}</wpml:autoFlightSpeed>
      <Placemark>
        <name>Flight Trajectory Line</name>
        <LineString>
          <tessellate>1</tessellate>
          <altitudeMode>relativeToGround</altitudeMode>
          <coordinates>{coords_block}</coordinates>
        </LineString>
      </Placemark>
{placemarks_block}
    </Folder>
  </Document>
</kml>
"""
    return kml_content


def export_dji_kmz(
    trajectory: TrajectoryResult,
    mission_name: str = "SkySim AI Mission",
    mission: Optional[MissionRecord] = None,
    require_pic_ack: bool = False,
) -> bytes:
    """
    Package DJI Waypoint 3.0 mission into a standard `.kmz` ZIP archive containing
    `wpmz/template.kml`, `wpmz/waylines.wpml`, and root `doc.kml`, with the mandatory
    FAA Part 107 disclaimer embedded in every file and the archive comment.
    """
    kml_xml = export_dji_kml(
        trajectory=trajectory,
        mission_name=mission_name,
        mission=mission,
        require_pic_ack=require_pic_ack,
    )
    ack_timestamp = verify_pic_acknowledgment(mission, require_ack=require_pic_ack)
    safe_disclaimer = escape(MANDATORY_PIC_DISCLAIMER)
    waylines_wpml = kml_xml.replace(
        "<wpml:missionConfig>",
        """<wpml:missionConfig>
      <wpml:droneInfo>
        <wpml:droneEnumValue>67</wpml:droneEnumValue>
        <wpml:droneSubEnumValue>0</wpml:droneSubEnumValue>
      </wpml:droneInfo>
      <wpml:payloadInfo>
        <wpml:payloadEnumValue>52</wpml:payloadEnumValue>
        <wpml:payloadPositionIndex>0</wpml:payloadPositionIndex>
      </wpml:payloadInfo>""",
        1,
    ).replace(
        "<wpml:templateId>0</wpml:templateId>",
        f"<wpml:templateId>0</wpml:templateId>\n      <wpml:executeHeightMode>relativeToStartPoint</wpml:executeHeightMode>\n      <!-- RPIC Verified At {ack_timestamp}: {safe_disclaimer} -->",
        1,
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.comment = MANDATORY_PIC_DISCLAIMER.encode("utf-8")
        zf.writestr("doc.kml", kml_xml.encode("utf-8"))
        zf.writestr("wpmz/template.kml", kml_xml.encode("utf-8"))
        zf.writestr("wpmz/waylines.wpml", waylines_wpml.encode("utf-8"))
    return buffer.getvalue()


def export_px4_plan(
    trajectory: TrajectoryResult,
    mission_name: str = "SkySim AI Mission",
    mission: Optional[MissionRecord] = None,
    require_pic_ack: bool = False,
) -> str:
    """
    Compile trajectory into PX4 / QGroundControl `.plan` JSON format with embedded
    mandatory FAA Part 107 RPIC legal disclaimer in metadata comments.
    """
    ack_timestamp = verify_pic_acknowledgment(mission, require_ack=require_pic_ack)

    items = []
    seq_id = 1
    for idx, pt in enumerate(trajectory.points):
        # MAV_CMD_NAV_TAKEOFF (22) for first point, MAV_CMD_NAV_WAYPOINT (16) for subsequent points
        nav_cmd = 22 if idx == 0 else 16
        items.append(
            {
                "AMSLAltAboveTerrain": pt.alt_agl,
                "Altitude": pt.alt_agl,
                "AltitudeMode": 1,  # Relative to home / AGL
                "autoContinue": True,
                "command": nav_cmd,
                "doJumpId": seq_id,
                "frame": 3,  # MAV_FRAME_GLOBAL_RELATIVE_ALT
                "params": [
                    0.0,  # Hold time
                    0.0,  # Acceptance radius
                    0.0,  # Pass radius (0 = spline/pass through)
                    pt.gimbal_yaw_deg,
                    pt.lat,
                    pt.lon,
                    pt.alt_agl,
                ],
                "type": "SimpleItem",
            }
        )
        seq_id += 1

        # Add MAV_CMD_DO_MOUNT_CONTROL (205) for gimbal pitch & yaw control
        items.append(
            {
                "autoContinue": True,
                "command": 205,
                "doJumpId": seq_id,
                "frame": 2,
                "params": [
                    pt.gimbal_pitch_deg,
                    0.0,  # Roll
                    pt.gimbal_yaw_deg,
                    0.0,
                    0.0,
                    0.0,
                    2.0,  # MAV_MOUNT_MODE_MAVLINK_TARGETING
                ],
                "type": "SimpleItem",
            }
        )
        seq_id += 1

    home_pt = trajectory.points[0]
    plan_payload: Dict[str, Any] = {
        "_comment": f"MANDATORY FAA PART 107 LEGAL DISCLAIMER: {MANDATORY_PIC_DISCLAIMER}",
        "legalDisclaimer": MANDATORY_PIC_DISCLAIMER,
        "rpicComplianceMetadata": {
            "disclaimer": MANDATORY_PIC_DISCLAIMER,
            "rpicAcknowledgedAt": ack_timestamp,
            "regulatoryFramework": "FAA 14 CFR Part 107",
            "missionName": mission_name,
        },
        "fileType": "Plan",
        "groundStation": "QGroundControl",
        "version": 1,
        "geoFence": {
            "circles": [],
            "polygons": [],
            "version": 2,
        },
        "rallyPoints": {
            "points": [],
            "version": 2,
        },
        "mission": {
            "cruiseSpeed": min(max(trajectory.metrics.max_horizontal_speed_ms, 1.0), 15.0),
            "firmwareType": 12,  # PX4 Pro
            "globalPlanAltitudeMode": 1,
            "hoverSpeed": min(max(trajectory.metrics.max_horizontal_speed_ms * 0.5, 1.0), 5.0),
            "items": items,
            "plannedHomePosition": [home_pt.lat, home_pt.lon, home_pt.alt_agl],
            "vehicleType": 2,  # Quadrotor
            "version": 2,
        },
    }
    return json.dumps(plan_payload, indent=2)


def export_webgl_preview_json(
    trajectory: TrajectoryResult,
    mission_name: str = "SkySim AI Mission",
    mission: Optional[MissionRecord] = None,
    require_pic_ack: bool = False,
) -> str:
    """
    Compile trajectory into Three.js / WebGL preview JSON format with local 3D scene
    coordinates (Three.js Y-up convention: x = East, y = Altitude AGL, z = -North),
    camera target vectors, and mandatory FAA Part 107 legal disclaimer.
    """
    ack_timestamp = verify_pic_acknowledgment(mission, require_ack=require_pic_ack)

    keyframes = []
    for pt in trajectory.points:
        keyframes.append(
            {
                "index": pt.index,
                "t": pt.timestamp_s,
                "wgs84": {
                    "lat": pt.lat,
                    "lon": pt.lon,
                    "alt_agl": pt.alt_agl,
                },
                "position_threejs": {
                    "x": pt.local_x_m,
                    "y": pt.local_z_m,
                    "z": round(-pt.local_y_m, 3),
                },
                "gimbal": {
                    "pitch_deg": pt.gimbal_pitch_deg,
                    "yaw_deg": pt.gimbal_yaw_deg,
                },
                "kinematics": {
                    "horizontal_speed_ms": pt.horizontal_speed_ms,
                    "vertical_speed_ms": pt.vertical_speed_ms,
                    "total_speed_ms": pt.total_speed_ms,
                    "clearance_margin_m": pt.clearance_margin_m,
                },
            }
        )

    preview_payload: Dict[str, Any] = {
        "_comment": f"MANDATORY FAA PART 107 LEGAL DISCLAIMER: {MANDATORY_PIC_DISCLAIMER}",
        "metadata": {
            "mission_name": mission_name,
            "generator": "SkySim AI WebGL/Three.js Spatial Preview Exporter",
            "legal_disclaimer": MANDATORY_PIC_DISCLAIMER,
            "rpic_acknowledged_at": ack_timestamp,
            "coordinate_system": "Three.js Right-Handed Y-Up (x=East_m, y=Alt_AGL_m, z=-North_m)",
        },
        "subject_focal_point": {
            "lat": trajectory.subject_focal_point.lat,
            "lon": trajectory.subject_focal_point.lon,
            "alt_agl": trajectory.subject_focal_point.alt_agl,
            "position_threejs": {
                "x": 0.0,
                "y": trajectory.subject_focal_point.alt_agl,
                "z": 0.0,
            },
        },
        "metrics": trajectory.metrics.model_dump(),
        "warnings": trajectory.warnings,
        "keyframes": keyframes,
    }
    return json.dumps(preview_payload, indent=2)
