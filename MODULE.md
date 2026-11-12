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
  guards (`guards.py`). Two doors, one row: the sign-up fact's
  `signup_context` (below) and `POST claim/` (older auth, guests).
- **RewardRule / Reward**: rules on `signup`, `first_order`, `nth_order`;
  beneficiary referrer/referee/both; caps per referrer per rolling period and
  budgets counted under `select_for_update()` on the rule row; ledger FSM
  `pending → granted | rejected`, `granted → reversed` with
  `reversal_outcome` `reversed | reversed_partial | already_spent`
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
| `BILLING_*_FUNCTION`, `PROMO_*_FUNCTION` | billing.credit, billing.debit, promotions.codes.issue, promotions.codes.revoke | The Function names the built-in strategies call (REPLACE). |

### Reward strategies (`strategies.py`)

Subclass `RewardStrategy`; set `params_schema` (JSON Schema 2020-12 of the
params — served by `GET staff/strategies/` to the rule form and checked on
every rule write before `clean_params`) and a one-line `description`;
`clean_params(params) -> dict` (raise
`ValueError`), `value(params) -> Decimal` (counts against `budget_value`),
`grant(reward) -> ref`, `reverse(reward) -> Reversal | None` (`None` = all
of it came back; `Reversal("reversed_partial" | "already_spent", value)`
otherwise). Raise `StrategyError` for any "not now" — the reward stays
pending/granted and the beat retries; `StrategyConflict` for a provider
key conflict (pending, ERROR log); `StrategyRefused(reason)` for a domain
"no" (the reward is rejected with that reason). Use
`strategies.idempotency_key(reward)` on every outbound call: it is
namespaced `referrals:reward:<id>` because a provider's key space (billing:
per wallet, shared with debits and holds) is not ours.

### Sign-up carriage — the client contract

The client stores the code on the first visit and sends it in auth's
opaque sign-up field (stapel-auth >= 0.51.0):

```json
"signup_context": {"referral_code": "AB2CD3EF", "referral_captured_at": "2026-11-10T09:00:00Z"}
```

`referral_code` is the code text (any case); `referral_captured_at` is
ISO 8601 on the client clock (optional). Auth copies the object into
`user.signed_up`; referrals attributes from the fact with the claim's
guards, judged at the fact's `registered_at` (an auth clock more than 5
minutes ahead reads as now; a client `captured_at` after it reads as the
sign-up instant; older than `ATTRIBUTION_WINDOW_DAYS` is refused). A
refusal is logged at INFO and never fails the fact. A client on an older
auth, or a guest, calls `POST claim/` instead — doing both is safe: one
attribution, a same-code claim answers `200`.

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
| `StrategyListView` (GET `staff/strategies/`) | denied; `IsStaffUser` | — | `RewardStrategyListSerializer` `{items: [{name, unit, builtin, description, params_schema}]}` |

### Events & functions (comm surface)

| Kind | Name | Payload | Schema |
|---|---|---|---|
| Action (emits) | `referrals.attribution.created` | `{attribution_id, referee_id, referrer_id, code_kind, attributed_at}` | `schemas/emits/` |
| Action (emits) | `referrals.reward.granted` / `.rejected` / `.reversed` | `{reward_id, rule_id, attribution_id, role, beneficiary_id, referrer_id, trigger, strategy, value, unit, status, reason?}` (+ `reversal_outcome`, `reversed_value` once reversed) | `schemas/emits/` |
| Action (consumes) | `user.signed_up` | signup marker (+ `auth_type`), attribution from `signup_context`, signup rules | `schemas/consumes/` (consumer view) |
| Action (consumes) | `user.merged` | codes, attributions, rewards, markers follow | `schemas/consumes/` |
| Action (consumes) | `orders.order.placed` / `orders.order.status_changed` | order rules; cancel rejects/reverses | `schemas/consumes/` |
| Action (consumes) | `user.deleted`, `gdpr.erasure.requested` | via `register_gdpr_owner("referrals", ["account"], …)` | core |
| Function (provides) | `referrals.attribution.get` | `{user_id}` → `{referred, referrer_id, code_kind, attributed_at}` | `schemas/functions/` |
| Function (provides) | `referrals.code.resolve` | `{code}` → `{found, claimable, kind, owner_id}` | `schemas/functions/` |
| Function (provides) | `referrals.export` | `{cursor, limit}` → `{rows, cursor, total}` | `schemas/functions/` |
| Function (calls) | `billing.credit` | strategy `billing_credit`: `{user_id, credits, idempotency_key: "referrals:reward:<id>", source: "grant", type, description, metadata, expires_at?, identity?}` → `{ok, balance, reason, transaction_id?}`; `user_unknown`/`user_inactive` → rejected, `idempotency_conflict` → pending + ERROR, `invalid_expires_at`/other/no answer → retried; requires stapel-billing >= 0.25.0 | owned by stapel-billing |
| Function (calls) | `billing.debit` | reversal: `{credits, idempotency_key: "referrals:reward:<id>:reverse", identity?}`, no `allow_partial`; `insufficient_credits` → the plan drops to the balance (stored first, so a retry replays it): `reversed_partial` / `already_spent`, never a debt | owned by stapel-billing |
| Function (calls) | `promotions.codes.issue` | strategy `promo_code`: `{promotion_key, assigned_user_id, usage_limit: 1, idempotency_key: "referrals:reward:<id>"}` → `{promotion_key, codes}`; a lost answer replays the same code | owned by stapel-promotions (0.1.0 with the issue key) |
| Function (calls) | `promotions.codes.revoke` | reversal: `{code, reason: "referrals:<reason>"}` → `{revoked, already_revoked, redeemed, …}`; `redeemed: true` → `already_spent` | owned by stapel-promotions |
| Notification | `referrals.reward_granted` | `{reward_id, role, value, unit, grant_ref}`; the type ships in stapel-notifications >= 0.30.1 (caller variables `value`, `unit`, `grant_ref`) | owned by stapel-notifications |

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
