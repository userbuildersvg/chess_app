"""
Billing entitlement resolution - the one thing a paid limit would trust.

    /tmp/chessapp/bin/python test_billing_api.py

No database, no network: RevenueCat's HTTP call is stubbed. Asserts that
`zugzwang_pro` is read correctly (active / expired / lifetime / absent), that
every failure mode answers `pro: False` and `verified: False` (fail-closed),
and that unverified answers are never cached.
"""

import os

os.environ.setdefault("DISABLE_LANGFLOW", "true")

import billing_api  # noqa: E402

passed = 0


def check(cond, label):
    global passed
    assert cond, label
    passed += 1
    print(f"  ok  {label}")


def subscriber(entitlement):
    ents = {"zugzwang_pro": entitlement} if entitlement is not None else {}
    return {"subscriber": {"entitlements": ents}}


class FakeResponse:
    def __init__(self, payload=None, status=200):
        self._payload, self.status_code = payload, status
        self.headers = {"content-type": "application/json"}

    def json(self):
        return self._payload


def stub(payload=None, status=200, boom=None):
    calls = []

    def get(url, headers, timeout):
        assert headers["Authorization"].startswith("Bearer "), "secret key sent as bearer"
        assert "zw-user-42" in url
        calls.append(url)
        if boom:
            raise boom
        return FakeResponse(payload, status)
    billing_api.httpx.get = get
    return calls


def stub_sequence(*responses):
    """Answer each attempt differently. Each item is a FakeResponse or an exception."""
    calls = []

    def get(url, headers, timeout):
        item = responses[min(len(calls), len(responses) - 1)]
        calls.append(url)
        if isinstance(item, BaseException):
            raise item
        return item
    billing_api.httpx.get = get
    return calls


print("parsing")
check(billing_api._parse_entitlement(subscriber(None))["pro"] is False, "no entitlement -> not pro")
check(billing_api._parse_entitlement(subscriber({"expires_date": None, "product_identifier": "lifetime"}))["pro"] is True,
      "lifetime (null expiry) -> pro")
check(billing_api._parse_entitlement(subscriber({"expires_date": "2099-01-01T00:00:00Z", "product_identifier": "yearly"}))["pro"] is True,
      "future expiry -> pro")
check(billing_api._parse_entitlement(subscriber({"expires_date": "2020-01-01T00:00:00Z", "product_identifier": "monthly"}))["pro"] is False,
      "past expiry -> not pro")
check(billing_api.app_user_id_for(42) == "zw-user-42", "app user id is stable and url-safe")

print("fail-closed")
os.environ.pop("REVENUECAT_SECRET_KEY", None)
r = billing_api.fetch_entitlement("zw-user-42")
check(r == {"pro": False, "expires_at": None, "product": None, "verified": False, "reason": "not_configured"},
      "no secret key -> unverified, not pro, and says which")

os.environ["REVENUECAT_SECRET_KEY"] = "sk_test_not_real"
stub(boom=ConnectionError("down"))
check(billing_api.fetch_entitlement("zw-user-42")["verified"] is False, "network error -> unverified")
stub(status=500)
check(billing_api.fetch_entitlement("zw-user-42")["verified"] is False, "HTTP 500 -> unverified")
stub(payload={"garbage": True})
r = billing_api.fetch_entitlement("zw-user-42")
check(r["pro"] is False and r["verified"] is True, "unexpected but valid payload -> verified, not pro")

print("why it could not verify")
# Every one of these looks identical in the UI and has a different fix, which
# is the whole reason the code exists.
for status, reason in [(401, "provider_key_rejected"), (403, "provider_key_rejected"), (404, "no_subscriber"),
                       (429, "rate_limited"), (500, "provider_error")]:
    stub(status=status)
    r = billing_api.fetch_entitlement("zw-user-42")
    check(r["verified"] is False and r["pro"] is False and r["reason"] == reason,
          f"HTTP {status} -> unverified, not pro, reason={reason}")
stub(boom=ConnectionError("down"))
check(billing_api.fetch_entitlement("zw-user-42")["reason"] == "unreachable", "transport failure -> unreachable")


class FakeTimeout(Exception):
    pass


FakeTimeout.__name__ = "ReadTimeout"
stub(boom=FakeTimeout("slow"))
check(billing_api.fetch_entitlement("zw-user-42")["reason"] == "timeout", "a timeout is named as one")
stub(payload=subscriber({"expires_date": None, "product_identifier": "lifetime"}))
check(billing_api.fetch_entitlement("zw-user-42")["reason"] is None, "a verified answer carries no reason")

print("retrying only what a retry can fix")
pro_body = FakeResponse(subscriber({"expires_date": None, "product_identifier": "lifetime"}))
calls = stub_sequence(ConnectionError("blip"), pro_body)
check(billing_api.fetch_entitlement("zw-user-42")["pro"] is True and len(calls) == 2,
      "one blip then success -> Pro, not a refresh warning")
calls = stub_sequence(FakeResponse(status=500), pro_body)
check(billing_api.fetch_entitlement("zw-user-42")["pro"] is True and len(calls) == 2, "a 500 is retried")
calls = stub_sequence(FakeResponse(status=401), pro_body)
check(billing_api.fetch_entitlement("zw-user-42")["verified"] is False and len(calls) == 1,
      "a rejected key is NOT retried - it would fail identically")
calls = stub_sequence(FakeResponse(status=404), pro_body)
check(billing_api.fetch_entitlement("zw-user-42")["verified"] is False and len(calls) == 1,
      "a missing subscriber is NOT retried")
calls = stub_sequence(ConnectionError("a"), ConnectionError("b"), pro_body)
check(billing_api.fetch_entitlement("zw-user-42")["verified"] is False and len(calls) == 2,
      "retries are capped - a dead provider does not hold the request open")

os.environ["REVENUECAT_ENABLED"] = "false"
check(billing_api.secret_key() is None, "REVENUECAT_ENABLED=false hides the key -> unverified, not pro")
os.environ["REVENUECAT_ENABLED"] = "true"

print("caching")
billing_api._cache.clear()
stub(boom=ConnectionError("down"))
billing_api.entitlement_for("zw-user-42")
check("zw-user-42" not in billing_api._cache, "unverified answer is not cached")
stub(status=401)
billing_api.entitlement_for("zw-user-42")
check("zw-user-42" not in billing_api._cache, "a rejected key is not cached either")
stub(payload=subscriber({"expires_date": None, "product_identifier": "lifetime"}))
check(billing_api.entitlement_for("zw-user-42")["pro"] is True, "verified pro answer")
stub(payload=subscriber(None))
check(billing_api.entitlement_for("zw-user-42")["pro"] is True, "cached for 60s")
check(billing_api.entitlement_for("zw-user-42", fresh=True)["pro"] is False, "fresh=True bypasses the cache")

print("profile gate")
F = [{"theme": "A", "label": "A", "evidence_count": 5, "games_count": 4, "representative": ["secret"]},
     {"theme": "B", "label": "B", "evidence_count": 3, "games_count": 3, "representative": ["secret"]},
     {"theme": "C", "label": "C", "evidence_count": 3, "games_count": 3, "representative": ["secret"]}]
vis, locked = billing_api.gate_findings(F, pro=False)
check([f["theme"] for f in vis] == ["A"], "free sees only the strongest theme in full")
check([l["theme"] for l in locked] == ["B", "C"], "the rest come back as locked previews")
check(all("representative" not in l for l in locked), "locked previews carry no evidence")
check(billing_api.gate_findings(F, pro=True) == (F, []), "pro sees everything, nothing locked")
check(billing_api.gate_findings(F[:1], pro=False) == (F[:1], []), "one theme: nothing to lock, nothing faked")
check(billing_api.gate_findings([], pro=False) == ([], []), "no themes: no locked cards")
check(billing_api.FREE_LIMITS["profile_themes_visible"] == 1 and billing_api.PRO_LIMITS["profile_themes_visible"] == "all", "limits say what the gate does")

print(f"\n{passed}/{passed} checks passed")
