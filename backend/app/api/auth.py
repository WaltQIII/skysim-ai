"""
Firebase Authentication & User Session Router for SkySim AI.

Supports:
- Google Cloud / Firebase Auth ID token verification (`Authorization: Bearer <token>`).
- Deterministic local development auth fallback so local testing and CI work without
  live Firebase project credentials.
- Syncs authenticated user profile into the Google Cloud Firestore `users` collection.
"""

from __future__ import annotations

import hashlib
import os
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, status
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token
from pydantic import BaseModel, Field

from app.models.schemas import UserPlan, UserRecord
from app.services.store import DEFAULT_DEMO_USER_ID, db_store

router = APIRouter(prefix="/api/auth", tags=["auth"])


class FirebaseSyncRequest(BaseModel):
    email: str = Field(..., min_length=3)
    firebase_uid: Optional[str] = None
    id_token_jwt: Optional[str] = None
    plan: UserPlan = UserPlan.TRIAL


def _uid_to_uuid(uid_str: str) -> UUID:
    try:
        return UUID(uid_str)
    except ValueError:
        digest = hashlib.md5(uid_str.encode("utf-8")).hexdigest()
        return UUID(digest)


def get_current_user(
    authorization: Optional[str] = Header(default=None),
    x_firebase_email: Optional[str] = Header(default=None),
) -> UserRecord:
    """
    FastAPI dependency resolving the current authenticated user via Firebase ID Token
    (or defaulting to the seeded Pro operator in local dev mode when no header is sent).
    """
    if not authorization:
        user = db_store.get_user(DEFAULT_DEMO_USER_ID)
        if user:
            return user
        return db_store.upsert_user(
            UserRecord(id=DEFAULT_DEMO_USER_ID, email="pilot@skysim.ai", plan=UserPlan.PRO_MONTHLY)
        )

    parts = authorization.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Authorization header format. Expected 'Bearer <firebase_id_token>'.",
        )

    token = parts[1].strip()
    firebase_project_id = os.getenv("FIREBASE_PROJECT_ID") or os.getenv("GOOGLE_CLOUD_PROJECT")

    # Verify with Google OAuth2 / Firebase certs if configured in production
    if firebase_project_id and not token.startswith("dev-token-"):
        try:
            claims = id_token.verify_firebase_token(
                token,
                google_requests.Request(),
                audience=firebase_project_id,
            )
            uid = _uid_to_uuid(claims.get("sub") or claims.get("user_id", str(DEFAULT_DEMO_USER_ID)))
            email = claims.get("email", x_firebase_email or "pilot@skysim.ai")
            existing = db_store.get_user(uid)
            if existing:
                return existing
            return db_store.upsert_user(UserRecord(id=uid, email=email, plan=UserPlan.TRIAL))
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"Firebase ID token verification failed: {exc}",
            ) from exc

    # Local dev / test token handling ("dev-token-<uid>")
    uid = _uid_to_uuid(token.replace("dev-token-", ""))
    existing = db_store.get_user(uid)
    if existing:
        return existing
    return db_store.upsert_user(
        UserRecord(
            id=uid,
            email=x_firebase_email or "pilot@skysim.ai",
            plan=UserPlan.PRO_MONTHLY,
        )
    )


@router.get("/me", response_model=UserRecord)
def get_my_profile(current_user: UserRecord = Depends(get_current_user)) -> UserRecord:
    """Return the authenticated user's Firestore profile and active subscription tier."""
    return current_user


@router.post("/sync", response_model=UserRecord)
def sync_firebase_user(req: FirebaseSyncRequest) -> UserRecord:
    """Sync a Firebase Auth user login into the Google Cloud Firestore `users` collection."""
    existing = db_store.get_user_by_email(req.email)
    if existing:
        return existing
    uid = _uid_to_uuid(req.firebase_uid or req.email)
    new_user = UserRecord(
        id=uid,
        email=req.email.strip().lower(),
        plan=req.plan,
    )
    return db_store.upsert_user(new_user)
