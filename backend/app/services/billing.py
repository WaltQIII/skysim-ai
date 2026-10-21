"""
Florida B2B SaaS Tax & Payment Compliance Service (Section 7, Guardrail #5).

Configures Stripe and Lemon Squeezy product metadata to classify SkySim AI subscriptions
strictly as cloud-hosted SaaS (digital software access without tangible personal property).
Under Florida Department of Revenue (FL DOR) rules (F.S. 212.05 / TAA 14A-010),
web-based SaaS access without downloaded desktop media or tangible personal property
is non-taxable in Florida.
"""

from __future__ import annotations

from typing import Any, Dict

from pydantic import BaseModel, Field

from app.models.schemas import UserPlan


SAAS_PRODUCT_METADATA: Dict[str, Any] = {
    "product_classification": "cloud_hosted_saas",
    "service_delivery": "web_application_access_only",
    "tangible_personal_property_transferred": False,
    "downloaded_desktop_media_included": False,
    "stripe_tax_code": "txcd_10103001",  # Cloud-based Software as a Service (SaaS) - Business Use
    "lemonsqueezy_tax_category": "saas_b2b_cloud",
    "florida_dor_saas_exempt": True,
    "florida_dor_ruling_ref": "FL DOR Rule 12A-1.032 / Cloud SaaS Non-Taxable Service",
}

PLAN_PRICING_CENTS: Dict[UserPlan, int] = {
    UserPlan.TRIAL: 0,
    UserPlan.PRO_MONTHLY: 7900,    # $79.00 / month
    UserPlan.ENTERPRISE: 29900,    # $299.00 / month
}


class TaxCalculationRequest(BaseModel):
    customer_email: str
    plan: UserPlan = UserPlan.PRO_MONTHLY
    billing_state: str = Field(..., min_length=2, max_length=32, description="Two-letter US state code, e.g., FL")
    billing_country: str = Field(default="US")
    postal_code: str = Field(default="33131")


class TaxCalculationResponse(BaseModel):
    plan: UserPlan
    subtotal_cents: int
    tax_rate: float
    tax_amount_cents: int
    total_cents: int
    tax_exempt_reason: str
    product_metadata: Dict[str, Any]


def calculate_subscription_tax(req: TaxCalculationRequest) -> TaxCalculationResponse:
    """
    Calculate subscription tax while strictly enforcing the Florida DOR cloud SaaS
    exemption so webhooks never erroneously bill or remit Florida sales tax on pure SaaS.
    """
    subtotal = PLAN_PRICING_CENTS.get(req.plan, 7900)
    state_norm = req.billing_state.strip().upper()
    country_norm = req.billing_country.strip().upper()

    if country_norm == "US" and state_norm in ("FL", "FLORIDA"):
        return TaxCalculationResponse(
            plan=req.plan,
            subtotal_cents=subtotal,
            tax_rate=0.0,
            tax_amount_cents=0,
            total_cents=subtotal,
            tax_exempt_reason=(
                "Exempt under Florida Department of Revenue rules: SkySim AI is pure "
                "cloud-hosted SaaS (digital software access without tangible personal property)."
            ),
            product_metadata=SAAS_PRODUCT_METADATA,
        )

    # Default non-FL handling (0.0 until nexus threshold configured in payment provider)
    return TaxCalculationResponse(
        plan=req.plan,
        subtotal_cents=subtotal,
        tax_rate=0.0,
        tax_amount_cents=0,
        total_cents=subtotal,
        tax_exempt_reason="Cloud-hosted B2B SaaS subscription metadata applied.",
        product_metadata=SAAS_PRODUCT_METADATA,
    )
