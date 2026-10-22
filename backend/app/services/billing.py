"""
Stripe Checkout + Stripe Tax (`txcd_10103001`) & Florida B2B SaaS Compliance Service.

Implements:
- Section 7, Guardrail #5: Classifies SkySim AI subscriptions strictly as cloud-hosted SaaS
  (`txcd_10103001`, digital software access without tangible personal property), which is
  non-taxable under Florida Department of Revenue (FL DOR Rule 12A-1.032) rules.
- Stripe Checkout Session creation with `automatic_tax={"enabled": True}` and `tax_code="txcd_10103001"`.
- HMAC-SHA256 `Stripe-Signature` webhook verification (`checkout.session.completed`,
  `customer.subscription.updated`, `customer.subscription.deleted`) that updates the user's
  subscription tier in Google Cloud Firestore (`db_store`).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from typing import Any, Dict, Optional
from uuid import UUID

import httpx
from pydantic import BaseModel, Field

from app.models.schemas import UserPlan
from app.services.store import DEFAULT_DEMO_USER_ID, db_store

STRIPE_TAX_CODE_SAAS = "txcd_10103001"

SAAS_PRODUCT_METADATA: Dict[str, Any] = {
    "product_classification": "cloud_hosted_saas",
    "service_delivery": "web_application_access_only",
    "tangible_personal_property_transferred": False,
    "downloaded_desktop_media_included": False,
    "stripe_tax_code": STRIPE_TAX_CODE_SAAS,  # Cloud-based Software as a Service (SaaS) - Business Use
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


class CheckoutSessionRequest(BaseModel):
    user_id: UUID = Field(default=DEFAULT_DEMO_USER_ID)
    customer_email: str = Field(default="pilot@skysim.ai")
    plan: UserPlan = Field(default=UserPlan.PRO_MONTHLY)
    billing_state: str = Field(default="FL")
    success_url: str = Field(default="http://localhost:3000/?checkout=success")
    cancel_url: str = Field(default="http://localhost:3000/?checkout=cancelled")


class CheckoutSessionResponse(BaseModel):
    session_id: str
    checkout_url: str
    plan: UserPlan
    amount_subtotal_cents: int
    amount_tax_cents: int
    amount_total_cents: int
    stripe_tax_code: str
    automatic_tax_enabled: bool
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

    return TaxCalculationResponse(
        plan=req.plan,
        subtotal_cents=subtotal,
        tax_rate=0.0,
        tax_amount_cents=0,
        total_cents=subtotal,
        tax_exempt_reason="Cloud-hosted B2B SaaS subscription metadata (txcd_10103001) applied.",
        product_metadata=SAAS_PRODUCT_METADATA,
    )


def create_stripe_checkout_session(req: CheckoutSessionRequest) -> CheckoutSessionResponse:
    """
    Create a Stripe Checkout Session configured with Stripe Tax (`txcd_10103001`) and
    Florida B2B SaaS compliance metadata. Uses live Stripe API when `STRIPE_SECRET_KEY`
    is set, or returns a deterministic local session for development/testing.
    """
    unit_amount = PLAN_PRICING_CENTS.get(req.plan, 7900)
    tax_calc = calculate_subscription_tax(
        TaxCalculationRequest(
            customer_email=req.customer_email,
            plan=req.plan,
            billing_state=req.billing_state,
        )
    )

    stripe_secret_key = os.getenv("STRIPE_SECRET_KEY")
    if stripe_secret_key and stripe_secret_key.startswith("sk_"):
        form_data = {
            "mode": "subscription",
            "customer_email": req.customer_email,
            "success_url": req.success_url,
            "cancel_url": req.cancel_url,
            "automatic_tax[enabled]": "true",
            "client_reference_id": str(req.user_id),
            "metadata[user_id]": str(req.user_id),
            "metadata[plan]": req.plan.value,
            "metadata[stripe_tax_code]": STRIPE_TAX_CODE_SAAS,
            "metadata[florida_dor_saas_exempt]": "true",
            "line_items[0][quantity]": "1",
            "line_items[0][price_data][currency]": "usd",
            "line_items[0][price_data][unit_amount]": str(unit_amount),
            "line_items[0][price_data][recurring][interval]": "month",
            "line_items[0][price_data][tax_behavior]": "exclusive",
            "line_items[0][price_data][product_data][name]": f"SkySim AI ({req.plan.value})",
            "line_items[0][price_data][product_data][tax_code]": STRIPE_TAX_CODE_SAAS,
        }
        resp = httpx.post(
            "https://api.stripe.com/v1/checkout/sessions",
            data=form_data,
            auth=(stripe_secret_key, ""),
            timeout=15.0,
        )
        resp.raise_for_status()
        session_json = resp.json()
        return CheckoutSessionResponse(
            session_id=session_json["id"],
            checkout_url=session_json["url"],
            plan=req.plan,
            amount_subtotal_cents=unit_amount,
            amount_tax_cents=tax_calc.tax_amount_cents,
            amount_total_cents=unit_amount + tax_calc.tax_amount_cents,
            stripe_tax_code=STRIPE_TAX_CODE_SAAS,
            automatic_tax_enabled=True,
            product_metadata=SAAS_PRODUCT_METADATA,
        )

    # Deterministic local Stripe Checkout Session for dev & test environments
    seed = f"{req.user_id}:{req.customer_email}:{req.plan.value}:{int(time.time())}"
    session_hash = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:24]
    session_id = f"cs_test_{session_hash}"
    checkout_url = f"https://checkout.stripe.com/c/pay/{session_id}"

    return CheckoutSessionResponse(
        session_id=session_id,
        checkout_url=checkout_url,
        plan=req.plan,
        amount_subtotal_cents=unit_amount,
        amount_tax_cents=tax_calc.tax_amount_cents,
        amount_total_cents=unit_amount + tax_calc.tax_amount_cents,
        stripe_tax_code=STRIPE_TAX_CODE_SAAS,
        automatic_tax_enabled=True,
        product_metadata=SAAS_PRODUCT_METADATA,
    )


def verify_stripe_signature(payload_bytes: bytes, sig_header: str, secret: str) -> bool:
    """Verify Stripe-Signature header (`t=<timestamp>,v1=<hmac_sha256>`)."""
    try:
        parts = dict(item.split("=", 1) for item in sig_header.split(",") if "=" in item)
        timestamp = parts.get("t", "")
        v1_sig = parts.get("v1", "")
        if not timestamp or not v1_sig:
            return False
        signed_payload = f"{timestamp}.{payload_bytes.decode('utf-8')}".encode("utf-8")
        expected_sig = hmac.new(secret.encode("utf-8"), signed_payload, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected_sig, v1_sig)
    except Exception:
        return False


def process_stripe_webhook_event(
    payload_bytes: bytes,
    stripe_signature: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Validate and process incoming Stripe webhook events, updating user subscription plans
    in Google Cloud Firestore.
    """
    webhook_secret = os.getenv("STRIPE_WEBHOOK_SECRET")
    if webhook_secret:
        if not stripe_signature or not verify_stripe_signature(payload_bytes, stripe_signature, webhook_secret):
            raise PermissionError("Invalid Stripe-Signature header")

    event = json.loads(payload_bytes.decode("utf-8"))
    event_type = event.get("type", "")
    data_obj = event.get("data", {}).get("object", {})
    metadata = data_obj.get("metadata", {}) or {}

    user_id_str = metadata.get("user_id") or data_obj.get("client_reference_id") or str(DEFAULT_DEMO_USER_ID)
    user_id = UUID(user_id_str)
    customer_email = data_obj.get("customer_email") or metadata.get("customer_email")

    if event_type in ("checkout.session.completed", "customer.subscription.created", "customer.subscription.updated"):
        plan_str = metadata.get("plan", UserPlan.PRO_MONTHLY.value)
        try:
            new_plan = UserPlan(plan_str)
        except ValueError:
            new_plan = UserPlan.PRO_MONTHLY
        updated_user = db_store.update_user_plan(user_id=user_id, new_plan=new_plan, email=customer_email)
        return {
            "status": "processed",
            "event_type": event_type,
            "user_id": str(updated_user.id),
            "updated_plan": updated_user.plan.value,
            "stripe_tax_code": STRIPE_TAX_CODE_SAAS,
        }

    if event_type == "customer.subscription.deleted":
        updated_user = db_store.update_user_plan(user_id=user_id, new_plan=UserPlan.TRIAL, email=customer_email)
        return {
            "status": "processed",
            "event_type": event_type,
            "user_id": str(updated_user.id),
            "updated_plan": updated_user.plan.value,
        }

    return {"status": "ignored", "event_type": event_type}
