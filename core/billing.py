"""Billing helpers — plan seeding, Stripe customer/checkout helpers."""

from __future__ import annotations

import logging

from core.config import settings
from core.db import AsyncDatabase

logger = logging.getLogger("blenderserver.billing")

DEFAULT_PLANS = [
    {
        "name": "免费版",
        "slug": "free",
        "description": "个人用户免费使用，每月 10 次渲染",
        "price_monthly_cents": 0,
        "price_yearly_cents": 0,
        "features": {
            "concurrency": 1,
            "max_resolution": 2048,
            "max_samples": 256,
            "max_tasks_per_month": 10,
        },
        "is_public": True,
        "sort_order": 0,
        "stripe_monthly_price_id": None,
        "stripe_yearly_price_id": None,
    },
    {
        "name": "专业版",
        "slug": "pro",
        "description": "专业渲染，更高并发和分辨率",
        "price_monthly_cents": 2999,
        "price_yearly_cents": 29900,
        "features": {
            "concurrency": 5,
            "max_resolution": 8192,
            "max_samples": 1024,
            "max_tasks_per_month": 500,
        },
        "is_public": True,
        "sort_order": 1,
        "stripe_monthly_price_id": None,
        "stripe_yearly_price_id": None,
    },
    {
        "name": "企业版",
        "slug": "enterprise",
        "description": "无限渲染，高级支持和定制服务",
        "price_monthly_cents": 9999,
        "price_yearly_cents": 99900,
        "features": {
            "concurrency": 20,
            "max_resolution": 16384,
            "max_samples": 10000,
            "max_tasks_per_month": -1,
        },
        "is_public": True,
        "sort_order": 2,
        "stripe_monthly_price_id": None,
        "stripe_yearly_price_id": None,
    },
    {
        "name": "按需付费",
        "slug": "payg",
        "description": "按渲染次数付费，无月费",
        "price_monthly_cents": 0,
        "price_yearly_cents": 0,
        "features": {
            "concurrency": 2,
            "max_resolution": 4096,
            "max_samples": 512,
            "max_tasks_per_month": -1,
        },
        "is_public": True,
        "sort_order": 3,
        "stripe_monthly_price_id": None,
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


async def create_stripe_customer(email: str, name: str = "") -> str:
    """Create a Stripe Customer and return the customer ID.

    Returns empty string if Stripe is not configured.
    """
    if not settings.stripe_enabled:
        return ""
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
    price_id: str,
    customer_id: str,
    org_id: str,
    success_url: str = "",
    cancel_url: str = "",
) -> str:
    """Create a Stripe Checkout Session and return the URL."""
    import stripe
    stripe.api_key = settings.stripe_secret_key

    base_url = settings.cors_origins[0] if settings.cors_origins and settings.cors_origins[0] != "*" else "http://localhost:5173"

    session = stripe.checkout.Session.create(
        customer=customer_id,
        mode="subscription",
        line_items=[{"price": price_id, "quantity": 1}],
        client_reference_id=org_id,
        success_url=success_url or f"{base_url}/subscription?success=true",
        cancel_url=cancel_url or f"{base_url}/plans?canceled=true",
    )
    return session.url


async def create_portal_session(customer_id: str, return_url: str = "") -> str:
    """Create a Stripe Customer Portal session and return the URL."""
    import stripe
    stripe.api_key = settings.stripe_secret_key

    base_url = settings.cors_origins[0] if settings.cors_origins and settings.cors_origins[0] != "*" else "http://localhost:5173"

    session = stripe.billing_portal.Session.create(
        customer=customer_id,
        return_url=return_url or f"{base_url}/subscription",
    )
    return session.url
