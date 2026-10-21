"""
Trajectory Engine & Cubic B-Spline Interpolation Service for SkySim AI.

Implements:
- WGS84 (lat, lon, alt_agl) <-> Local ENU metric coordinate transformations.
- Cubic B-Spline (degree k=3) 3D trajectory smoothing via SciPy.
- Orbital waypoint expansion when orbit_radius_m is specified.
- Collision buffer margin enforcement against ground (AGL) and 3D obstacles.
- Hard kinematic speed clamping (Max Horizontal: 15.0 m/s, Max Ascent: 5.0 m/s).
- Cinematographically fluid gimbal pitch and unwrapped yaw transitions tracking the subject focal point.
"""

from __future__ import annotations

import math
from typing import List, Optional, Tuple

import numpy as np
from scipy.interpolate import BSpline, splprep, splev
from scipy.ndimage import gaussian_filter1d

from app.models.schemas import (
    FlightMetrics,
    InterpolatedPoint,
    MANDATORY_PIC_DISCLAIMER,
    Obstacle3D,
    SubjectFocalPoint,
    TrajectoryRequest,
    TrajectoryResult,
    WaypointInput,
)

EARTH_RADIUS_M = 6378137.0
MAX_HORIZONTAL_SPEED_MS = 15.0
MAX_ASCENT_SPEED_MS = 5.0
MAX_DESCENT_SPEED_MS = 5.0
MIN_GIMBAL_PITCH_DEG = -90.0
MAX_GIMBAL_PITCH_DEG = 30.0


def wgs84_to_enu(
    lat: float,
    lon: float,
    alt_agl: float,
    ref_lat: float,
    ref_lon: float,
) -> Tuple[float, float, float]:
    """Convert WGS84 (lat, lon, alt_agl) to local East-North-Up (x, y, z) in meters."""
    d_lat = math.radians(lat - ref_lat)
    d_lon = math.radians(lon - ref_lon)
    cos_lat = math.cos(math.radians(ref_lat))
    east_x = d_lon * EARTH_RADIUS_M * cos_lat
    north_y = d_lat * EARTH_RADIUS_M
    up_z = float(alt_agl)
    return east_x, north_y, up_z


def enu_to_wgs84(
    east_x: float,
    north_y: float,
    up_z: float,
    ref_lat: float,
    ref_lon: float,
) -> Tuple[float, float, float]:
    """Convert local East-North-Up (x, y, z) in meters back to WGS84 (lat, lon, alt_agl)."""
    cos_lat = math.cos(math.radians(ref_lat))
    if abs(cos_lat) < 1e-12:
        cos_lat = 1e-12
    d_lat = north_y / EARTH_RADIUS_M
    d_lon = east_x / (EARTH_RADIUS_M * cos_lat)
    lat = ref_lat + math.degrees(d_lat)
    lon = ref_lon + math.degrees(d_lon)
    return lat, lon, float(up_z)


def generate_orbit_waypoints(
    start: WaypointInput,
    subject: SubjectFocalPoint,
    orbit_radius_m: float,
    altitude_agl: float,
    end: Optional[WaypointInput] = None,
    num_orbit_control_points: int = 6,
) -> List[WaypointInput]:
    """
    Generate key control waypoints connecting Start -> Orbit around Subject Focal Point -> End.
    Satisfies Module B specification: (Start, Subject Focal Point, Orbit Radius, Altitude AGL, End).
    """
    if orbit_radius_m <= 0:
        raise ValueError("orbit_radius_m must be positive")

    sx, sy, _ = wgs84_to_enu(start.lat, start.lon, start.alt_agl, subject.lat, subject.lon)
    start_angle = math.atan2(sy, sx)

    waypoints: List[WaypointInput] = [start]
    for i in range(num_orbit_control_points):
        theta = start_angle + (2.0 * math.pi * i) / max(num_orbit_control_points - 1, 1)
        ox = orbit_radius_m * math.cos(theta)
        oy = orbit_radius_m * math.sin(theta)
        lat, lon, alt = enu_to_wgs84(ox, oy, altitude_agl, subject.lat, subject.lon)
        waypoints.append(
            WaypointInput(
                lat=lat,
                lon=lon,
                alt_agl=alt,
                speed_ms=start.speed_ms,
            )
        )

    if end is not None:
        waypoints.append(end)
    return waypoints


def _ensure_min_control_points(coords: np.ndarray, min_points: int = 4) -> np.ndarray:
    """
    Ensure at least `min_points` distinct 3D control points exist so degree-3 (cubic)
    B-spline interpolation (`k=3`) always has `m > k`.
    """
    pts = [coords[0]]
    for i in range(1, len(coords)):
        if np.linalg.norm(coords[i] - pts[-1]) > 1e-4:
            pts.append(coords[i])
    if len(pts) == 1:
        # Degenerate identical points: create a small horizontal displacement
        pts.append(pts[0] + np.array([1.0, 0.0, 0.0]))

    arr = np.array(pts, dtype=float)
    while len(arr) < min_points:
        # Subdivide each segment at its midpoint
        expanded = [arr[0]]
        for i in range(len(arr) - 1):
            mid = 0.5 * (arr[i] + arr[i + 1])
            expanded.append(mid)
            expanded.append(arr[i + 1])
        arr = np.array(expanded, dtype=float)
    return arr


def interpolate_cubic_bspline_3d(
    control_points_enu: np.ndarray,
    num_samples: int = 60,
    smoothing: float = 0.0,
) -> np.ndarray:
    """
    Fit and evaluate a 3D cubic B-spline (degree k=3) through the ENU control points.
    Returns an array of shape (num_samples, 3).
    """
    pts = _ensure_min_control_points(control_points_enu, min_points=4)
    # Compute cumulative chord-length parameter u in [0, 1]
    diffs = np.diff(pts, axis=0)
    seg_lengths = np.linalg.norm(diffs, axis=1)
    cum_dist = np.concatenate([[0.0], np.cumsum(seg_lengths)])
    total_chord = cum_dist[-1]
    if total_chord < 1e-6:
        return np.repeat(pts[:1], num_samples, axis=0)

    u_params = cum_dist / total_chord
    tck, _ = splprep([pts[:, 0], pts[:, 1], pts[:, 2]], u=u_params, k=3, s=smoothing)
    u_eval = np.linspace(0.0, 1.0, num_samples)
    x_out, y_out, z_out = splev(u_eval, tck)
    return np.column_stack([x_out, y_out, z_out])


def _enforce_collision_buffers(
    points_enu: np.ndarray,
    obstacles: List[Obstacle3D],
    min_clearance_buffer_m: float,
    ref_lat: float,
    ref_lon: float,
) -> Tuple[np.ndarray, np.ndarray, List[str]]:
    """
    Enforce minimum ground AGL clearance and 3D obstacle clearance buffers.
    Returns (adjusted_points_enu, clearance_margins_m, warnings).
    """
    adjusted = points_enu.copy()
    warnings: List[str] = []
    num_pts = len(adjusted)
    clearances = np.zeros(num_pts, dtype=float)

    # 1. Enforce minimum ground clearance buffer
    min_ground_z = max(min_clearance_buffer_m, 1.0)
    low_ground_mask = adjusted[:, 2] < min_ground_z
    if np.any(low_ground_mask):
        warnings.append(
            f"Clamped trajectory altitude to minimum AGL safety buffer of {min_ground_z:.1f}m."
        )
        adjusted[low_ground_mask, 2] = min_ground_z

    # Convert obstacles to ENU
    obs_enu = []
    for obs in obstacles:
        ox, oy, _ = wgs84_to_enu(obs.lat, obs.lon, obs.height_agl, ref_lat, ref_lon)
        obs_enu.append((ox, oy, obs.radius_m, obs.height_agl, obs.label))

    # 2. Check and enforce 3D obstacle collision buffers
    for obs_idx, (ox, oy, radius_m, height_agl, label) in enumerate(obs_enu):
        min_obs_dist_detected = float("inf")
        pushed = False
        for i in range(num_pts):
            px, py, pz = adjusted[i]
            dx = px - ox
            dy = py - oy
            horiz_dist = math.hypot(dx, dy)
            horiz_clearance = horiz_dist - radius_m
            vert_clearance = pz - height_agl

            if pz <= height_agl + min_clearance_buffer_m and horiz_clearance < min_clearance_buffer_m:
                # Inside the obstacle's buffer zone
                actual_clearance = max(horiz_clearance, vert_clearance)
                min_obs_dist_detected = min(min_obs_dist_detected, actual_clearance)
                pushed = True

                # Push radially outward or vertically above the obstacle buffer
                required_horiz = radius_m + min_clearance_buffer_m + 0.25
                if horiz_dist > 1e-3:
                    scale = required_horiz / horiz_dist
                    adjusted[i, 0] = ox + dx * scale
                    adjusted[i, 1] = oy + dy * scale
                else:
                    adjusted[i, 0] = ox + required_horiz
                # Also lift altitude slightly if near top of canopy/roofline
                if vert_clearance > -min_clearance_buffer_m:
                    adjusted[i, 2] = max(adjusted[i, 2], height_agl + min_clearance_buffer_m + 0.25)

        if pushed:
            warnings.append(
                f"Trajectory passed within {max(0.0, min_obs_dist_detected):.1f}m of {label}; "
                f"rerouted to maintain {min_clearance_buffer_m:.1f}m safety buffer."
            )

    # Smooth slightly if obstacle push occurred while preserving endpoints and floor
    if obs_enu and len(warnings) > 0 and num_pts >= 5:
        start_pt = adjusted[0].copy()
        end_pt = adjusted[-1].copy()
        adjusted[:, 0] = gaussian_filter1d(adjusted[:, 0], sigma=1.0, mode="nearest")
        adjusted[:, 1] = gaussian_filter1d(adjusted[:, 1], sigma=1.0, mode="nearest")
        adjusted[:, 2] = np.maximum(
            gaussian_filter1d(adjusted[:, 2], sigma=1.0, mode="nearest"),
            min_ground_z,
        )
        adjusted[0] = start_pt
        adjusted[-1] = end_pt
        # Re-verify strict obstacle buffer after smoothing
        for ox, oy, radius_m, height_agl, _ in obs_enu:
            for i in range(num_pts):
                px, py, pz = adjusted[i]
                dx = px - ox
                dy = py - oy
                horiz_dist = math.hypot(dx, dy)
                if pz < height_agl + min_clearance_buffer_m and horiz_dist < radius_m + min_clearance_buffer_m:
                    required_horiz = radius_m + min_clearance_buffer_m + 0.1
                    if horiz_dist > 1e-3:
                        adjusted[i, 0] = ox + dx * (required_horiz / horiz_dist)
                        adjusted[i, 1] = oy + dy * (required_horiz / horiz_dist)
                    else:
                        adjusted[i, 0] = ox + required_horiz

    # Compute final clearance margin at every sample (min of ground AGL and 3D obstacle surface distance)
    for i in range(num_pts):
        px, py, pz = adjusted[i]
        c_margin = pz  # Ground clearance
        for ox, oy, radius_m, height_agl, _ in obs_enu:
            horiz_c = max(0.0, math.hypot(px - ox, py - oy) - radius_m)
            vert_c = max(0.0, pz - height_agl)
            if pz <= height_agl:
                obs_dist = horiz_c
            elif math.hypot(px - ox, py - oy) <= radius_m:
                obs_dist = vert_c
            else:
                obs_dist = math.hypot(horiz_c, vert_c)
            c_margin = min(c_margin, obs_dist)
        clearances[i] = c_margin

    return adjusted, clearances, warnings


def compute_smooth_gimbal_angles(
    points_enu: np.ndarray,
    subject_enu: Tuple[float, float, float],
    waypoint_pitch_overrides: Optional[List[Optional[float]]] = None,
    waypoint_heading_overrides: Optional[List[Optional[float]]] = None,
) -> Tuple[np.ndarray, np.ndarray]:
    """
    Compute smooth camera gimbal pitch and yaw angles (in degrees) across all trajectory points,
    tracking the target subject focal point with unwrapped angular smoothing.
    """
    sx, sy, sz = subject_enu
    num_pts = len(points_enu)

    dx = sx - points_enu[:, 0]
    dy = sy - points_enu[:, 1]
    dz = sz - points_enu[:, 2]
    horiz_dist = np.hypot(dx, dy)

    # Compass bearing: 0 deg = North (+y), 90 deg = East (+x)
    raw_yaw_rad = np.arctan2(dx, dy)
    unwrapped_yaw_rad = np.unwrap(raw_yaw_rad)

    # Pitch angle: negative when looking down at subject
    raw_pitch_deg = np.degrees(np.arctan2(dz, np.maximum(horiz_dist, 1e-3)))

    # Apply manual waypoint overrides if provided by interpolating across normalized index
    if waypoint_pitch_overrides and any(p is not None for p in waypoint_pitch_overrides):
        n_wp = len(waypoint_pitch_overrides)
        wp_u = np.linspace(0.0, 1.0, n_wp)
        sample_u = np.linspace(0.0, 1.0, num_pts)
        known_u = []
        known_p = []
        for idx, p_val in enumerate(waypoint_pitch_overrides):
            u_val = wp_u[idx]
            if p_val is not None:
                known_u.append(u_val)
                known_p.append(float(p_val))
            else:
                sample_idx = min(int(round(u_val * (num_pts - 1))), num_pts - 1)
                known_u.append(u_val)
                known_p.append(float(raw_pitch_deg[sample_idx]))
        raw_pitch_deg = np.interp(sample_u, known_u, known_p)

    if waypoint_heading_overrides and any(h is not None for h in waypoint_heading_overrides):
        n_wp = len(waypoint_heading_overrides)
        wp_u = np.linspace(0.0, 1.0, n_wp)
        sample_u = np.linspace(0.0, 1.0, num_pts)
        known_u = []
        known_h_rad = []
        for idx, h_val in enumerate(waypoint_heading_overrides):
            u_val = wp_u[idx]
            if h_val is not None:
                known_u.append(u_val)
                known_h_rad.append(math.radians(float(h_val)))
            else:
                sample_idx = min(int(round(u_val * (num_pts - 1))), num_pts - 1)
                known_u.append(u_val)
                known_h_rad.append(float(raw_yaw_rad[sample_idx]))
        unwrapped_known_h = np.unwrap(known_h_rad)
        unwrapped_yaw_rad = np.interp(sample_u, known_u, unwrapped_known_h)

    # Smooth both pitch and unwrapped yaw for cinematographic fluidity
    if num_pts >= 5:
        smooth_pitch_deg = gaussian_filter1d(raw_pitch_deg, sigma=1.5, mode="nearest")
        smooth_yaw_rad = gaussian_filter1d(unwrapped_yaw_rad, sigma=1.5, mode="nearest")
    else:
        smooth_pitch_deg = raw_pitch_deg
        smooth_yaw_rad = unwrapped_yaw_rad

    # Clamp gimbal pitch to hardware limits [-90, +30] deg and wrap yaw to [0, 360) deg
    clamped_pitch_deg = np.clip(smooth_pitch_deg, MIN_GIMBAL_PITCH_DEG, MAX_GIMBAL_PITCH_DEG)
    wrapped_yaw_deg = np.mod(np.degrees(smooth_yaw_rad), 360.0)
    return clamped_pitch_deg, wrapped_yaw_deg


def generate_trajectory(request: TrajectoryRequest) -> TrajectoryResult:
    """
    Generate a complete 3D cubic B-spline trajectory from a TrajectoryRequest,
    enforcing hard velocity clamps (15 m/s horizontal, 5 m/s vertical/ascent)
    and obstacle/ground safety buffers.
    """
    warnings: List[str] = []

    # Determine effective speed caps (strictly clamped to hardware/FAA safety limits)
    if request.desired_horizontal_speed_ms > MAX_HORIZONTAL_SPEED_MS:
        warnings.append(
            f"Requested horizontal speed {request.desired_horizontal_speed_ms:.1f} m/s exceeds limit; "
            f"clamped to {MAX_HORIZONTAL_SPEED_MS:.1f} m/s."
        )
    effective_horiz_limit = min(request.desired_horizontal_speed_ms, MAX_HORIZONTAL_SPEED_MS)

    if request.desired_vertical_speed_ms > MAX_ASCENT_SPEED_MS:
        warnings.append(
            f"Requested vertical speed {request.desired_vertical_speed_ms:.1f} m/s exceeds limit; "
            f"clamped to {MAX_ASCENT_SPEED_MS:.1f} m/s."
        )
    effective_vert_limit = min(request.desired_vertical_speed_ms, MAX_ASCENT_SPEED_MS)

    # Also check waypoint-level speed overrides
    wp_speeds = [wp.speed_ms for wp in request.waypoints if wp.speed_ms is not None]
    if wp_speeds:
        max_wp_speed = max(wp_speeds)
        if max_wp_speed > MAX_HORIZONTAL_SPEED_MS:
            warnings.append(
                f"Waypoint speed {max_wp_speed:.1f} m/s exceeds max horizontal limit; "
                f"clamped to {MAX_HORIZONTAL_SPEED_MS:.1f} m/s."
            )
        effective_horiz_limit = min(effective_horiz_limit, min(max_wp_speed, MAX_HORIZONTAL_SPEED_MS))

    # Expand orbital waypoints if orbit_radius_m is provided with <= 3 waypoints
    input_waypoints = list(request.waypoints)
    if request.orbit_radius_m is not None and len(input_waypoints) <= 3:
        orbit_alt = float(np.mean([wp.alt_agl for wp in input_waypoints]))
        input_waypoints = generate_orbit_waypoints(
            start=input_waypoints[0],
            subject=request.subject_focal_point,
            orbit_radius_m=request.orbit_radius_m,
            altitude_agl=orbit_alt,
            end=input_waypoints[-1] if len(input_waypoints) >= 2 else None,
        )

    ref_lat = request.subject_focal_point.lat
    ref_lon = request.subject_focal_point.lon

    # Convert input waypoints to local ENU coordinates
    wp_enu = np.array(
        [wgs84_to_enu(wp.lat, wp.lon, wp.alt_agl, ref_lat, ref_lon) for wp in input_waypoints],
        dtype=float,
    )

    # Interpolate 3D Cubic B-Spline
    spline_enu = interpolate_cubic_bspline_3d(
        control_points_enu=wp_enu,
        num_samples=request.num_samples,
        smoothing=0.0,
    )

    # Enforce ground and 3D obstacle collision buffers
    safe_enu, clearances_m, buffer_warnings = _enforce_collision_buffers(
        points_enu=spline_enu,
        obstacles=request.obstacles,
        min_clearance_buffer_m=request.min_clearance_buffer_m,
        ref_lat=ref_lat,
        ref_lon=ref_lon,
    )
    warnings.extend(buffer_warnings)

    # Compute smooth gimbal pitch & yaw transitions tracking the subject focal point
    subject_enu = wgs84_to_enu(
        request.subject_focal_point.lat,
        request.subject_focal_point.lon,
        request.subject_focal_point.alt_agl,
        ref_lat,
        ref_lon,
    )
    pitch_overrides = [wp.gimbal_pitch for wp in input_waypoints]
    heading_overrides = [wp.heading for wp in input_waypoints]
    gimbal_pitch_deg, gimbal_yaw_deg = compute_smooth_gimbal_angles(
        points_enu=safe_enu,
        subject_enu=subject_enu,
        waypoint_pitch_overrides=pitch_overrides,
        waypoint_heading_overrides=heading_overrides,
    )

    # Compute kinematics & timestamps with strict speed clamping
    num_pts = len(safe_enu)
    timestamps = np.zeros(num_pts, dtype=float)
    horiz_speeds = np.zeros(num_pts, dtype=float)
    vert_speeds = np.zeros(num_pts, dtype=float)
    total_speeds = np.zeros(num_pts, dtype=float)
    total_dist = 0.0

    for i in range(1, num_pts):
        dx = safe_enu[i, 0] - safe_enu[i - 1, 0]
        dy = safe_enu[i, 1] - safe_enu[i - 1, 1]
        dz = safe_enu[i, 2] - safe_enu[i - 1, 2]

        dh = math.hypot(dx, dy)
        d3d = math.sqrt(dh * dh + dz * dz)
        total_dist += d3d

        # Smooth ease-in / ease-out velocity factor in [0.6, 1.0] so transitions are gentle
        progress = i / max(num_pts - 1, 1)
        ease_factor = 0.65 + 0.35 * math.sin(math.pi * progress)

        target_h_speed = min(effective_horiz_limit * ease_factor, MAX_HORIZONTAL_SPEED_MS)
        target_v_speed = min(effective_vert_limit * ease_factor, MAX_ASCENT_SPEED_MS)

        dt_horiz = dh / max(target_h_speed, 1e-6)
        dt_vert = abs(dz) / max(target_v_speed, 1e-6)
        # Ensure strict compliance with hard limits (15.0 m/s horizontal, 5.0 m/s vertical)
        dt_hard_horiz = dh / MAX_HORIZONTAL_SPEED_MS
        dt_hard_vert = abs(dz) / MAX_ASCENT_SPEED_MS

        dt = max(dt_horiz, dt_vert, dt_hard_horiz, dt_hard_vert, 0.05)
        timestamps[i] = timestamps[i - 1] + dt

        h_spd = min(dh / dt, MAX_HORIZONTAL_SPEED_MS)
        v_spd = np.clip(dz / dt, -MAX_DESCENT_SPEED_MS, MAX_ASCENT_SPEED_MS)
        horiz_speeds[i] = h_spd
        vert_speeds[i] = float(v_spd)
        total_speeds[i] = math.hypot(h_spd, float(v_spd))

    # Build output points in WGS84 + local ENU
    points: List[InterpolatedPoint] = []
    for i in range(num_pts):
        lat, lon, alt_agl = enu_to_wgs84(
            safe_enu[i, 0],
            safe_enu[i, 1],
            safe_enu[i, 2],
            ref_lat,
            ref_lon,
        )
        points.append(
            InterpolatedPoint(
                index=i,
                timestamp_s=round(float(timestamps[i]), 4),
                lat=round(lat, 8),
                lon=round(lon, 8),
                alt_agl=round(alt_agl, 3),
                local_x_m=round(float(safe_enu[i, 0]), 3),
                local_y_m=round(float(safe_enu[i, 1]), 3),
                local_z_m=round(float(safe_enu[i, 2]), 3),
                horizontal_speed_ms=round(float(horiz_speeds[i]), 4),
                vertical_speed_ms=round(float(vert_speeds[i]), 4),
                total_speed_ms=round(float(total_speeds[i]), 4),
                gimbal_pitch_deg=round(float(gimbal_pitch_deg[i]), 3),
                gimbal_yaw_deg=round(float(gimbal_yaw_deg[i]), 3),
                clearance_margin_m=round(float(clearances_m[i]), 3),
            )
        )

    min_clearance = float(np.min(clearances_m))
    metrics = FlightMetrics(
        duration_seconds=round(float(timestamps[-1]), 2),
        total_distance_meters=round(float(total_dist), 2),
        max_altitude_agl=round(float(np.max(safe_enu[:, 2])), 2),
        min_altitude_agl=round(float(np.min(safe_enu[:, 2])), 2),
        max_horizontal_speed_ms=round(float(np.max(horiz_speeds)), 4),
        max_vertical_speed_ms=round(float(np.max(np.abs(vert_speeds))), 4),
        min_obstacle_clearance_m=round(min_clearance, 2),
        collision_buffer_respected=bool(min_clearance >= request.min_clearance_buffer_m - 1e-2),
    )

    return TrajectoryResult(
        points=points,
        metrics=metrics,
        subject_focal_point=request.subject_focal_point,
        warnings=warnings,
        disclaimer=MANDATORY_PIC_DISCLAIMER,
    )
