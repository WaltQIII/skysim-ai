"""Unit tests for SkySim AI Trajectory Engine & Cubic B-Spline Math."""

from __future__ import annotations

import math

import numpy as np
import pytest

from app.models.schemas import (
    Obstacle3D,
    SubjectFocalPoint,
    TrajectoryRequest,
    WaypointInput,
)
from app.services.trajectory import (
    MAX_ASCENT_SPEED_MS,
    MAX_HORIZONTAL_SPEED_MS,
    enu_to_wgs84,
    generate_orbit_waypoints,
    generate_trajectory,
    wgs84_to_enu,
)


def test_wgs84_enu_roundtrip():
    ref_lat, ref_lon = 25.7617, -80.1918  # Miami, FL
    target_lat, target_lon, target_alt = 25.7632, -80.1895, 42.5

    x, y, z = wgs84_to_enu(target_lat, target_lon, target_alt, ref_lat, ref_lon)
    assert x > 0  # East
    assert y > 0  # North
    assert z == pytest.approx(42.5)

    lat_out, lon_out, alt_out = enu_to_wgs84(x, y, z, ref_lat, ref_lon)
    assert lat_out == pytest.approx(target_lat, abs=1e-7)
    assert lon_out == pytest.approx(target_lon, abs=1e-7)
    assert alt_out == pytest.approx(target_alt, abs=1e-6)


def test_cubic_bspline_interpolation_smoothness_and_endpoints():
    subject = SubjectFocalPoint(lat=25.7617, lon=-80.1918, alt_agl=5.0)
    waypoints = [
        WaypointInput(lat=25.7610, lon=-80.1925, alt_agl=20.0),
        WaypointInput(lat=25.7615, lon=-80.1910, alt_agl=30.0),
        WaypointInput(lat=25.7622, lon=-80.1912, alt_agl=35.0),
        WaypointInput(lat=25.7625, lon=-80.1922, alt_agl=25.0),
    ]
    req = TrajectoryRequest(
        waypoints=waypoints,
        subject_focal_point=subject,
        num_samples=80,
    )
    res = generate_trajectory(req)

    assert len(res.points) == 80
    # Endpoints match start and end waypoints closely
    assert res.points[0].lat == pytest.approx(waypoints[0].lat, abs=1e-5)
    assert res.points[0].lon == pytest.approx(waypoints[0].lon, abs=1e-5)
    assert res.points[0].alt_agl == pytest.approx(waypoints[0].alt_agl, abs=1e-2)

    assert res.points[-1].lat == pytest.approx(waypoints[-1].lat, abs=1e-5)
    assert res.points[-1].lon == pytest.approx(waypoints[-1].lon, abs=1e-5)
    assert res.points[-1].alt_agl == pytest.approx(waypoints[-1].alt_agl, abs=1e-2)

    # Verify C2/cubic smoothness: consecutive spatial steps should vary smoothly without spikes
    coords = np.array([[p.local_x_m, p.local_y_m, p.local_z_m] for p in res.points])
    step_lengths = np.linalg.norm(np.diff(coords, axis=0), axis=1)
    assert np.all(step_lengths > 0.0)
    assert np.max(step_lengths) / np.median(step_lengths) < 3.0


def test_two_waypoint_cubic_bspline_subdivision():
    """Even with only 2 input waypoints, cubic B-spline (k=3) must succeed via subdivision."""
    subject = SubjectFocalPoint(lat=28.5383, lon=-81.3792, alt_agl=4.0)
    waypoints = [
        WaypointInput(lat=28.5380, lon=-81.3798, alt_agl=18.0),
        WaypointInput(lat=28.5388, lon=-81.3785, alt_agl=28.0),
    ]
    req = TrajectoryRequest(waypoints=waypoints, subject_focal_point=subject, num_samples=40)
    res = generate_trajectory(req)
    assert len(res.points) == 40
    assert res.metrics.total_distance_meters > 100.0
    assert res.metrics.duration_seconds > 0.0


def test_hard_speed_clamps_horizontal_and_ascent():
    """Verify hard clamps: max horizontal speed <= 15.0 m/s and max ascent speed <= 5.0 m/s."""
    subject = SubjectFocalPoint(lat=25.7617, lon=-80.1918, alt_agl=5.0)
    # Steep climb from 15m to 135m AGL (+120m vertical ascent) over ~300m horizontal distance
    waypoints = [
        WaypointInput(lat=25.7605, lon=-80.1930, alt_agl=15.0, speed_ms=35.0),
        WaypointInput(lat=25.7615, lon=-80.1920, alt_agl=75.0, speed_ms=35.0),
        WaypointInput(lat=25.7628, lon=-80.1905, alt_agl=135.0, speed_ms=35.0),
    ]
    req = TrajectoryRequest(
        waypoints=waypoints,
        subject_focal_point=subject,
        num_samples=100,
        desired_horizontal_speed_ms=30.0,  # Exceeds 15 m/s limit
        desired_vertical_speed_ms=18.0,    # Exceeds 5 m/s limit
    )
    res = generate_trajectory(req)

    # Every single interpolated point must obey the hard limits
    for pt in res.points:
        assert pt.horizontal_speed_ms <= MAX_HORIZONTAL_SPEED_MS + 1e-6
        assert pt.vertical_speed_ms <= MAX_ASCENT_SPEED_MS + 1e-6
        assert abs(pt.vertical_speed_ms) <= MAX_ASCENT_SPEED_MS + 1e-6

    assert res.metrics.max_horizontal_speed_ms <= MAX_HORIZONTAL_SPEED_MS
    assert res.metrics.max_vertical_speed_ms <= MAX_ASCENT_SPEED_MS
    # Climbing 120m at max 5 m/s requires at least 24 seconds
    assert res.metrics.duration_seconds >= 24.0
    assert any("clamped" in w.lower() for w in res.warnings)


def test_gimbal_pitch_and_yaw_tracking_and_smoothness():
    """Verify smooth gimbal pitch and yaw transitions around a 360-degree orbit."""
    subject = SubjectFocalPoint(lat=25.7617, lon=-80.1918, alt_agl=5.0)
    start_wp = WaypointInput(lat=25.7621, lon=-80.1918, alt_agl=35.0)
    end_wp = WaypointInput(lat=25.7621, lon=-80.1918, alt_agl=35.0)

    req = TrajectoryRequest(
        waypoints=[start_wp, end_wp],
        subject_focal_point=subject,
        orbit_radius_m=45.0,
        num_samples=90,
    )
    res = generate_trajectory(req)

    pitches = np.array([p.gimbal_pitch_deg for p in res.points])
    yaws = np.array([p.gimbal_yaw_deg for p in res.points])

    # Drone is at 35m AGL looking at 5m AGL subject ~45m away -> pitch should be negative (~ -33 deg)
    assert np.all(pitches < -15.0)
    assert np.all(pitches >= -90.0)

    # Check angular step smoothness across the orbit (compensating for 360 wrap)
    yaw_diffs = np.abs(np.diff(yaws))
    wrapped_diffs = np.minimum(yaw_diffs, 360.0 - yaw_diffs)
    assert np.max(wrapped_diffs) < 15.0  # Smooth progression without sudden reversals


def test_collision_buffer_ground_and_3d_obstacle_avoidance():
    """Verify trajectory maintains collision buffer margin around 3D obstacles and ground."""
    subject = SubjectFocalPoint(lat=25.7617, lon=-80.1918, alt_agl=5.0)
    # Path directly from West to East through (25.7617, -80.1918)
    waypoints = [
        WaypointInput(lat=25.7617, lon=-80.1924, alt_agl=3.0),  # Below 6m ground buffer!
        WaypointInput(lat=25.7617, lon=-80.1918, alt_agl=14.0), # Directly through obstacle!
        WaypointInput(lat=25.7617, lon=-80.1912, alt_agl=18.0),
    ]
    obstacle = Obstacle3D(
        lat=25.7617,
        lon=-80.1918,
        radius_m=6.0,
        height_agl=20.0,
        label="northern tree canopy",
    )
    req = TrajectoryRequest(
        waypoints=waypoints,
        subject_focal_point=subject,
        min_clearance_buffer_m=6.0,
        obstacles=[obstacle],
        num_samples=60,
    )
    res = generate_trajectory(req)

    assert res.metrics.min_altitude_agl >= 6.0
    assert res.metrics.min_obstacle_clearance_m >= 6.0 - 0.05
    assert res.metrics.collision_buffer_respected is True
    assert any("northern tree canopy" in w for w in res.warnings)
