"""Billing helpers — plan seeding, Stripe customer/checkout helpers.

Dev mode: when ``STRIPE_SECRET_KEY`` is not set, all Stripe operations
are simulated (fake customer IDs, fake checkout sessions that auto-complete).
This lets the full billing flow work in development without a Stripe account.
"""

from __future__ import annotations

import logging
import uuid

from core.config import settings
from core.db import AsyncDatabase

logger = logging.getLogger("blenderserver.billing")

DEFAULT_PLANS = [
    {
        "name": "免费体验",
        "slug": "free",
        "description": "免费试用，了解出图质量",
        "price_monthly_cents": 0,
        "price_yearly_cents": 0,
        "features": {
            "concurrency": 1, "max_resolution": 2048,
            "max_samples": 256, "max_tasks_per_month": 10,
        },
        "is_public": True, "sort_order": 0,
        "stripe_monthly_price_id": None, "stripe_yearly_price_id": None,
    },
    {
        "name": "基础套餐",
        "slug": "starter",
        "description": "每月 200 张渲染，适合小型门窗厂",
        "price_monthly_cents": 500000,
        "price_yearly_cents": 5000000,
        "features": {
            "concurrency": 3, "max_resolution": 4096,
            "max_samples": 512, "max_tasks_per_month": 200,
        },
        "is_public": True, "sort_order": 1,
        "stripe_monthly_price_id": "dev_price_starter_monthly",
        "stripe_yearly_price_id": "dev_price_starter_yearly",
    },
    {
        "name": "专业套餐",
        "slug": "pro",
        "description": "每月 500 张渲染，适合中型门窗厂",
        "price_monthly_cents": 980000,
        "price_yearly_cents": 9800000,
        "features": {
            "concurrency": 5, "max_resolution": 8192,
            "max_samples": 1024, "max_tasks_per_month": 500,
        },
        "is_public": True, "sort_order": 2,
        "stripe_monthly_price_id": "dev_price_pro_monthly",
        "stripe_yearly_price_id": "dev_price_pro_yearly",
    },
    {
        "name": "充值包",
        "slug": "payg",
        "description": "一次性购买 100 张渲染额度，长期有效",
        "price_monthly_cents": 500000,
        "price_yearly_cents": 0,
        "features": {
            "concurrency": 2, "max_resolution": 4096,
            "max_samples": 512, "max_tasks_per_month": 100,
        },
        "is_public": True, "sort_order": 3,
        "stripe_monthly_price_id": "dev_price_payg_monthly",
        "stripe_yearly_price_id": None,
    },
]


async def seed_plans(db: AsyncDatabase):
    """Insert default plans if the subscription_plans table is empty."""
    existing = await db.list_plans(public_only=False)
    if existing:
        logger.info("Plans already seeded (%d plans found)", len(existing))
        return
    for plan in DEFAULT_PLANS:
        await db.create_plan(**plan)
    logger.info("Seeded %d default plans", len(DEFAULT_PLANS))


# ---------------------------------------------------------------------------
# Dev mode helpers
# ---------------------------------------------------------------------------

_DEV_CUSTOMER_COUNTER = 0


def _is_dev_mode() -> bool:
    return not settings.stripe_enabled


async def create_stripe_customer(email: str, name: str = "") -> str:
    """Create a Stripe Customer (or simulated in dev mode)."""
    global _DEV_CUSTOMER_COUNTER
    if not settings.stripe_enabled:
        _DEV_CUSTOMER_COUNTER += 1
        cust_id = f"dev_cus_{_DEV_CUSTOMER_COUNTER:06d}"
        logger.info("Dev mode: created fake Stripe customer %s for %s", cust_id, email)
        return cust_id
    try:
        import stripe
        stripe.api_key = settings.stripe_secret_key
        customer = stripe.Customer.create(email=email, name=name or email)
        logger.info("Created Stripe customer %s for %s", customer.id, email)
        return customer.id
    except Exception as e:
        logger.warning("Failed to create Stripe customer: %s", e)
        return ""


async def create_checkout_session(
    price_id: str, customer_id: str, org_id: str,
    success_url: str = "", cancel_url: str = "",
    payment_method: str = "stripe",
) -> str:
    """Create a Stripe Checkout Session (or simulated in dev mode)."""
    if _is_dev_mode():
        logger.info("Dev mode: simulated checkout for price=%s customer=%s method=%s", price_id, customer_id, payment_method)
        base = settings.cors_origins[0] if settings.cors_origins and settings.cors_origins[0] != "*" else "http://localhost:5173"
        token = uuid.uuid4().hex[:12]
        return f"{base}/api/billing/dev-checkout-complete?session_id=dev_{token}&org_id={org_id}&method={payment_method}"
    import stripe
    stripe.api_key = settings.stripe_secret_key
    base_url = settings.cors_origins[0] if settings.cors_origins and settings.cors_origins[0] != "*" else "http://localhost:5173"
    session = stripe.checkout.Session.create(
        customer=customer_id, mode="subscription",
        line_items=[{"price": price_id, "quantity": 1}],
        client_reference_id=org_id,
        success_url=success_url or f"{base_url}/subscription?success=true",
        cancel_url=cancel_url or f"{base_url}/plans?canceled=true",
    )
    return session.url


async def create_portal_session(customer_id: str, return_url: str = "") -> str:
    """Create a Stripe Customer Portal session (or simulated in dev mode)."""
    if _is_dev_mode():
        return return_url or "/subscription"
    import stripe
    stripe.api_key = settings.stripe_secret_key
    base_url = settings.cors_origins[0] if settings.cors_origins and settings.cors_origins[0] != "*" else "http://localhost:5173"
    session = stripe.billing_portal.Session.create(
        customer=customer_id,
        return_url=return_url or f"{base_url}/subscription",
    )
    return session.url
