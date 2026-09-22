"""
Billing: the server-side view of the `zugzwang_pro` entitlement.

The browser runs RevenueCat's Web SDK (`chess-frontend/src/services/
billingService.ts`) to show the paywall and take the payment. Nothing here
takes money. This module answers one question - *is this account Pro?* - by
asking RevenueCat's REST API with the secret key, so any feature limit that
ever keys on Pro trusts this answer and never the browser's copy of it
(docs/SHIPATON_READINESS.md, "likely integration seam").

    GET /api/billing/status            cached view, ~60s
    GET /api/billing/status?fresh=1    bypass the cache (after a purchase)

Guests are never Pro: a purchase is tied to `user:<id>`, so a guest is sent
to sign up first. The RevenueCat App User ID for an account is
`app_user_id_for(account_id)`; the frontend configures the SDK with exactly
the value this endpoint returns, so the two halves cannot drift.

Fail-closed: no secret key, a network error, or an unexpected payload all
answer `pro: false` with `verified: false`, so the UI can say "could not
check" instead of either lying or unlocking.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Request

from identity import account_id_of, identity_of
from api_responses import create_success_response

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/billing", tags=["billing"])

# Env: REVENUECAT_ENABLED ("false" switches verification off - everyone is
# Free, verified:false), REVENUECAT_SECRET_KEY (sk_..., never logged, never
# sent to the browser), REVENUECAT_ENTITLEMENT_ID, REVENUECAT_OFFERING_ID.
ENTITLEMENT = os.environ.get("REVENUECAT_ENTITLEMENT_ID") or "zugzwang_pro"
OFFERING_ID = os.environ.get("REVENUECAT_OFFERING_ID") or "default"
_RC_URL = "https://api.revenuecat.com/v1/subscribers/{app_user_id}"
_CACHE_TTL = 60.0

# What each plan includes. These are DISPLAYED, and only `profile_themes_visible`
# is enforced (profile_api.get_profile). No monthly counters exist yet, so
# the status payload calls this `limits_preview` and the UI must say "includes",
# never "N remaining". Enforce a number here only once something counts it.
FREE_LIMITS = {"monthly_reviews": 3, "monthly_corrections": 2, "profile_themes_visible": 1, "practice_retests": 2}
PRO_LIMITS = {"monthly_reviews": 40, "monthly_corrections": 12, "profile_themes_visible": "all", "practice_retests": 12}

# ponytail: per-process dict cache, fine for one Render instance. Move to
# Postgres (or accept RC webhooks) if there is ever more than one.
_cache: dict[str, tuple[float, dict]] = {}


def enabled() -> bool:
    return os.environ.get("REVENUECAT_ENABLED", "true").strip().lower() != "false"


def secret_key() -> str | None:
    return (os.environ.get("REVENUECAT_SECRET_KEY") or None) if enabled() else None


def app_user_id_for(account_id) -> str:
    """Stable, URL-safe RevenueCat App User ID for an account."""
    return f"zw-user-{account_id}"


def _parse_entitlement(payload: dict) -> dict:
    """Reduce RevenueCat's subscriber object to what the app needs."""
    ent = ((payload.get("subscriber") or {}).get("entitlements") or {}).get(ENTITLEMENT)
    if not ent:
        return {"pro": False, "expires_at": None, "product": None}
    expires = ent.get("expires_date")  # null for a lifetime purchase
    active = True
    if expires:
        until = datetime.fromisoformat(expires.replace("Z", "+00:00"))
        active = until > datetime.now(timezone.utc)
    return {"pro": active, "expires_at": expires, "product": ent.get("product_identifier")}


def fetch_entitlement(app_user_id: str) -> dict:
    """Ask RevenueCat. Never raises: an unverifiable answer is `pro: False`."""
    key = secret_key()
    if not key:
        return {"pro": False, "expires_at": None, "product": None, "verified": False}
    try:
        r = httpx.get(
            _RC_URL.format(app_user_id=app_user_id),
            headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
            timeout=8.0,
        )
        r.raise_for_status()
        return {**_parse_entitlement(r.json()), "verified": True}
    except Exception as e:  # noqa: BLE001 - any failure is "could not verify"
        logger.warning(f"⚠️ RevenueCat lookup failed for {app_user_id}: {e}")
        return {"pro": False, "expires_at": None, "product": None, "verified": False}


def entitlement_for(app_user_id: str, fresh: bool = False) -> dict:
    now = time.monotonic()
    hit = _cache.get(app_user_id)
    if hit and not fresh and now - hit[0] < _CACHE_TTL:
        return hit[1]
    result = fetch_entitlement(app_user_id)
    if result["verified"]:
        _cache[app_user_id] = (now, result)
    return result


def is_pro(request: Request) -> bool:
    """For any feature limit: the server's answer, never the client's.
    Fail-closed: RevenueCat unreachable reads as Free, same as billing_status."""
    account = account_id_of(identity_of(request))
    return account is not None and entitlement_for(app_user_id_for(account))["pro"]


def gate_findings(findings: list[dict], pro: bool) -> tuple[list[dict], list[dict]]:
    """
    The one enforced limit. Free sees the strongest theme in full; the rest
    come back as locked previews - label and counts, no evidence, no practice
    - so the UI can show what exists without inventing anything. Pro, or a
    list of one, is untouched. Nothing is deleted or rewritten: the rows are
    still there and come back whole the moment the entitlement does.
    """
    n = PRO_LIMITS["profile_themes_visible"] if pro else FREE_LIMITS["profile_themes_visible"]
    if n == "all" or len(findings) <= n:
        return findings, []
    locked = [{"theme": f["theme"], "label": f["label"], "evidence_count": f["evidence_count"],
               "games_count": f["games_count"]} for f in findings[n:]]
    return findings[:n], locked


@router.get("/status")
def billing_status(request: Request, fresh: bool = False):
    account = account_id_of(identity_of(request))
    base = {"configured": secret_key() is not None, "entitlement": ENTITLEMENT, "offering": OFFERING_ID,
            "limits_preview": {"free": FREE_LIMITS, "pro": PRO_LIMITS}}
    if account is None:
        return create_success_response(
            "Guest session",
            {**base, "signed_in": False, "app_user_id": None, "pro": False, "plan": "free", "verified": True,
             "expires_at": None, "product": None},
        )
    app_user_id = app_user_id_for(account)
    ent = entitlement_for(app_user_id, fresh)
    return create_success_response(
        "Billing status",
        {**base, "signed_in": True, "app_user_id": app_user_id, "plan": "pro" if ent["pro"] else "free", **ent},
    )
