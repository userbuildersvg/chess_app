# Shipaton / RevenueCat readiness

This is an implementation boundary and submission checklist, not a claim that
subscriptions or Shipaton eligibility are complete.

## Current state

- RevenueCat is **not integrated**. There is no SDK, customer mapping,
  entitlement check, paywall, purchase, restore, or webhook handling.
- Zugzwang's closed-beta access codes are invitation authorization, not paid
  entitlements, and must not be presented as subscriptions.
- The Post-Mortem demo uses the real import, engine, diagnosis, alternative,
  fresh-practice, and completion paths. It contains no hardcoded PGN or result.

## Likely integration seam

If subscriptions enter scope, keep purchase-platform calls out of chess and
diagnosis services. Map the authenticated Zugzwang account ID to a RevenueCat
App User ID on the backend, normalize verified entitlement state behind one
small account/billing service, and let feature limits ask that service. A
single entitlement such as `zugzwang_pro` is enough until product policy
requires more; the exact paid limits still need a founder decision.

Before implementation, decide guest-to-account purchase transfer, expiry and
grace-period behavior, webhook verification/idempotency, restore purchases,
refunds, and whether web billing is eligible for the target Shipaton category.
Do not put RevenueCat secret keys in the Vite bundle.

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
