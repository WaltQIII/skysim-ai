"""Billing & Florida B2B SaaS Tax Compliance API Router."""

from __future__ import annotations

from fastapi import APIRouter

from app.services.billing import (
    SAAS_PRODUCT_METADATA,
    TaxCalculationRequest,
    TaxCalculationResponse,
    calculate_subscription_tax,
)

router = APIRouter(prefix="/api/billing", tags=["billing"])


@router.get("/metadata")
def get_saas_billing_metadata():
    """Return Stripe / Lemon Squeezy product metadata for Florida B2B SaaS compliance."""
    return SAAS_PRODUCT_METADATA


@router.post("/calculate-tax", response_model=TaxCalculationResponse)
def calculate_tax_webhook(req: TaxCalculationRequest) -> TaxCalculationResponse:
    """Webhook endpoint calculating subscription tax with Florida SaaS exemption enforcement."""
    return calculate_subscription_tax(req)
