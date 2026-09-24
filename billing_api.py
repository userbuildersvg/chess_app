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

WHY THERE IS A `reason`
-----------------------
"Could not refresh subscription status just now" is the honest thing to show
a person, and a useless thing to debug from: a wrong key, the wrong project,
a timeout and an outage all produce it. Every unverified answer therefore
carries a short `reason` code - `not_configured`, `provider_key_rejected`,
`no_subscriber`, `rate_limited`, `provider_error`, `timeout`, `unreachable`
- and a successful lookup logs which entitlement keys RevenueCat returned
against the one being looked for. Codes and key NAMES only: no secret, no
token, no customer detail ever goes into a log line or the payload.
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
# A single blip should not read as "we cannot verify you". Retried only for
# failures that a second attempt can actually fix - never for a 4xx, which
# says the request itself is wrong and will be just as wrong again.
_ATTEMPTS = 2
_RETRY_PAUSE = 0.4
_TIMEOUT = 8.0

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


def _unverified(reason: str) -> dict:
    """Fail-closed answer, carrying why. Never Pro."""
    return {"pro": False, "expires_at": None, "product": None, "verified": False, "reason": reason}


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
        return _unverified("not_configured")

    last = _unverified("unreachable")
    for attempt in range(_ATTEMPTS):
        if attempt:
            time.sleep(_RETRY_PAUSE)
        try:
            r = httpx.get(
                _RC_URL.format(app_user_id=app_user_id),
                headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
                timeout=_TIMEOUT,
            )
        except Exception as e:  # noqa: BLE001 - transport failures are retryable
            name = type(e).__name__.lower()
            last = _unverified("timeout" if "timeout" in name else "unreachable")
            logger.warning(f"⚠️ RevenueCat unreachable for {app_user_id} (attempt {attempt + 1}): {name}")
            continue

        status = r.status_code
        if status == 200:
            payload = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
            keys = sorted(((payload.get("subscriber") or {}).get("entitlements") or {}).keys())
            # The line that settles an entitlement-id mismatch in one look.
            logger.info(f"💳 RevenueCat {app_user_id}: entitlements={keys or '[]'} looking_for={ENTITLEMENT}")
            return {**_parse_entitlement(payload), "verified": True, "reason": None}

        # 401/403 is a key or project problem; 404 is "this project has never
        # heard of this app user id". Neither improves on a second attempt,
        # and both are worth naming exactly because they look identical in the
        # UI and have completely different fixes.
        if status in (401, 403):
            # NOT the person's session - ours. This endpoint is only reached by
            # an authenticated request; a 401 here is RevenueCat refusing OUR
            # secret key. It was called `unauthorized` for one commit and that
            # read as "the user is signed out", which sent a debugging session
            # down the wrong path. The name is the fix.
            logger.error(f"⛔ RevenueCat rejected OUR secret key for {app_user_id}: HTTP {status} "
                         f"- check REVENUECAT_SECRET_KEY on this environment")
            return _unverified("provider_key_rejected")
        if status == 404:
            logger.warning(f"⚠️ RevenueCat has no subscriber {app_user_id} (HTTP 404)")
            return _unverified("no_subscriber")
        if status == 429:
            last = _unverified("rate_limited")
        else:
            last = _unverified("provider_error")
        logger.warning(f"⚠️ RevenueCat lookup failed for {app_user_id}: HTTP {status} (attempt {attempt + 1})")
        if status < 500 and status != 429:
            break  # a 4xx we did not name: still the request's fault, not luck
    return last


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
             "expires_at": None, "product": None, "reason": None},
        )
    app_user_id = app_user_id_for(account)
    ent = entitlement_for(app_user_id, fresh)
    return create_success_response(
        "Billing status",
        {**base, "signed_in": True, "app_user_id": app_user_id, "plan": "pro" if ent["pro"] else "free", **ent},
    )
