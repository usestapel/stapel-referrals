# CONFIG.MD — stapel-referrals

Config registry for **stapel-referrals**. One row per configuration key
the module reads, its **source** (`env` = the process environment / the
`STAPEL_REFERRALS` settings dict), what it is for, whether it is required,
and its default.

Every key is read through `referrals_settings` (`stapel_referrals.conf`,
namespace `STAPEL_REFERRALS`). Resolution order per key:
`settings.STAPEL_REFERRALS` dict → a flat Django setting of the same name →
environment variable → the default below. `VANITY_CODES`,
`CLAIM_REQUIRES_ACCOUNT`, `HOLD_DAYS` and `REFEREE_DISPLAY` are CTO-facing
config axes.

## stapel-referrals

| Key | Source | Purpose | Required | Default |
|-----|--------|---------|----------|---------|
| CODE_LENGTH | env | length of a minted code (6-32; E005). | no | 8 |
| VANITY_CODES | env | axis: who may choose their own code: off, staff, users (E004). | no | off |
| VANITY_MAX_CHANGES | env | vanity codes one member may ever take. | no | 3 |
| RESERVED_CODES | settings | codes nobody may take as vanity. | no | ADMIN, STAFF, SUPPORT, REFERRAL, INVITE, PROMO, TEST |
| SHARE_URL_TEMPLATE | env | `{code}` template of the share link; empty = no link (E003). | no | "" |
| CLAIM_REQUIRES_ACCOUNT | env | axis: a guest (anonymous account) is refused with `error.403.claim_requires_account`. | no | false |
| ATTRIBUTION_WINDOW_DAYS | env | a code captured longer ago than this (client `captured_at`) is refused. | no | 30 |
| CLAIM_MAX_ACCOUNT_AGE_HOURS | env | an account older than this cannot claim; 0 = no limit. | no | 72 |
| QUALIFYING_WINDOW_DAYS | env | orders count for rewards this long after the attribution. | no | 365 |
| ATTRIBUTION_GUARDS | settings | opt-in guards: `same_device`, `duplicate_contact` or dotted paths (E002). | no | [] |
| REWARD_STRATEGIES | settings | {name: dotted path or None} merged over `noop`, `billing_credit`, `promo_code` (E001). | no | {} |
| HOLD_DAYS | env | axis: days an order reward stays pending before the grant (a rule's `hold_days` overrides). | no | 14 |
| REVERSAL_WINDOW_DAYS | env | a cancel this long after the grant still reverses the reward. | no | 30 |
| REFEREE_DISPLAY | env | axis: how a referrer sees referees: hidden, masked (E004). | no | masked |
| NOTIFY | env | best-effort notification `referrals.reward_granted` to the beneficiary. | no | true |
| BILLING_CREDIT_FUNCTION | env | comm Function the billing_credit strategy calls to pay. | no | billing.credit |
| BILLING_DEBIT_FUNCTION | env | comm Function the billing_credit strategy calls to reverse. | no | billing.debit |
| PROMO_ISSUE_FUNCTION | env | comm Function the promo_code strategy calls to issue a code (stapel-promotions >= 0.1.0). | no | promotions.codes.issue |
| PROMO_REVOKE_FUNCTION | env | comm Function the promo_code strategy calls to revoke a code; empty = the reversal is recorded and the code stays redeemable (promotions has no revoke Function yet). | no | "" |
| FUNCTION_TIMEOUT_SECONDS | env | timeout of one strategy call. | no | 5.0 |
| SIGNUP_MARKER_TTL_DAYS | env | signup markers older than this are purged by the beat. | no | 30 |
| DEVICE_SIGHTING_TTL_DAYS | env | device sightings older than this are purged by the beat. | no | 30 |
