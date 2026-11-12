## What this is

Referral codes, attribution and rewards for a Stapel product — "invite a
friend, both get a bonus" and partner links. Every member gets one personal
code (minted on first use, vanity codes by policy) and a share link; staff
issue campaign codes with a window and a cap. A visitor's client stores the
code on the first visit and claims it right after sign-up; the account is
attributed once, first touch wins. Reward rules fire on facts — sign-up, the
first qualifying order, the Nth order — and pay through a strategy that asks
the paying brick by comm Function (`billing_credit`, `promo_code`) or just
records the reward (`noop`). The module never imports auth, orders, billing
or promotions.

## Quick start

```python
INSTALLED_APPS = [
    # ...
    "stapel_referrals",
]

STAPEL_REFERRALS = {
    "SHARE_URL_TEMPLATE": "https://shop.example/r/{code}",
}

# urls.py
path("referrals/api/", include("stapel_referrals.urls"))   # -> /referrals/api/v1/...

# settings.py — grants after the hold, reversal retries, purge (Celery optional)
from stapel_referrals.beat import get_referrals_beat_schedule
CELERY_BEAT_SCHEDULE = {**get_referrals_beat_schedule()}
```

Then a staff member creates a rule (`POST /referrals/api/v1/staff/rules/`):

```json
{"name": "Friend bonus", "trigger": "first_order", "min_order_total": "30.00",
 "currency": "USD", "beneficiary": "both", "strategy": "billing_credit",
 "referrer_params": {"credits": 100}, "referee_params": {"credits": 50},
 "cap_per_referrer": 10, "cap_period_days": 30}
```

The client side is two calls: `GET me/` (my code and link) for the referrer,
and `POST claim/ {code, captured_at}` right after sign-up for the referee.

## Concepts

- **Code** — personal (one primary per member; a vanity change retires the
  old code, which stays claimable so shared links keep working) or campaign
  (staff-owned, `starts_at`/`ends_at`, `max_attributions`). 31-symbol
  alphabet without look-alikes; case-insensitive.
- **Attribution** — one per account, enforced by a unique constraint. Self
  referral, old accounts (`CLAIM_MAX_ACCOUNT_AGE_HOURS`), stale captures
  (`ATTRIBUTION_WINDOW_DAYS`) and closed or used-up campaign codes are
  refused. Opt-in guards (`same_device`, `duplicate_contact`, or your own)
  record a refused attribution that keeps the account's slot.
- **Reward rule** — trigger, beneficiary (referrer / referee / both),
  strategy and params per beneficiary, caps per referrer per rolling period,
  budgets in triggers and in value, a hold for order rewards.
- **Reward** — the ledger: `pending → granted | rejected`, `granted →
  reversed`. Idempotent on `(rule, attribution, trigger, role)`. Caps are
  counted under a lock on the rule row. A provider failure keeps a reward
  pending and the beat retries; it is never recorded as a refusal. An order
  cancelled during the hold rejects its rewards; after the grant (within
  `REVERSAL_WINDOW_DAYS`) it reverses them.
- **GDPR** — erasure pseudonymises the person on codes, attributions and
  rewards and replaces their code text; the ledger stays.
