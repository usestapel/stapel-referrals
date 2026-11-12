# SPEC — stapel-referrals 0.1.0

Status: **built locally, waiting to go public** (brigade RF, 2026-11-12).
Queue: brick wave 2026-11-12 §1 row `stapel-referrals`. Consumer: this
repo's test suite enforces every rule below by name (§11); the brick-wave
gates (§3 of the wave spec) apply. House style: `SPEC-STAPEL-ORDERS.md`
(SimpleRouter-free plain paths, `StapelAPIView` seams, `AppSettings` +
CONFIG.MD, `errors.py`, `@access`, `mutate_and_emit`, `AnchorPagination`,
`user_id CharField(64)`, sibling-import ban, MODULE.md, capabilities.json).

## 1. Scope

| In | Out |
|---|---|
| One personal code per user, minted lazily; vanity codes by policy | Multi-level schemes (a referrer's referrer earns nothing) |
| Campaign codes owned by staff (window, attribution cap) | Cash payouts, commissions in money to third parties |
| Share link from a URL template | Click tracking / landing analytics (the client stores the code) |
| Attribution referee ↔ code: first touch wins, windows, guards | Cookie or session attribution on the server |
| Reward rules on facts: signup, first qualifying order, Nth order | Rewards on arbitrary facts (a later `TRIGGERS` registry) |
| Reward ledger with a pending → granted / rejected / reversed FSM | Moving money: a strategy asks the owning brick by Function |
| Reward strategies: `noop`, `billing_credit`, `promo_code`, dotted path | Importing billing / promotions / orders / auth |
| Caps per referrer per period, global budgets, race-proof | Fraud scoring; guards are hooks that return a reason |
| "My referrals", staff API and stats, facts, GDPR, admin | A react pair (next slice) |

## 2. Models

- **ReferralCode** — `code` (unique, upper-case, ≤32), `kind`
  (`personal` | `campaign`), `owner_id` (CharField 64: the referrer),
  `is_primary` (one primary personal code per owner — partial unique
  constraint), `vanity`, `active`, `label`, campaign window
  `starts_at`/`ends_at`, `max_attributions`, `attribution_count` (counter
  bumped under the code-row lock), `created_by`.
  Minted from a 31-symbol alphabet without look-alikes
  (`23456789ABCDEFGHJKMNPQRSTUVWXYZ`), `CODE_LENGTH` (default 8 → 31⁸ ≈
  8.5·10¹¹); a collision retries on the unique constraint, never a
  read-then-write. Lazy minting races end on the partial unique constraint
  and the loser re-reads.
- **Attribution** — `referee_id` **unique** (one per referee, enforced by a
  constraint, not a read), `code` FK PROTECT, `referrer_id` snapshot,
  `code_kind` snapshot, `status` (`active` | `refused`), `refusal_reason`,
  `source` (`claim` | `signup` | `merge`), `captured_at` (client), `attributed_at`,
  `qualifying_until`.
- **RewardRule** — `name`, `active`, `trigger` (`signup` | `first_order` |
  `nth_order`), `order_ordinal` (N; `first_order` is N=1), `min_order_total`
  + `currency`, `beneficiary` (`referrer` | `referee` | `both`), `strategy`
  + `referrer_params` / `referee_params`, `code_kind` (`any` | `personal` |
  `campaign`), optional `campaign_code`, `starts_at`/`ends_at`,
  `cap_per_referrer` + `cap_period_days` (null = all time),
  `budget_count`, `budget_value`, `hold_days` (null = setting).
- **Reward** — the ledger: `rule` FK PROTECT, `attribution` FK PROTECT,
  `trigger` (`signup` | `order`), `trigger_id` (≤128: the referee id for a
  signup, the order id for an order), `role` (`referrer` | `referee`),
  `beneficiary_id`, `referrer_id` snapshot, `status`, `strategy`, `params`
  snapshot, `value` Decimal(18,2) + `unit`, `grant_ref`, `reason`,
  `hold_until`, `granted_at` / `rejected_at` / `reversed_at`,
  `reverse_requested_at`, `reversal_outcome` (`reversed` |
  `reversed_partial` | `already_spent`), `reversed_value`, `attempts`,
  `last_error`.
  **Unique `(rule, attribution, trigger_id, role)`** — the idempotency key.
- **SignupMarker** — `user_id` unique, `signed_up_at`, `auth_type` (the
  `identity` a paying brick may create the user from): the
  `user.signed_up` fact seen (so a claim made after the fact still pays the
  signup rule, and the account age is the auth clock, not a shadow row's).
- **QualifyingOrder** — `order_id` unique, `attribution` FK, `total`,
  `currency`, `placed_at`, `cancelled_at`: orders a referee placed after
  attribution and inside the qualifying window (the ordinal source).
- **DeviceSighting** — `user_id`, `device_hash` (HMAC of a client device id
  with `SECRET_KEY`): the `same_device` guard's memory; purged after
  `DEVICE_SIGHTING_TTL_DAYS`.

## 3. Attribution rules

1. The client stores the code on the first visit (first touch: it never
   overwrites a stored code) with `captured_at`, and sends both in auth's
   `signup_context` as `referral_code` / `referral_captured_at` (stapel-auth
   >= 0.51.0 copies it into `user.signed_up`; source `signup`, judged at the
   fact's `registered_at`, an auth clock more than 5 minutes ahead reads as
   now, refusals logged at INFO). On an older auth, or as a guest (a guest
   row is an account), it calls `POST claim/` right after sign-up. Both
   doors share one code path and the `referee_id` constraint: together they
   make one attribution (race-tested).
2. Unknown or inactive code → `400 code_unknown`. Own code →
   `400 self_referral` (does **not** occupy the referee's slot).
3. A campaign code outside its window → `409 code_expired`; at
   `max_attributions` → `409 code_exhausted` (counted under the code-row
   lock).
4. `captured_at` older than `ATTRIBUTION_WINDOW_DAYS` → `409
   attribution_window_closed`. It is client-supplied: advisory, not a fraud
   guard.
5. Account older than `CLAIM_MAX_ACCOUNT_AGE_HOURS` (auth's
   `registered_at` from the `user.signed_up` marker, else the user row's
   `created_at`/`date_joined`; unknown → allowed) → `409
   claim_window_closed`. Existing customers are not referees.
6. The referee already has an attribution: same code → `200` replay; another
   code → `409 already_attributed` (first touch wins on the server too).
7. Guards (`ATTRIBUTION_GUARDS`, opt-in): each returns a reason or None.
   The first reason records the attribution as `refused` (it keeps the
   referee's slot — no retrying with another code) and answers `409
   claim_refused` without the reason on the wire. Built-ins: `same_device`
   (the claim's device hash was seen on the referrer), `duplicate_contact`
   (normalised e-mail or phone equal to the referrer's, read from the user
   model; a referrer row this service has not seen is "unmeasured" → no
   finding).
8. `CLAIM_REQUIRES_ACCOUNT` (default False): a guest claim is allowed, the
   guest row's later promotion is the same user, and `user.merged` moves a
   guest's attribution to the survivor only when the survivor has none and
   is within the account-age window; otherwise the guest's attribution is
   dropped (rewards already in the ledger follow their beneficiary).
9. `referrals.attribution.created` is emitted with the row.

## 4. Reward rules

- **Triggers**: `signup` on `user.signed_up` (or at claim when the marker
  already exists); `first_order` / `nth_order` on `orders.order.placed` —
  the order counts when placed after `attributed_at` and before
  `qualifying_until` (`QUALIFYING_WINDOW_DAYS`), its currency equals the
  rule's and its total ≥ `min_order_total`; the ordinal is the order's
  position among the referee's non-cancelled qualifying orders.
- **Once per rule per attribution and role**: a live (pending/granted)
  reward of the same rule and attribution blocks another trigger of that
  rule; the unique key makes a replayed fact a no-op.
- **Caps**, counted under `select_for_update()` on the rule row, which
  serialises every writer of that rule: `cap_per_referrer` triggers per
  referrer in the last `cap_period_days`; `budget_count` triggers per rule;
  `budget_value` summed value. A trigger over a cap is recorded as
  `rejected` with `cap_reached` / `budget_exhausted` (visible to staff,
  idempotent on replay).
- **Hold**: order rewards wait `pending` until `hold_until = now +
  hold_days` (`HOLD_DAYS`, default 14); signup rewards default to 0. The
  beat grants due rewards. An order cancelled while its reward is pending →
  `rejected` (`order_cancelled`); cancelled after the grant and within
  `REVERSAL_WINDOW_DAYS` → reversal requested, `reversed` once the strategy
  confirms.
- **Granting** runs outside the rule lock: the strategy is called with the
  idempotency key `referrals:reward:<id>`; an infrastructure failure keeps
  the reward `pending` with `last_error` and `attempts` (never recorded as a
  domain refusal); the beat retries.

## 5. Reward strategies (`REWARD_STRATEGIES`, merge-over-builtins)

| Name | Params | Calls | Value / unit |
|---|---|---|---|
| `noop` (default) | `{value?}` | nothing — records only | value / `none` |
| `billing_credit` | `{credits: int ≥ 1, expires_in_days?, description?}` | `BILLING_CREDIT_FUNCTION` (`billing.credit`, stapel-billing ≥ 0.25.0, key `referrals:reward:<id>`, `identity` from the signup marker; `user_unknown`/`user_inactive` → `rejected`, `idempotency_conflict` → pending + ERROR). Reversal via `BILLING_DEBIT_FUNCTION` (`billing.debit`, key `…:reverse`, no `allow_partial`): spent credits are not clawed back as debt — the reversal takes what the wallet holds (`reversed_partial`, `already_spent` at zero) | credits / `credits` |
| `promo_code` | `{promotion_key: str ≤58, value?}` | `PROMO_ISSUE_FUNCTION` = `promotions.codes.issue {promotion_key, assigned_user_id, usage_limit: 1, idempotency_key}` → `{promotion_key, codes: [str]}` (a lost answer replays the same code); unknown key fails the call (reward stays pending). Reversal: `PROMO_REVOKE_FUNCTION` = `promotions.codes.revoke {code, reason}`; a redeemed code is revoked against further use and the reversal ends `already_spent` | value / `code` |
| dotted path | strategy-defined | strategy-defined | strategy-defined |

This module ships no copy of the payload contracts it calls; their owners'
schemas are the source, and the seam run (§11) installs the real owners.

## 6. API (`/referrals/api/v1/`)

| Method/path | Who | Request → Response |
|---|---|---|
| `GET me/` | account or guest | → `MyReferralsResponse {code, link, vanity_allowed, hold_days, counts, rewards_total, referred}` (mints the code lazily; `hold_days` = `HOLD_DAYS`) |
| `PATCH me/code/` | account (policy) | `VanityCodeRequest {code}` → `MyReferralsResponse` |
| `GET me/referrals/` | account or guest | anchor page `AttributionPage` of my referees, labels masked by `REFEREE_DISPLAY` |
| `GET me/rewards/` | account or guest | anchor page `RewardPage` of rewards where I am the beneficiary |
| `POST claim/` | account or guest | `ClaimRequest {code, captured_at?, device_id?}` → `201 ClaimResponse`, replay `200` |
| `GET codes/{code}/` | anyone | → `CodeLookupResponse {code, kind, valid, referrer_label}` |
| `GET/POST staff/rules/`, `GET/PATCH staff/rules/{id}/` | staff | `RewardRuleRequest` → `RewardRuleResponse` |
| `GET/POST staff/campaign-codes/`, `PATCH staff/campaign-codes/{id}/` | staff | `CampaignCodeRequest` → `CampaignCodeResponse` |
| `GET staff/rewards/` | staff | `?status=&rule=` → `RewardPage` |
| `POST staff/rewards/{id}/transition/` | staff | `RewardTransitionRequest {to, reason?}` → `RewardResponse` |
| `GET staff/stats/` | staff | → `ReferralStatsResponse` |
| `GET staff/strategies/` | staff | → `RewardStrategyList {items: [{name, unit, builtin, description, params_schema}]}`: every registered strategy; rule writes validate params against `params_schema` (`400 reward_params_invalid {problem}`) |

## 7. Settings (`STAPEL_REFERRALS`)

See CONFIG.MD (the registry). Axes: `VANITY_CODES` (`off` | `staff` |
`users`), `CLAIM_REQUIRES_ACCOUNT`, `HOLD_DAYS`, `REFEREE_DISPLAY`
(`hidden` | `masked`).

## 8. Functions and facts

Provides: `referrals.attribution.get {user_id}`, `referrals.code.resolve
{code}`, `referrals.export {cursor, limit}` (ledger, `source_of_truth`
shape). Emits: `referrals.attribution.created`, `referrals.reward.granted`,
`referrals.reward.rejected`, `referrals.reward.reversed` (all through
`mutate_and_emit`, key = attribution / reward id). Consumes:
`user.signed_up`, `user.merged`, `orders.order.placed`,
`orders.order.status_changed`; `user.deleted` via `register_gdpr_owner`.
Best-effort notification `referrals.reward_granted` (`NOTIFY`).

## 9. GDPR

Owner `referrals`, subject `account`. Export: codes, attribution as
referee, attributions as referrer (masked), rewards as beneficiary.
Erase: personal codes deactivated and their text replaced (a vanity code
can carry a name), `owner_id` / `referee_id` / `referrer_id` /
`beneficiary_id` pseudonymised, markers and device sightings deleted; the
reward ledger (rule, status, value, unit, grant_ref) is retained.

## 10. Upstream asks

Shipped and adopted: A-RF1 (auth `signup_context`, 0.51.0), B-RF1
(`billing.credit`, 0.25.0), P-RF1 (issue key + `promotions.codes.revoke`,
promotions 0.1.0), N-RF1 (`referrals.reward_granted`, notifications 0.30.1).
Still open:

| Id | Repo | Ask |
|---|---|---|
| B-RF2 | stapel-billing | a Function that reverses one credit by its transaction (`claw_back_grant` without the `outstanding` debt, e.g. `billing.credit.reverse {transaction_id, idempotency_key, forgive_spent: true}` → `{reclaimed, forgiven}`): `billing.debit` walks every lot, so a reversal of a spent referral credit today takes from credits the user bought |
| P-RF2 | stapel-promotions | `promotions.codes.issue` answers the minted `code_ids` too, so a reversal can revoke by id instead of code text |
| T-RF1 | stapel-tools | `STAPEL_LIBS["referrals"]`, `_gdpr_owners.py` += `referrals: ["account"]` |

## 11. Gates

pytest on 3.12 and 3.14 (+3.13 when present) with `emit_outside_atomic_gate`;
`make contract-check`; `stapel-verify .`; system checks with and without
billing / orders / promotions providers registered; migration from zero;
concurrency tests for the code counter, the attribution constraint and the
rule caps (lock-before-count spy + interleaving + inversion); GDPR export and
erase; architecture test (no sibling import). Seam run: the module in one
host with the real stapel-auth (>= 0.51.0), stapel-billing (>= 0.25.0),
stapel-promotions (0.1.0 with the issue key) and released stapel-orders —
sign-up with a context, first order, grant (wallet balance / issued code),
cancel inside the hold window after the grant, reversal (debit / revoke).
