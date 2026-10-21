"""Integration tests for GCS Direct Uploads, Missions API, Fail-Closed Quota, and FL SaaS Tax."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from app.api.uploads import generate_signed_upload_url
from app.main import app
from app.models.schemas import MANDATORY_PIC_DISCLAIMER

client = TestClient(app)


def test_generate_signed_upload_url_with_gcs_sdk_mock():
    mock_storage_client = MagicMock()
    mock_bucket = MagicMock()
    mock_blob = MagicMock()
    mock_blob.generate_signed_url.return_value = "https://storage.googleapis.com/skysim-bucket/photo.jpg?X-Goog-Signature=mock123"
    mock_bucket.blob.return_value = mock_blob
    mock_storage_client.bucket.return_value = mock_bucket

    url = generate_signed_upload_url(
        bucket_name="skysim-bucket",
        blob_name="missions/123/photo.jpg",
        expiration=15,
        content_type="image/jpeg",
        storage_client=mock_storage_client,
    )

    assert url == "https://storage.googleapis.com/skysim-bucket/photo.jpg?X-Goog-Signature=mock123"
    mock_storage_client.bucket.assert_called_once_with("skysim-bucket")
    mock_bucket.blob.assert_called_once_with("missions/123/photo.jpg")
    mock_blob.generate_signed_url.assert_called_once_with(
        version="v4",
        expiration=timedelta(minutes=15),
        method="PUT",
        content_type="image/jpeg",
    )


def test_generate_signed_upload_url_local_fallback():
    url = generate_signed_upload_url(
        bucket_name="skysim-ai-raw-uploads",
        blob_name="missions/demo/site_north.jpg",
        expiration=15,
    )
    assert url.startswith("https://storage.googleapis.com/skysim-ai-raw-uploads/missions/demo/site_north.jpg")
    assert "X-Goog-Algorithm=GOOG4-RSA-SHA256" in url
    assert "X-Goog-Expires=900" in url


def test_mission_lifecycle_and_pic_acknowledgment_shield():
    payload = {
        "name": "742 Evergreen Terrace Luxury Orbit",
        "bounding_box": {
            "min_lat": 25.7610,
            "min_lon": -80.1925,
            "max_lat": 25.7625,
            "max_lon": -80.1910,
        },
        "photo_filenames": ["front_elevation.jpg", "roofline_north.jpg"],
        "orbit_radius_m": 40.0,
    }
    resp = client.post("/api/missions/create", json=payload)
    assert resp.status_code == 201
    mission = resp.json()
    mission_id = mission["id"]

    assert mission["name"] == "742 Evergreen Terrace Luxury Orbit"
    assert mission["status"] == "ready"
    assert len(mission["signed_upload_urls"]) == 2
    assert mission["signed_upload_urls"][0]["method"] == "PUT"
    assert mission["audit_report"]["safety_score"] > 0
    assert mission["audit_report"]["cinematic_score"] > 0
    assert mission["pic_acknowledged"] is False
    assert mission["pic_acknowledged_at"] is None

    # Attempting to export .kml, .kmz, or .plan BEFORE PIC acknowledgment must be blocked (403)
    for fmt in ("kml", "kmz", "plan"):
        exp_blocked = client.get(f"/api/missions/{mission_id}/export/{fmt}")
        assert exp_blocked.status_code == 403

    # Acknowledge PIC Part 107 liability shield
    ack_resp = client.post(
        f"/api/missions/{mission_id}/acknowledge-pic",
        json={
            "acknowledged": True,
            "pilot_name_or_cert": "RPIC-4491028",
            "laanc_verified": True,
        },
    )
    assert ack_resp.status_code == 200
    acked_mission = ack_resp.json()
    assert acked_mission["pic_acknowledged"] is True
    assert acked_mission["pic_acknowledged_at"] is not None

    # Now exports succeed and include the mandatory FAA Part 107 disclaimer
    kml_resp = client.get(f"/api/missions/{mission_id}/export/kml")
    assert kml_resp.status_code == 200
    assert MANDATORY_PIC_DISCLAIMER in kml_resp.text

    plan_resp = client.get(f"/api/missions/{mission_id}/export/plan")
    assert plan_resp.status_code == 200
    assert MANDATORY_PIC_DISCLAIMER in plan_resp.text

    kmz_resp = client.get(f"/api/missions/{mission_id}/export/kmz")
    assert kmz_resp.status_code == 200
    assert len(kmz_resp.content) > 200


def test_health_check_and_fail_closed_quota(monkeypatch):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json()["healthy"] is True
    assert resp.json()["cloud_run_max_instances"] == 3

    monkeypatch.setenv("SKYSIM_FAIL_CLOSED_QUOTA", "true")
    resp_closed = client.get("/api/health")
    assert resp_closed.status_code == 503
    assert resp_closed.json()["healthy"] is False
    assert resp_closed.json()["mode"] == "fail_closed"


def test_florida_b2b_saas_tax_exemption():
    resp = client.post(
        "/api/billing/calculate-tax",
        json={
            "customer_email": "aerial@miamirealestate.com",
            "plan": "pro_monthly",
            "billing_state": "FL",
            "billing_country": "US",
            "postal_code": "33131",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["tax_rate"] == 0.0
    assert data["tax_amount_cents"] == 0
    assert data["total_cents"] == data["subtotal_cents"]
    assert data["product_metadata"]["product_classification"] == "cloud_hosted_saas"
    assert data["product_metadata"]["tangible_personal_property_transferred"] is False
    assert data["product_metadata"]["florida_dor_saas_exempt"] is True
