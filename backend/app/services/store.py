"""
In-memory & Firestore-compatible Repository Store for SkySim AI.

Implements Section 4 Database Schema:
- `users`: id (UUID), email (string), plan (trial | pro_monthly | enterprise), created_at
- `missions`: id (UUID), user_id (UUID), name, status, bounding_box, flight_metrics,
  export_urls, audit_report, pic_acknowledged_at
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Optional
from uuid import UUID, uuid4

from app.models.schemas import (
    MissionRecord,
    UserPlan,
    UserRecord,
)


class InMemoryDatabaseStore:
    def __init__(self) -> None:
        self.users: Dict[UUID, UserRecord] = {}
        self.missions: Dict[UUID, MissionRecord] = {}
        self._seed_default_user()

    def _seed_default_user(self) -> None:
        default_uid = UUID("00000000-0000-4000-8000-000000000001")
        self.users[default_uid] = UserRecord(
            id=default_uid,
            email="pilot@skysim.ai",
            plan=UserPlan.PRO_MONTHLY,
            created_at=datetime.now(timezone.utc),
        )

    def save_mission(self, mission: MissionRecord) -> MissionRecord:
        self.missions[mission.id] = mission
        return mission

    def get_mission(self, mission_id: UUID) -> Optional[MissionRecord]:
        return self.missions.get(mission_id)

    def list_missions(self) -> List[MissionRecord]:
        return list(self.missions.values())


db_store = InMemoryDatabaseStore()
