# Shipaton / RevenueCat readiness

This is an implementation boundary and submission checklist, not a claim that
subscriptions or Shipaton eligibility are complete.

## Current state (2026-09-19)

RevenueCat **is integrated on the web build**, status-and-paywall only:

- `chess-frontend/src/services/billingService.ts` - `@revenuecat/purchases-js`
  (Web SDK, v1.63) configured with `VITE_REVENUECAT_PUBLIC_API_KEY` (Web Billing
  public key, `rcb_sb_…` sandbox / `rcb_…` live) and the App
  User ID the backend hands out (`zw-user-<account id>`). Draws the
  dashboard-built paywall with `presentPaywall()`; "Manage subscription" opens
  `customerInfo.managementURL` because there is no Customer Center on web.
- `billing_api.py` - `GET /api/billing/status[?fresh=1]`: asks RevenueCat's
  REST API with `REVENUECAT_SECRET_KEY`, 60s cache, fail-closed. `is_pro(request)`
  is the one function a future feature limit should call.
- `SubscriptionSettings.tsx` - Settings > Subscription. Signed-in only, so a
  purchase is always tied to an account; guests must sign up first.
- Env: frontend `VITE_REVENUECAT_ENABLED|PUBLIC_API_KEY|ENTITLEMENT_ID|OFFERING_ID`
  (all public); backend `REVENUECAT_ENABLED|SECRET_KEY|ENTITLEMENT_ID|OFFERING_ID`
  (secret key never logged or served). Ids default to `zugzwang_pro`/`default`.
- Entitlement `zugzwang_pro`; offering `default` with packages
  `$rc_monthly`/`monthly`, `$rc_annual`/`yearly`, `$rc_lifetime`/`lifetime`.
- CSP (all three of `vite.config.ts`, `vercel.json`, `nginx.conf`, exact hosts,
  no wildcards, each one found by running the flow and reading the violation):
  `connect-src` + `api.revenuecat.com`, `e.revenue.cat`; `img-src` +
  `icons.pawwalls.com`; `script-src` + `js.stripe.com`; `frame-src` `'none'` →
  `js.stripe.com`. Nested Stripe/hCaptcha frames live inside Stripe's frame.
- **Verified end to end in sandbox on 2026-09-19** (`tools/verify/billing.mjs`,
  15/15): Free → See plans → hosted paywall with the three plans → Stripe
  sandbox checkout → test card → "Payment complete" → the component re-asks
  the server with `fresh=1` → REST answers `pro: true, product: yearly` →
  plan row reads Pro and "Manage subscription" (`managementURL`) appears.
- Tests: `test_billing_api.py` (14, no DB), `tools/verify/billing.mjs` (15, browser,
  makes a real sandbox purchase on a throwaway account each run).

Not done, deliberately: **nothing is gated on Pro yet** (paid limits still
need a founder decision), no webhooks (the 60s cache + `fresh=1` after
purchase covers status; add webhooks if instant revocation matters), no
guest purchases. Production needs a Web Billing key (`rcb_...`) with Stripe
connected in place of the Test Store key, `REVENUECAT_SECRET_KEY` on Render
and `VITE_REVENUECAT_PUBLIC_API_KEY` on Vercel - both set *before* the push.

Zugzwang's closed-beta access codes are invitation authorization, not paid
entitlements, and must not be presented as subscriptions.

## Submission checklist

- Re-check the current official Shipaton rules and deadline; this repository
  intentionally does not freeze externally changing eligibility requirements.
- Run the Post-Mortem demo end to end against the release candidate, with the
  real provider path and a documented fallback demonstration.
- Export the anonymized cohort funnel and a 20–50-position deeper grade audit.
- Confirm privacy copy covers first-party pseudonymous product events and the
  retention decision for `product_events` / `move_grade_audits`.
- Run the complete backend, frontend, browser, security, secret-scan, and
  migration gates in `CLAUDE.md` before merging to `master`.
- Verify production environment variables and migrations before the merge;
  pushing `master` deploys both services immediately.
