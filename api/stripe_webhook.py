"""Stripe webhook handler + billing API endpoints."""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import text

from api.deps import get_current_user
from core.billing import create_checkout_session, create_portal_session
from core.config import settings
from core.db import AsyncDatabase
from core.quota_sync import sync_quotas_for_org
from models.schemas import (
    CheckoutSessionRequest,
    CheckoutSessionResponse,
    PlanResponse,
    SubscriptionResponse,
)

logger = logging.getLogger("blenderserver.billing")

router = APIRouter(prefix="/billing", tags=["billing"])


def _db(request: Request) -> AsyncDatabase:
    return request.app.state.task_manager.db


# ---------------------------------------------------------------------------
# Stripe Webhook
# ---------------------------------------------------------------------------


@router.post("/stripe-webhook")
async def stripe_webhook(request: Request):
    """Handle Stripe webhook events (no auth — signature-verified)."""
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")

    if not settings.stripe_enabled:
        raise HTTPException(status_code=501, detail="Stripe not configured")

    import stripe
    stripe.api_key = settings.stripe_secret_key

    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, settings.stripe_webhook_secret
        )
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid payload")
    except stripe.error.SignatureVerificationError:
        raise HTTPException(status_code=400, detail="Invalid signature")

    db = _db(request)
    handler = _EVENT_HANDLERS.get(event.type)
    if handler:
        try:
            await handler(event, db)
        except Exception as e:
            logger.error("Webhook handler failed for %s: %s", event.type, e)

    return {"received": True}


async def _handle_checkout_completed(event, db: AsyncDatabase):
    """checkout.session.completed — activate subscription."""
    session = event.data.object
    org_id = session.get("client_reference_id")
    if not org_id:
        logger.warning("checkout.session completed without client_reference_id")
        return

    subscription_id = session.get("subscription")
    if subscription_id:
        sub = await db.get_subscription_by_stripe_id(subscription_id)
        if sub:
            await db.update_subscription(sub["id"], stripe_subscription_id=subscription_id, status="active")
            await sync_quotas_for_org(db, org_id)


async def _handle_invoice_paid(event, db: AsyncDatabase):
    """invoice.paid — confirm subscription remains active."""
    invoice = event.data.object
    subscription_id = invoice.get("subscription")
    if subscription_id:
        sub = await db.get_subscription_by_stripe_id(subscription_id)
        if sub:
            period_end = invoice.get("period_end")
            if isinstance(period_end, (int, float)):
                import datetime
                dt = datetime.datetime.fromtimestamp(period_end, tz=datetime.timezone.utc)
                await db.update_subscription(sub["id"], status="active", current_period_end=dt.isoformat())


async def _handle_subscription_updated(event, db: AsyncDatabase):
    """customer.subscription.updated — sync plan/status changes."""
    stripe_sub = event.data.object
    sub_id = stripe_sub.get("id")
    sub = await db.get_subscription_by_stripe_id(sub_id)
    if not sub:
        return

    status = stripe_sub.get("status", "active")
    cancel_at_period_end = stripe_sub.get("cancel_at_period_end", False)

    updates = {"status": status, "cancel_at_period_end": cancel_at_period_end}

    # Check if plan changed
    items = stripe_sub.get("items", {}).get("data", [])
    if items and items[0].get("price", {}).get("id"):
        price_id = items[0]["price"]["id"]
        plans = await db.list_plans(public_only=False)
        for plan in plans:
            if plan.get("stripe_monthly_price_id") == price_id or plan.get("stripe_yearly_price_id") == price_id:
                updates["plan_id"] = plan["id"]
                break

    # Update billing interval
    if items and items[0].get("plan", {}).get("interval"):
        updates["billing_interval"] = items[0]["plan"]["interval"]

    # Period dates
    current_period_start = stripe_sub.get("current_period_start")
    current_period_end = stripe_sub.get("current_period_end")
    if isinstance(current_period_start, (int, float)):
        import datetime
        updates["current_period_start"] = datetime.datetime.fromtimestamp(
            current_period_start, tz=datetime.timezone.utc
        ).isoformat()
    if isinstance(current_period_end, (int, float)):
        import datetime
        updates["current_period_end"] = datetime.datetime.fromtimestamp(
            current_period_end, tz=datetime.timezone.utc
        ).isoformat()

    await db.update_subscription(sub["id"], **updates)
    await sync_quotas_for_org(db, sub["organization_id"])


async def _handle_subscription_deleted(event, db: AsyncDatabase):
    """customer.subscription.deleted — revert to free plan."""
    stripe_sub = event.data.object
    sub_id = stripe_sub.get("id")
    sub = await db.get_subscription_by_stripe_id(sub_id)
    if not sub:
        return

    default_plan = await db.get_default_plan()
    if default_plan:
        await db.update_subscription(
            sub["id"],
            status="canceled",
            plan_id=default_plan["id"],
            cancel_at_period_end=False,
        )
    else:
        await db.update_subscription(sub["id"], status="canceled", cancel_at_period_end=False)

    await sync_quotas_for_org(db, sub["organization_id"])


_EVENT_HANDLERS = {
    "checkout.session.completed": _handle_checkout_completed,
    "invoice.paid": _handle_invoice_paid,
    "customer.subscription.updated": _handle_subscription_updated,
    "customer.subscription.deleted": _handle_subscription_deleted,
}


# ---------------------------------------------------------------------------
# Billing API (user-facing)
# ---------------------------------------------------------------------------


@router.get("/plans", response_model=list[PlanResponse])
async def list_plans(request: Request):
    """List all public subscription plans."""
    db = _db(request)
    plans = await db.list_plans(public_only=True)
    return [
        PlanResponse(
            id=p["id"],
            name=p["name"],
            slug=p["slug"],
            description=p.get("description", ""),
            price_monthly_cents=p.get("price_monthly_cents", 0),
            price_yearly_cents=p.get("price_yearly_cents", 0),
            stripe_monthly_price_id=p.get("stripe_monthly_price_id"),
            stripe_yearly_price_id=p.get("stripe_yearly_price_id"),
            features=p.get("features", {}),
            is_public=bool(p.get("is_public", True)),
            sort_order=p.get("sort_order", 0),
        )
        for p in plans
    ]


@router.get("/subscription", response_model=SubscriptionResponse)
async def get_subscription(
    request: Request,
    current_user: Annotated[dict, Depends(get_current_user)],
):
    """Get the current user's organization subscription."""
    db = _db(request)
    org = await db.get_organization_by_user(current_user["id"])
    if not org:
        raise HTTPException(status_code=404, detail="No organization found")

    sub = await db.get_subscription_for_org(org["id"])
    if not sub:
        raise HTTPException(status_code=404, detail="No active subscription")

    plan = await db.get_plan(sub["plan_id"])

    return _format_subscription(sub, plan)


@router.get("/subscription/org/{org_id}", response_model=SubscriptionResponse)
async def get_org_subscription(
    org_id: str,
    request: Request,
    current_user: Annotated[dict, Depends(get_current_user)],
):
    """Get a specific organization's subscription."""
    db = _db(request)
    sub = await db.get_subscription_for_org(org_id)
    if not sub:
        raise HTTPException(status_code=404, detail="No active subscription")

    plan = await db.get_plan(sub["plan_id"])
    return _format_subscription(sub, plan)


@router.post("/create-checkout-session", response_model=CheckoutSessionResponse)
async def create_checkout(
    body: CheckoutSessionRequest,
    request: Request,
    current_user: Annotated[dict, Depends(get_current_user)],
):
    """Create a Stripe Checkout Session for upgrading/downgrading."""
    db = _db(request)
    org = await db.get_organization_by_user(current_user["id"])
    if not org:
        raise HTTPException(status_code=404, detail="No organization found")

    customer_id = org.get("stripe_customer_id") or ""
    if not customer_id:
        if not settings.stripe_enabled:
            # Dev mode: create a fake customer
            from core.billing import create_stripe_customer
            customer_id = await create_stripe_customer(
                current_user.get("email", ""),
                current_user.get("display_name", ""),
            )
            if customer_id:
                await db._execute(
                    text("UPDATE organizations SET stripe_customer_id = :cid WHERE id = :oid"),
                    {"cid": customer_id, "oid": org["id"]},
                )
        if not customer_id:
            raise HTTPException(status_code=400, detail="No Stripe customer ID. Contact support.")

    url = await create_checkout_session(
        price_id=body.price_id,
        customer_id=customer_id,
        org_id=org["id"],
        success_url=body.success_url,
        cancel_url=body.cancel_url,
        payment_method=body.payment_method,
    )
    return CheckoutSessionResponse(url=url)


@router.post("/create-portal-session", response_model=CheckoutSessionResponse)
async def create_portal(
    request: Request,
    current_user: Annotated[dict, Depends(get_current_user)],
):
    """Create a Stripe Customer Portal session for managing subscription."""
    if not settings.stripe_enabled:
        raise HTTPException(status_code=501, detail="Stripe not configured")

    db = _db(request)
    org = await db.get_organization_by_user(current_user["id"])
    if not org:
        raise HTTPException(status_code=404, detail="No organization found")

    customer_id = org.get("stripe_customer_id") or ""
    if not customer_id:
        raise HTTPException(status_code=400, detail="No Stripe customer ID")

    url = await create_portal_session(customer_id=customer_id)
    return CheckoutSessionResponse(url=url)


@router.post("/subscription/cancel")
async def cancel_subscription(
    request: Request,
    current_user: Annotated[dict, Depends(get_current_user)],
):
    """Cancel the current subscription at period end."""
    db = _db(request)
    org = await db.get_organization_by_user(current_user["id"])
    if not org:
        raise HTTPException(status_code=404, detail="No organization found")

    sub = await db.get_subscription_for_org(org["id"])
    if not sub:
        raise HTTPException(status_code=404, detail="No active subscription")

    await db.update_subscription(sub["id"], cancel_at_period_end=True)
    return {"ok": True, "message": "Subscription will be canceled at period end"}


@router.post("/subscription/reactivate")
async def reactivate_subscription(
    request: Request,
    current_user: Annotated[dict, Depends(get_current_user)],
):
    """Reactivate a subscription that was set to cancel."""
    db = _db(request)
    org = await db.get_organization_by_user(current_user["id"])
    if not org:
        raise HTTPException(status_code=404, detail="No organization found")

    sub = await db.get_subscription_for_org(org["id"])
    if not sub:
        raise HTTPException(status_code=404, detail="No active subscription")

    await db.update_subscription(sub["id"], cancel_at_period_end=False)
    return {"ok": True, "message": "Subscription reactivated"}


@router.get("/dev-checkout-complete")
async def dev_checkout_complete(
    request: Request,
    session_id: str = "",
    org_id: str = "",
):
    """Dev mode: simulate Stripe webhook after checkout."""
    if settings.stripe_enabled:
        raise HTTPException(status_code=400, detail="Only available in dev mode")
    if not org_id:
        raise HTTPException(status_code=400, detail="Missing org_id")

    db = _db(request)
    # Find the selected plan from the session_id (dev_price_{slug}_monthly/yearly)
    import re
    m = re.search(r"dev_price_(\w+)_(monthly|yearly)", session_id or "")
    plan_slug = m.group(1) if m else "free"
    interval = m.group(2) if m else "monthly"

    plans = await db.list_plans(public_only=False)
    plan = next((p for p in plans if p["slug"] == plan_slug), None)
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")

    # Create or update subscription
    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone.utc)
    sub = await db._fetchone(
        text("SELECT * FROM subscriptions WHERE organization_id = :oid"),
        {"oid": org_id},
    )
    if sub:
        await db._execute(
            text("""UPDATE subscriptions SET plan_id = :pid, status = 'active',
                    billing_interval = :interval, current_period_start = :cps,
                    current_period_end = :cpe, cancel_at_period_end = false,
                    updated_at = :now WHERE id = :sid"""),
            {"pid": plan["id"], "interval": interval,
             "cps": now.isoformat(),
             "cpe": (now + timedelta(days=30)).isoformat(),
             "now": now.isoformat(), "sid": sub["id"]},
        )
    else:
        import uuid
        sub_id = str(uuid.uuid4())
        await db._execute(
            text("""INSERT INTO subscriptions (id, organization_id, plan_id, status,
                    billing_interval, current_period_start, current_period_end, created_at, updated_at)
                    VALUES (:id, :oid, :pid, 'active', :interval, :cps, :cpe, :now, :now)"""),
            {"id": sub_id, "oid": org_id, "pid": plan["id"], "interval": interval,
             "cps": now.isoformat(), "cpe": (now + timedelta(days=30)).isoformat(),
             "now": now.isoformat()},
        )

    # Sync quotas
    from core.quota_sync import sync_quotas_for_org
    await sync_quotas_for_org(db, org_id)
    logger.info("Dev checkout complete: org=%s plan=%s", org_id, plan_slug)
    from fastapi.responses import RedirectResponse
    base = settings.cors_origins[0] if settings.cors_origins and settings.cors_origins[0] != "*" else "http://localhost:5173"
    return RedirectResponse(url=f"{base}/subscription?success=true")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _format_subscription(sub: dict, plan: dict | None = None) -> SubscriptionResponse:
    plan_resp = None
    if plan:
        plan_resp = PlanResponse(
            id=plan["id"],
            name=plan["name"],
            slug=plan["slug"],
            description=plan.get("description", ""),
            price_monthly_cents=plan.get("price_monthly_cents", 0),
            price_yearly_cents=plan.get("price_yearly_cents", 0),
            stripe_monthly_price_id=plan.get("stripe_monthly_price_id"),
            stripe_yearly_price_id=plan.get("stripe_yearly_price_id"),
            features=plan.get("features", {}),
            is_public=bool(plan.get("is_public", True)),
            sort_order=plan.get("sort_order", 0),
        )

    return SubscriptionResponse(
        id=sub["id"],
        organization_id=sub["organization_id"],
        plan_id=sub["plan_id"],
        plan=plan_resp,
        stripe_subscription_id=sub.get("stripe_subscription_id"),
        status=sub.get("status", "active"),
        billing_interval=sub.get("billing_interval", "monthly"),
        current_period_start=sub.get("current_period_start"),
        current_period_end=sub.get("current_period_end"),
        cancel_at_period_end=bool(sub.get("cancel_at_period_end", False)),
        created_at=sub["created_at"],
        updated_at=sub.get("updated_at", ""),
    )
