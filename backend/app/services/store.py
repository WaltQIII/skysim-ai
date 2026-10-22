"""
Google Cloud Firestore + In-Memory Fallback Repository Store for SkySim AI.

Implements Section 4 Database Schema on Google Cloud Firestore (serverless, zero-idle cost):
- Collection `users`: id (UUID/UID), email, plan (trial | pro_monthly | enterprise), created_at
- Collection `missions`: id (UUID), user_id, name, status, bounding_box, flight_metrics,
  export_urls, audit_report, pic_acknowledged, pic_acknowledged_at
- Collection `compliance_audit_logs`: immutable FAA Part 107 RPIC liability acceptance records

Automatically uses `google.cloud.firestore.Client` in GCP / Cloud Run environments (or when
injected in unit tests) and falls back to local in-memory storage when running offline without
GCP credentials.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID

from google.cloud import firestore

from app.models.schemas import (
    MissionRecord,
    UserPlan,
    UserRecord,
)

DEFAULT_DEMO_USER_ID = UUID("00000000-0000-4000-8000-000000000001")


class FirestoreDatabaseStore:
    def __init__(self, firestore_client: Optional[firestore.Client] = None) -> None:
        self._injected_client = firestore_client
        self.users: Dict[UUID, UserRecord] = {}
        self.missions: Dict[UUID, MissionRecord] = {}
        self.compliance_audit_logs: List[Dict[str, Any]] = []
        self._seed_default_user()

    def _get_firestore_client(self) -> Optional[firestore.Client]:
        if self._injected_client is not None:
            return self._injected_client
        force_mock = os.getenv("SKYSIM_USE_MOCK_FIRESTORE", "false").lower() in ("1", "true", "yes")
        has_gcp_creds = bool(os.getenv("GOOGLE_APPLICATION_CREDENTIALS") or os.getenv("K_SERVICE"))
        if has_gcp_creds and not force_mock:
            try:
                project_id = os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("GCP_PROJECT_ID")
                return firestore.Client(project=project_id)
            except Exception:
                return None
        return None

    def _seed_default_user(self) -> None:
        self.users[DEFAULT_DEMO_USER_ID] = UserRecord(
            id=DEFAULT_DEMO_USER_ID,
            email="pilot@skysim.ai",
            plan=UserPlan.PRO_MONTHLY,
            created_at=datetime.now(timezone.utc),
        )

    def upsert_user(self, user: UserRecord) -> UserRecord:
        self.users[user.id] = user
        fs = self._get_firestore_client()
        if fs is not None:
            fs.collection("users").document(str(user.id)).set(
                user.model_dump(mode="json"), merge=True
            )
        return user

    def get_user(self, user_id: UUID) -> Optional[UserRecord]:
        fs = self._get_firestore_client()
        if fs is not None:
            doc = fs.collection("users").document(str(user_id)).get()
            if doc.exists:
                data = doc.to_dict()
                if data:
                    return UserRecord(**data)
        return self.users.get(user_id)

    def get_user_by_email(self, email: str) -> Optional[UserRecord]:
        email_norm = email.strip().lower()
        for u in self.users.values():
            if u.email.lower() == email_norm:
                return u
        fs = self._get_firestore_client()
        if fs is not None:
            docs = fs.collection("users").where("email", "==", email_norm).limit(1).stream()
            for d in docs:
                data = d.to_dict()
                if data:
                    user = UserRecord(**data)
                    self.users[user.id] = user
                    return user
        return None

    def update_user_plan(self, user_id: UUID, new_plan: UserPlan, email: Optional[str] = None) -> UserRecord:
        user = self.get_user(user_id)
        if user is None:
            user = UserRecord(
                id=user_id,
                email=email or "customer@skysim.ai",
                plan=new_plan,
                created_at=datetime.now(timezone.utc),
            )
        else:
            user.plan = new_plan
            if email:
                user.email = email
        return self.upsert_user(user)

    def save_mission(self, mission: MissionRecord) -> MissionRecord:
        self.missions[mission.id] = mission
        fs = self._get_firestore_client()
        if fs is not None:
            fs.collection("missions").document(str(mission.id)).set(
                mission.model_dump(mode="json")
            )
        return mission

    def get_mission(self, mission_id: UUID) -> Optional[MissionRecord]:
        fs = self._get_firestore_client()
        if fs is not None:
            doc = fs.collection("missions").document(str(mission_id)).get()
            if doc.exists:
                data = doc.to_dict()
                if data:
                    return MissionRecord(**data)
        return self.missions.get(mission_id)

    def list_missions(self, user_id: Optional[UUID] = None) -> List[MissionRecord]:
        fs = self._get_firestore_client()
        if fs is not None:
            query: Any = fs.collection("missions")
            if user_id is not None:
                query = query.where("user_id", "==", str(user_id))
            results = []
            for doc in query.stream():
                data = doc.to_dict()
                if data:
                    results.append(MissionRecord(**data))
            if results:
                return results

        all_missions = list(self.missions.values())
        if user_id is not None:
            return [m for m in all_missions if m.user_id == user_id]
        return all_missions

    def log_pic_compliance_event(
        self,
        mission_id: UUID,
        user_id: UUID,
        pilot_name_or_cert: str,
        laanc_verified: bool,
        acknowledged_at: datetime,
    ) -> Dict[str, Any]:
        """Record an immutable FAA Part 107 RPIC liability acceptance log in Firestore."""
        entry = {
            "mission_id": str(mission_id),
            "user_id": str(user_id),
            "pilot_name_or_cert": pilot_name_or_cert,
            "laanc_verified": laanc_verified,
            "acknowledged_at": acknowledged_at.isoformat(),
            "regulatory_notice": "FAA 14 CFR Part 107 RPIC Sole Responsibility Acknowledged",
        }
        self.compliance_audit_logs.append(entry)
        fs = self._get_firestore_client()
        if fs is not None:
            fs.collection("compliance_audit_logs").document(
                f"{mission_id}_{int(acknowledged_at.timestamp())}"
            ).set(entry)
        return entry


db_store = FirestoreDatabaseStore()
