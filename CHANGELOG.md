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
  `billing.debit`), `promo_code` (`promotions.codes.issue`, stapel-promotions >= 0.1.0),
  dotted path for custom ones.
- API: `me/`, `me/code/`, `me/referrals/`, `me/rewards/`, `claim/`,
  `codes/{code}/`, staff rules, campaign codes, rewards ledger and
  transitions, stats.
- Facts `referrals.attribution.created`, `referrals.reward.granted|rejected|reversed`;
  consumers of `user.signed_up`, `user.merged`, `orders.order.placed`,
  `orders.order.status_changed`; Functions `referrals.attribution.get`,
  `referrals.code.resolve`, `referrals.export`.
- GDPR owner `referrals` (export + erase; ledger retained), admin, beat,
  `referrals_housekeeping`, system checks E001–E005.
