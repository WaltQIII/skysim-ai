"""Integration tests for GCS Direct Uploads, Missions API, Firestore, Firebase Auth, and Stripe Tax."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import time
from unittest.mock import MagicMock
from uuid import uuid4

from fastapi.testclient import TestClient

from app.api.uploads import generate_signed_upload_url
from app.main import app
from app.models.schemas import MANDATORY_PIC_DISCLAIMER, UserPlan, UserRecord
from app.services.store import FirestoreDatabaseStore

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


def test_firestore_persistence_with_mock_client():
    mock_fs = MagicMock()
    store = FirestoreDatabaseStore(firestore_client=mock_fs)

    user = UserRecord(id=uuid4(), email="firestore_pilot@skysim.ai", plan=UserPlan.PRO_MONTHLY)
    store.upsert_user(user)
    mock_fs.collection.assert_called_with("users")

    log_entry = store.log_pic_compliance_event(
        mission_id=uuid4(),
        user_id=user.id,
        pilot_name_or_cert="RPIC-FS-107",
        laanc_verified=True,
        acknowledged_at=datetime.now(timezone.utc),
    )
    assert log_entry["pilot_name_or_cert"] == "RPIC-FS-107"
    mock_fs.collection.assert_called_with("compliance_audit_logs")


def test_firebase_auth_sync_and_profile():
    sync_resp = client.post(
        "/api/auth/sync",
        json={
            "email": "miami_operator@skysim.ai",
            "firebase_uid": "firebase-uid-998877",
            "plan": "trial",
        },
    )
    assert sync_resp.status_code == 200
    synced = sync_resp.json()
    assert synced["email"] == "miami_operator@skysim.ai"
    assert synced["plan"] == "trial"

    me_resp = client.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer dev-token-{synced['id']}"},
    )
    assert me_resp.status_code == 200
    assert me_resp.json()["email"] == "miami_operator@skysim.ai"


def test_stripe_checkout_session_and_signed_webhook(monkeypatch):
    # 1. Create Stripe Checkout Session with txcd_10103001
    uid = str(uuid4())
    checkout_resp = client.post(
        "/api/billing/checkout-session",
        json={
            "user_id": uid,
            "customer_email": "enterprise@skysim.ai",
            "plan": "enterprise",
            "billing_state": "FL",
        },
    )
    assert checkout_resp.status_code == 200
    cs = checkout_resp.json()
    assert cs["session_id"].startswith("cs_test_")
    assert cs["stripe_tax_code"] == "txcd_10103001"
    assert cs["automatic_tax_enabled"] is True
    assert cs["amount_tax_cents"] == 0
    assert cs["amount_total_cents"] == 29900

    # 2. Verify Stripe Webhook with HMAC-SHA256 signature verification
    webhook_secret = "whsec_test_secret_key_12345"
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", webhook_secret)

    event_payload = json.dumps(
        {
            "id": "evt_test_123",
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "id": cs["session_id"],
                    "client_reference_id": uid,
                    "customer_email": "enterprise@skysim.ai",
                    "metadata": {
                        "user_id": uid,
                        "plan": "enterprise",
                        "stripe_tax_code": "txcd_10103001",
                    },
                }
            },
        }
    ).encode("utf-8")

    ts = str(int(time.time()))
    signed_str = f"{ts}.{event_payload.decode('utf-8')}".encode("utf-8")
    sig = hmac.new(webhook_secret.encode("utf-8"), signed_str, hashlib.sha256).hexdigest()
    stripe_sig_header = f"t={ts},v1={sig}"

    wh_resp = client.post(
        "/api/billing/stripe-webhook",
        content=event_payload,
        headers={"Stripe-Signature": stripe_sig_header},
    )
    assert wh_resp.status_code == 200
    wh_data = wh_resp.json()
    assert wh_data["status"] == "processed"
    assert wh_data["updated_plan"] == "enterprise"

    # Invalid signature must be rejected with 400
    bad_resp = client.post(
        "/api/billing/stripe-webhook",
        content=event_payload,
        headers={"Stripe-Signature": f"t={ts},v1=deadbeef"},
    )
    assert bad_resp.status_code == 400
