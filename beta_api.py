"""
`/api/beta/*` - the two routes a locked-out visitor is allowed to reach.

Both are exempt from the gate (`beta_gate.OPEN_PREFIXES`), which is the point:
this is the door. Everything else in the application is behind it.

WHAT THIS DELIBERATELY DOES NOT HAVE
------------------------------------
There is no route here that creates, lists, disables or expires a code. Those
live in `tools/beta_codes.py` and run on a machine with a database URL,
because an HTTP endpoint that mints invitations is an HTTP endpoint that ends
the beta - and "it requires an admin session" is the same sentence every
compromised admin panel could once have said. The operator has a shell; the
internet does not need a form.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

import beta_service
from beta_service import BetaError
from identity import identity_of
from rate_limit import client_ip, limit_beta_redeem, redeem_by_identity
from utils import create_success_response

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/beta", tags=["beta"])


class RedeemRequest(BaseModel):
    code: str


@router.get("/status")
def beta_status(request: Request):
    """
    Whether the caller may use the app, and whether the gate is on at all.

    Unauthenticated by necessity - it is the first question the page asks - and
    it answers only about the caller. It does not say how many codes exist,
    how many are left, or whether any particular code is real, because a
    status endpoint that leaks any of that is a free oracle bolted to the door.
    """
    identity = identity_of(request)
    required = beta_service.beta_required()
    return create_success_response("Beta status", {
        "beta_required": required,
        "has_access": (not required) or beta_service.has_access(identity),
        # So the landing page can say "you're signed in but this account has no
        # invitation" instead of showing a code box to somebody who already
        # tried one. Derived from the identity string, not from client input.
        "signed_in": identity.startswith("user:"),
    })


@router.post("/redeem", dependencies=[Depends(limit_beta_redeem)])
def redeem(payload: RedeemRequest, request: Request):
    """
    Spend an access code for whoever is asking.

    **There is no beneficiary parameter, and that is the security property.**
    The grant lands on `identity_of(request)`, which came from a signed cookie
    the server minted. A caller cannot redeem a code onto somebody else's
    identity, and cannot redeem one onto an identity the server never issued.

    Two rate-limit buckets, for two different attacks:

    * per IP (`limit_beta_redeem`), which bounds one machine working through
      the code space. With 40 bits of code and 10 attempts per 15 minutes, an
      exhaustive search takes longer than the universe has had.
    * per identity (`redeem_by_identity`), because the per-IP bucket does
      nothing against a distributed guesser, and an identity is the one thing
      a distributed attempt must still hold to receive the grant. It costs a
      real tester nothing: they type one code, once.

    Neither bucket distinguishes a wrong code from a right one, so a 429
    reveals only that this caller has been trying.
    """
    identity = identity_of(request)

    # Per-identity, on top of the per-IP dependency. Checked before the
    # database is touched so a flood costs a dict lookup rather than a row
    # lock.
    redeem_by_identity.check(identity)

    try:
        result = beta_service.redeem(payload.code, identity, ip=client_ip(request))
    except BetaError as e:
        raise HTTPException(status_code=e.status_code, detail=e.message)

    return create_success_response(
        "Access granted",
        {"has_access": True, "already_had_access": result["already"]},
    )
