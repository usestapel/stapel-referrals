# Changelog

All notable changes to stapel-referrals are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Pre-1.0 semver: **minor = breaking**, patch = compatible.

## [0.1.0] — unreleased

### Added
- `ReferralCode`: one primary personal code per user, minted lazily and
  collision-proof; vanity codes by `VANITY_CODES` policy (retired codes stay
  claimable); staff campaign codes with window and attribution cap; share
  link from `SHARE_URL_TEMPLATE`.
- `Attribution`: one per referee (unique constraint), first touch wins,
  self-referral refused, account-age and capture windows, opt-in guards
  `same_device` / `duplicate_contact` / dotted path; `POST claim/`.
- `RewardRule` (signup / first qualifying order / Nth order; beneficiary
  referrer, referee or both; caps per referrer per period, budgets in count
  and value, hold days) and the `Reward` ledger (pending → granted / rejected,
  granted → reversed), idempotent on `(rule, attribution, trigger, role)`,
  caps counted under the rule-row lock.
- Reward strategies `noop`, `billing_credit` (`billing.credit` /
  `billing.debit`, stapel-billing >= 0.25.0: keys `referrals:reward:<id>` and
  `:reverse`, `identity` from the sign-up fact, `user_unknown` /
  `user_inactive` reject, `idempotency_conflict` stays pending with an ERROR,
  a reversal of spent credits ends `reversed_partial` / `already_spent` and
  never records debt), `promo_code` (`promotions.codes.issue` with an
  idempotency key, `promotions.codes.revoke {code, reason}`; a redeemed code
  ends `already_spent`), dotted path for custom ones.
- **Error catalogs** `translations/errors.{ru,es}.json` for the 21 codes in
  `errors.py` (with the `.state.json` provenance sidecar), shipped in the
  wheel, and the per-language error reference `docs/errors.{en,ru,es}.md`;
  `tests/test_error_i18n.py` gates coverage, drift, placeholders, foreign
  keys and packaging.
- Sign-up carriage: `user.signed_up.signup_context` (stapel-auth >= 0.51.0)
  with `referral_code` / `referral_captured_at` attributes the account
  (source `signup`); `POST claim/` stays for older auth and guests, and both
  together make one attribution.
- API: `me/`, `me/code/`, `me/referrals/`, `me/rewards/`, `claim/`,
  `codes/{code}/`, staff rules, campaign codes, rewards ledger and
  transitions, stats, `staff/strategies/` (every registered strategy with
  the JSON Schema of its params; rule writes validate against it).
- Facts `referrals.attribution.created`, `referrals.reward.granted|rejected|reversed`;
  consumers of `user.signed_up`, `user.merged`, `orders.order.placed`,
  `orders.order.status_changed`; Functions `referrals.attribution.get`,
  `referrals.code.resolve`, `referrals.export`.
- GDPR owner `referrals` (export + erase; ledger retained), admin, beat,
  `referrals_housekeeping`, system checks E001–E005.
