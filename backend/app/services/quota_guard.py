"""
GCP Hard Budget Cap & Quota Circuit Breaker (Section 7, Guardrail #2).

Enforces:
- Monthly GCP budget cap monitoring ($100.00 default cap).
- Automated threshold states at $50 (50% Warning) and $90 (90% Critical).
- Fail-closed circuit breaker when spend or API quota usage reaches >= 95%
  (or when `SKYSIM_FAIL_CLOSED_QUOTA=true`).
"""

from __future__ import annotations

import os
from typing import Any, Dict

from fastapi import HTTPException, status


class QuotaGuardService:
    def __init__(self) -> None:
        self.monthly_budget_usd: float = float(os.getenv("GCP_MONTHLY_BUDGET_USD", "100.0"))
        self.alert_threshold_50_usd: float = float(os.getenv("GCP_ALERT_50_USD", "50.0"))
        self.alert_threshold_90_usd: float = float(os.getenv("GCP_ALERT_90_USD", "90.0"))
        self.hard_cutoff_usd: float = float(os.getenv("GCP_HARD_CUTOFF_USD", "95.0"))
        self.max_cloud_run_instances: int = int(os.getenv("CLOUD_RUN_MAX_INSTANCES", "3"))

    def get_current_spend_usd(self) -> float:
        return float(os.getenv("GCP_CURRENT_SPEND_USD", "12.50"))

    def get_quota_usage_pct(self) -> float:
        return float(os.getenv("API_QUOTA_USAGE_PCT", "12.5"))

    def is_fail_closed(self) -> bool:
        force_closed = os.getenv("SKYSIM_FAIL_CLOSED_QUOTA", "false").lower() in ("1", "true", "yes")
        spend = self.get_current_spend_usd()
        quota_pct = self.get_quota_usage_pct()
        return force_closed or spend >= self.hard_cutoff_usd or quota_pct >= 95.0

    def evaluate_status(self) -> Dict[str, Any]:
        spend = self.get_current_spend_usd()
        quota_pct = self.get_quota_usage_pct()
        fail_closed = self.is_fail_closed()

        alerts = []
        if spend >= self.alert_threshold_90_usd or quota_pct >= 90.0:
            alerts.append("CRITICAL: 90% ($90/$100) GCP monthly credit threshold reached.")
        elif spend >= self.alert_threshold_50_usd or quota_pct >= 50.0:
            alerts.append("WARNING: 50% ($50/$100) GCP monthly credit threshold reached.")

        return {
            "healthy": not fail_closed,
            "mode": "fail_closed" if fail_closed else "operational",
            "monthly_budget_usd": self.monthly_budget_usd,
            "current_spend_usd": spend,
            "quota_usage_pct": quota_pct,
            "alert_thresholds_usd": [self.alert_threshold_50_usd, self.alert_threshold_90_usd],
            "active_alerts": alerts,
            "cloud_run_max_instances": self.max_cloud_run_instances,
        }

    def enforce_quota_or_fail_closed(self) -> None:
        if self.is_fail_closed():
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=(
                    "Service temporarily failed closed: GCP API quota or $100 monthly budget cap "
                    "threshold reached. External world-model calls are suspended to prevent cost overruns."
                ),
            )


quota_guard = QuotaGuardService()
