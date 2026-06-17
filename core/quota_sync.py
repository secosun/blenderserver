"""Quota synchronization — subscription plan features → user quotas."""

from __future__ import annotations

import json
import logging

from core.db import AsyncDatabase

logger = logging.getLogger("blenderserver.billing")


async def sync_quotas_for_org(db: AsyncDatabase, org_id: str):
    """Ensure all members of an org have quotas matching the org's current plan."""
    org = await db.get_organization(org_id)
    if not org:
        logger.warning("sync_quotas_for_org: org %s not found", org_id)
        return

    sub = await db.get_subscription_for_org(org_id)
    plan = None
    if sub:
        plan = await db.get_plan(sub["plan_id"])

    if not plan:
        plan = await db.get_default_plan()

    if not plan:
        logger.warning("sync_quotas_for_org: no plan found for org %s", org_id)
        return

    features = _parse_features(plan.get("features_json") or plan.get("features", {}))
    concurrency = features.get("concurrency", 2)
    max_res = features.get("max_resolution", 4096)
    max_samp = features.get("max_samples", 512)
    max_tasks = features.get("max_tasks_per_month", -1)

    members = await db.get_org_members(org_id)
    for member in members:
        await db.update_user(
            member["user_id"],
            quota_concurrency=concurrency,
            quota_max_resolution=max_res,
            quota_max_samples=max_samp,
            quota_max_tasks_per_month=max_tasks,
        )

    logger.info(
        "Synced quotas for org %s (plan=%s): concurrency=%d, max_res=%d, max_samp=%d, max_tasks=%d",
        org_id, plan.get("slug", "?"), concurrency, max_res, max_samp, max_tasks,
    )


async def sync_quotas_for_user(db: AsyncDatabase, user_id: str):
    """Sync quotas for a single user by finding their org."""
    org = await db.get_organization_by_user(user_id)
    if org:
        await sync_quotas_for_org(db, org["id"])


def _parse_features(features: dict | str | None) -> dict:
    if features is None:
        return {}
    if isinstance(features, dict):
        return features
    if isinstance(features, str):
        try:
            return json.loads(features)
        except (json.JSONDecodeError, TypeError):
            return {}
    return {}
