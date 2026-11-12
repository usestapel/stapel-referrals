# stapel-referrals — MODULE.md

> Agent-facing map of this module: what it provides, where to extend it
> without forking, and what not to do. Kept in the same change as any edit
> to a seam. See also README.md, CHANGELOG.md and docs/SPEC.md.

## What this module provides

- **ReferralCode**: one primary personal code per user (lazy, minted from a
  31-symbol alphabet, partial unique constraint), vanity codes by policy
  (old code retired but claimable), staff campaign codes with window and cap
  (`attribution_count` bumped under the code-row lock). Share link from
  `SHARE_URL_TEMPLATE`.
- **Attribution**: referee ↔ code, `referee_id` unique (first touch wins),
  self-referral, account-age, capture-window and campaign checks, opt-in
  guards (`guards.py`). Claim endpoint for the client to call right after
  sign-up (the auth seam does not carry a code yet — ask A-RF1).
- **RewardRule / Reward**: rules on `signup`, `first_order`, `nth_order`;
  beneficiary referrer/referee/both; caps per referrer per rolling period and
  budgets counted under `select_for_update()` on the rule row; ledger FSM
  `pending → granted | rejected`, `granted → reversed`
  (`services/rewards.TRANSITIONS`); idempotent on `(rule, attribution,
  trigger_id, role)`; hold period for order rewards; reversal on order cancel.
- API under `/referrals/api/v1/` (`me/`, `me/code/`, `me/referrals/`,
  `me/rewards/`, `claim/`, `codes/{code}/`, `staff/...`), facts
  `referrals.*`, Functions `referrals.*`.

## Extension points (fork-free)

### Settings — `STAPEL_REFERRALS` namespace (`conf.py`, registry in CONFIG.MD)

| Key | Default | What it customizes |
|---|---|---|
| `REWARD_STRATEGIES` | `{}` | MERGE registry `{name: "dotted.Strategy" | None}` over `noop`, `billing_credit`, `promo_code`. A rule may also name a dotted path. |
| `ATTRIBUTION_GUARDS` | `[]` | Opt-in list: `same_device`, `duplicate_contact`, or dotted paths `(ClaimContext) -> reason | None`. |
| `VANITY_CODES` | `"off"` | Axis: `off` / `staff` / `users`. |
| `CLAIM_REQUIRES_ACCOUNT` | `False` | Axis: guests may claim by default (a guest row is an account). |
| `HOLD_DAYS` | `14` | Axis: order rewards wait pending; signups default to 0. A rule's `hold_days` overrides. |
| `REFEREE_DISPLAY` | `"masked"` | Axis: `hidden` / `masked` labels in `me/referrals/`. |
| `SHARE_URL_TEMPLATE` | `""` | `{code}` link; empty = `link: null`. |
| `ATTRIBUTION_WINDOW_DAYS` / `CLAIM_MAX_ACCOUNT_AGE_HOURS` / `QUALIFYING_WINDOW_DAYS` / `REVERSAL_WINDOW_DAYS` | 30 / 72 / 365 / 30 | Windows (§3–4 of docs/SPEC.md). |
| `BILLING_*_FUNCTION`, `PROMO_*_FUNCTION` | billing.credit, billing.debit, promotions.codes.issue, "" (no revoke seam yet) | The Function names the built-in strategies call (REPLACE). |

### Reward strategies (`strategies.py`)

Subclass `RewardStrategy`: `clean_params(params) -> dict` (raise
`ValueError`), `value(params) -> Decimal` (counts against `budget_value`),
`grant(reward) -> ref`, `reverse(reward)`. Raise `StrategyError` for any
"not now" — the reward stays pending/granted and the beat retries. Use
`strategies.idempotency_key(reward)` on every outbound call.

### Serializer seams and presenters (`views.py`, `presenters.py`)

Views inherit `StapelAPIView`. Presentation swaps via `STAPEL_SWAP`
`STAPEL_REFERRALS_ME_PRESENTER` / `_CLAIM_PRESENTER` / `_REFERRAL_PRESENTER` /
`_REWARD_PRESENTER` / `_STAFF_PRESENTER`.

| View | Guest stance | Request | Response |
|---|---|---|---|
| `MeView` (GET) | allowed (authenticated incl. guest) | `?device_id` | `MyReferralsResponseSerializer` |
| `VanityCodeView` (PATCH) | allowed statically; policy refuses guests 403 | `VanityCodeRequestSerializer` | `MyReferralsResponseSerializer` |
| `MyReferralsView`, `MyRewardsView` (GET) | allowed, scoped to me | anchor params | `ReferralPage` / `RewardPage` |
| `ClaimView` (POST) | allowed; enforces `CLAIM_REQUIRES_ACCOUNT` | `ClaimRequestSerializer` | `ClaimResponseSerializer` |
| `CodeLookupView` (GET) | public (AllowAny) | — | `CodeLookupResponseSerializer` |
| `Rule*`, `CampaignCode*`, `StaffReward*`, `StatsView` | denied; `IsStaffUser` | rule / campaign / transition requests | staff responses |

### Events & functions (comm surface)

| Kind | Name | Payload | Schema |
|---|---|---|---|
| Action (emits) | `referrals.attribution.created` | `{attribution_id, referee_id, referrer_id, code_kind, attributed_at}` | `schemas/emits/` |
| Action (emits) | `referrals.reward.granted` / `.rejected` / `.reversed` | `{reward_id, rule_id, attribution_id, role, beneficiary_id, referrer_id, trigger, strategy, value, unit, status, reason?}` | `schemas/emits/` |
| Action (consumes) | `user.signed_up` | signup marker + signup rules | `schemas/consumes/` (consumer view) |
| Action (consumes) | `user.merged` | codes, attributions, rewards, markers follow | `schemas/consumes/` |
| Action (consumes) | `orders.order.placed` / `orders.order.status_changed` | order rules; cancel rejects/reverses | `schemas/consumes/` |
| Action (consumes) | `user.deleted`, `gdpr.erasure.requested` | via `register_gdpr_owner("referrals", ["account"], …)` | core |
| Function (provides) | `referrals.attribution.get` | `{user_id}` → `{referred, referrer_id, code_kind, attributed_at}` | `schemas/functions/` |
| Function (provides) | `referrals.code.resolve` | `{code}` → `{found, claimable, kind, owner_id}` | `schemas/functions/` |
| Function (provides) | `referrals.export` | `{cursor, limit}` → `{rows, cursor, total}` | `schemas/functions/` |
| Function (calls) | `billing.credit` (ask B-RF1), `billing.debit` | strategy `billing_credit` | `schemas/asks/billing.credit.json`; billing owns `billing.debit` |
| Function (calls) | `promotions.codes.issue` | strategy `promo_code`: `{promotion_key, assigned_user_id, usage_limit: 1}` → `{promotion_key, codes}`; requires stapel-promotions >= 0.1.0; no idempotency key (ask P-RF1) | owned by stapel-promotions |
| Function (calls, proposed) | `promotions.codes.revoke` (ask P-RF1) | reversal of a promo reward; until it ships, `PROMO_REVOKE_FUNCTION=""` records the reversal and the code stays valid | `schemas/asks/promotions.codes.revoke.json` |
| Notification | `referrals.reward_granted` | `{reward_id, role, value, unit, grant_ref}` | ask N-RF1 |

### Beat (`beat.py`)

`get_referrals_beat_schedule()`: grant due rewards / retry failed grants
(5 min), retry reversals (15 min), purge markers and device sightings
(daily). `manage.py referrals_housekeeping` runs all three.

## Contract emission

`make contract` emits `docs/{schema,flows,errors,capabilities}.json`,
`docs/llms.txt` and README.md (from `docs/readme.md`); enum names pinned in
`_codegen_settings.enum_name_overrides()`. `STAPEL_TEST_DATABASE_URL` runs
the suite on PostgreSQL (`tests/test_concurrency_pg.py` needs it).

## Anti-patterns

- **Never import a sibling** (`stapel_auth`, `stapel_billing`,
  `stapel_orders`, `stapel_promotions`, …); `tests/test_architecture.py`
  greps the package.
- **Never count a cap outside the rule-row lock**, never read
  `attribution_count` outside the code-row lock.
- **Never call a strategy inside a lock**; never record a provider failure
  as `rejected`.
- **Never set `Reward.status` directly**; go through `services.rewards`.
- **Never put a code text a person chose, or a contact, on the bus.**
