"""Settings namespace for stapel-referrals.

All configuration is read through ``referrals_settings`` (lazily, at call
time). Resolution order per key: ``settings.STAPEL_REFERRALS`` dict -> flat
Django setting of the same name -> environment variable -> default below.

``REWARD_STRATEGIES`` and ``ATTRIBUTION_GUARDS`` are open registries:
``REWARD_STRATEGIES`` merges over the built-ins (``noop``,
``billing_credit``, ``promo_code``; ``None`` removes one), and a reward rule
may also name a strategy by dotted path. The module never imports billing,
promotions, orders or auth: every neighbour is a comm Function name or a
fact name below.
"""
from stapel_core.conf import AppSettings

VANITY_POLICIES = ("off", "staff", "users")
REFEREE_DISPLAYS = ("hidden", "masked")

#: AppSettings-shaped literal dict: a top-level DEFAULTS lets the
#: capabilities.json emitter introspect axis keys/kinds.
DEFAULTS = {
    # Length of a minted code (31-symbol alphabet without look-alikes).
    "CODE_LENGTH": 8,
    # Axis: who may replace a personal code with a vanity one.
    # "off" | "staff" (staff accounts only) | "users" (everyone).
    "VANITY_CODES": "off",
    # Personal codes one user may ever mint by vanity changes.
    "VANITY_MAX_CHANGES": 3,
    # Codes nobody may take as vanity (compared upper-case).
    "RESERVED_CODES": ["ADMIN", "STAFF", "SUPPORT", "REFERRAL", "INVITE", "PROMO", "TEST"],
    # "{code}" template of the share link, e.g. "https://shop.example/r/{code}";
    # empty = no link (never a guess).
    "SHARE_URL_TEMPLATE": "",
    # Axis: may a guest (anonymous account) claim a code?
    "CLAIM_REQUIRES_ACCOUNT": False,
    # A code captured longer ago than this (client captured_at) is refused.
    "ATTRIBUTION_WINDOW_DAYS": 30,
    # An account older than this cannot claim; 0 = no limit.
    "CLAIM_MAX_ACCOUNT_AGE_HOURS": 72,
    # Orders count for rewards this long after the attribution.
    "QUALIFYING_WINDOW_DAYS": 365,
    # Opt-in guards: short names ("same_device", "duplicate_contact") or
    # dotted paths to (ClaimContext) -> reason | None.
    "ATTRIBUTION_GUARDS": [],
    # {name: "dotted.path.Strategy" | None}, merged over the built-ins.
    "REWARD_STRATEGIES": {},
    # Axis: days an order-triggered reward stays pending before it is granted.
    "HOLD_DAYS": 14,
    # A cancel this long after the grant still reverses the reward.
    "REVERSAL_WINDOW_DAYS": 30,
    # Axis: how a referrer sees their referees: "hidden" | "masked".
    "REFEREE_DISPLAY": "masked",
    # Best-effort notification referrals.reward_granted to the beneficiary.
    "NOTIFY": True,
    # Functions the built-in strategies call (owned by billing / promotions).
    "BILLING_CREDIT_FUNCTION": "billing.credit",
    "BILLING_DEBIT_FUNCTION": "billing.debit",
    "PROMO_ISSUE_FUNCTION": "promotions.codes.issue",
    # Empty: promotions ships no revoke Function yet (ask P-RF1); a reversal
    # is recorded and the issued code stays redeemable.
    "PROMO_REVOKE_FUNCTION": "",
    # Seconds a strategy call may take.
    "FUNCTION_TIMEOUT_SECONDS": 5.0,
    # Housekeeping: signup markers and device sightings older than these go.
    "SIGNUP_MARKER_TTL_DAYS": 30,
    "DEVICE_SIGHTING_TTL_DAYS": 30,
}

referrals_settings = AppSettings("STAPEL_REFERRALS", defaults=DEFAULTS)

__all__ = ["referrals_settings", "DEFAULTS", "VANITY_POLICIES", "REFEREE_DISPLAYS"]
