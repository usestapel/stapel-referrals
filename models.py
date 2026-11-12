"""Models of stapel-referrals.

Users are referenced by id strings (``str(user.pk)``), not foreign keys: an
erased attribution keeps the ledger under a pseudonymized id, which a
foreign key cannot hold, and the referrer may live in another service.
Orders are referenced by the id the ``orders.order.placed`` fact carries.
"""
from __future__ import annotations

from django.db import models
from django.db.models import Q
from stapel_core.access import access

USER_ID_MAX_LENGTH = 64
CODE_MAX_LENGTH = 32
LABEL_MAX_LENGTH = 120
REASON_MAX_LENGTH = 64
TRIGGER_ID_MAX_LENGTH = 128
STRATEGY_MAX_LENGTH = 200
GRANT_REF_MAX_LENGTH = 255
ERROR_MAX_LENGTH = 500
UNIT_MAX_LENGTH = 16
CURRENCY_MAX_LENGTH = 3
DEVICE_HASH_MAX_LENGTH = 64

#: Reward values: Decimal(18,2), the orders total precision.
VALUE_MAX_DIGITS = 18
VALUE_DECIMAL_PLACES = 2


def _value_field(**kwargs):
    return models.DecimalField(
        max_digits=VALUE_MAX_DIGITS, decimal_places=VALUE_DECIMAL_PLACES, **kwargs
    )


class CodeKind(models.TextChoices):
    PERSONAL = "personal", "Personal"
    CAMPAIGN = "campaign", "Campaign"


class AttributionStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    REFUSED = "refused", "Refused by a guard"


class AttributionSource(models.TextChoices):
    CLAIM = "claim", "Claimed by the referee"
    MERGE = "merge", "Carried over by an account merge"


class Trigger(models.TextChoices):
    SIGNUP = "signup", "Referee signed up"
    FIRST_ORDER = "first_order", "Referee's first qualifying order"
    NTH_ORDER = "nth_order", "Referee's Nth qualifying order"


class Beneficiary(models.TextChoices):
    REFERRER = "referrer", "Referrer"
    REFEREE = "referee", "Referee"
    BOTH = "both", "Both"


class Role(models.TextChoices):
    REFERRER = "referrer", "Referrer"
    REFEREE = "referee", "Referee"


class RuleCodeKind(models.TextChoices):
    ANY = "any", "Any code"
    PERSONAL = "personal", "Personal codes"
    CAMPAIGN = "campaign", "Campaign codes"


class RewardStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    GRANTED = "granted", "Granted"
    REJECTED = "rejected", "Rejected"
    REVERSED = "reversed", "Reversed"


class ReversalOutcome(models.TextChoices):
    REVERSED = "reversed", "Taken back in full"
    PARTIAL = "reversed_partial", "Taken back in part; the rest was already spent"
    SPENT = "already_spent", "Nothing to take back; already spent"


class TriggerKind(models.TextChoices):
    SIGNUP = "signup", "Signup"
    ORDER = "order", "Order"


#: Rewards that count against caps and block a second trigger of a rule.
LIVE_REWARD_STATUSES = (RewardStatus.PENDING, RewardStatus.GRANTED)


@access.standard
class ReferralCode(models.Model):
    code = models.CharField(
        max_length=CODE_MAX_LENGTH, unique=True,
        help_text="Upper-case code; case-insensitive on input.",
    )
    kind = models.CharField(
        max_length=16, choices=CodeKind.choices, default=CodeKind.PERSONAL,
        help_text="personal (one primary per user) or campaign (staff-owned).",
    )
    owner_id = models.CharField(
        max_length=USER_ID_MAX_LENGTH, db_index=True,
        help_text="str(user.pk) of the referrer; a pseudonym after erasure.",
    )
    is_primary = models.BooleanField(
        default=True,
        help_text="The personal code shown to its owner; retired codes stay claimable.",
    )
    vanity = models.BooleanField(default=False, help_text="Chosen by a person, not minted.")
    active = models.BooleanField(default=True, help_text="Inactive codes cannot be claimed.")
    label = models.CharField(
        max_length=LABEL_MAX_LENGTH, blank=True, default="",
        help_text="Staff label of a campaign code.",
    )
    starts_at = models.DateTimeField(null=True, blank=True, help_text="Campaign window start.")
    ends_at = models.DateTimeField(null=True, blank=True, help_text="Campaign window end (exclusive).")
    max_attributions = models.PositiveIntegerField(
        null=True, blank=True, help_text="Attributions this code may make; NULL = no cap.",
    )
    attribution_count = models.PositiveIntegerField(
        default=0, help_text="Attributions made; bumped under the code-row lock.",
    )
    created_by = models.CharField(
        max_length=USER_ID_MAX_LENGTH, blank=True, default="",
        help_text="Staff user that created a campaign code.",
    )
    created_at = models.DateTimeField(auto_now_add=True, help_text="When the code was minted.")
    updated_at = models.DateTimeField(auto_now=True, help_text="Last change.")

    class Meta:
        app_label = "referrals"
        ordering = ["-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["owner_id"],
                condition=Q(kind="personal", is_primary=True),
                name="referrals_one_primary_code",
            ),
        ]

    def __str__(self) -> str:
        return self.code


@access.standard
class Attribution(models.Model):
    referee_id = models.CharField(
        max_length=USER_ID_MAX_LENGTH, unique=True,
        help_text="str(user.pk) of the referred account; one attribution per referee.",
    )
    code = models.ForeignKey(
        ReferralCode, on_delete=models.PROTECT, related_name="attributions",
        help_text="The code the referee claimed.",
    )
    referrer_id = models.CharField(
        max_length=USER_ID_MAX_LENGTH, db_index=True,
        help_text="The code owner at attribution time.",
    )
    code_kind = models.CharField(
        max_length=16, choices=CodeKind.choices,
        help_text="Snapshot of the code kind.",
    )
    status = models.CharField(
        max_length=16, choices=AttributionStatus.choices, default=AttributionStatus.ACTIVE,
        help_text="refused = a guard objected; the slot stays taken.",
    )
    refusal_reason = models.CharField(
        max_length=REASON_MAX_LENGTH, blank=True, default="",
        help_text="The guard's reason; never sent to the referee.",
    )
    source = models.CharField(
        max_length=16, choices=AttributionSource.choices, default=AttributionSource.CLAIM,
        help_text="How the attribution was made.",
    )
    captured_at = models.DateTimeField(
        null=True, blank=True, help_text="When the client captured the code (client clock).",
    )
    attributed_at = models.DateTimeField(help_text="When the claim was accepted.")
    qualifying_until = models.DateTimeField(help_text="Orders after this do not count.")

    class Meta:
        app_label = "referrals"
        ordering = ["-id"]
        indexes = [
            models.Index(fields=["referrer_id", "-id"], name="referrals_attr_referrer"),
        ]

    def __str__(self) -> str:
        return f"{self.referee_id} <- {self.code_id}"


@access.standard
class RewardRule(models.Model):
    name = models.CharField(max_length=LABEL_MAX_LENGTH, help_text="Staff-facing name.")
    active = models.BooleanField(default=True, help_text="Inactive rules never fire.")
    trigger = models.CharField(max_length=16, choices=Trigger.choices, help_text="What fires the rule.")
    order_ordinal = models.PositiveIntegerField(
        default=1, help_text="N of nth_order; first_order is 1.",
    )
    min_order_total = _value_field(
        null=True, blank=True, help_text="An order below this total does not qualify.",
    )
    currency = models.CharField(
        max_length=CURRENCY_MAX_LENGTH, blank=True, default="",
        help_text="Currency of min_order_total; empty = any currency (no minimum).",
    )
    beneficiary = models.CharField(
        max_length=16, choices=Beneficiary.choices, default=Beneficiary.BOTH,
        help_text="Who is rewarded.",
    )
    strategy = models.CharField(
        max_length=STRATEGY_MAX_LENGTH, default="noop",
        help_text="Reward strategy name or dotted path.",
    )
    referrer_params = models.JSONField(default=dict, blank=True, help_text="Strategy params for the referrer.")
    referee_params = models.JSONField(default=dict, blank=True, help_text="Strategy params for the referee.")
    code_kind = models.CharField(
        max_length=16, choices=RuleCodeKind.choices, default=RuleCodeKind.ANY,
        help_text="Which codes the rule rewards.",
    )
    campaign_code = models.ForeignKey(
        ReferralCode, on_delete=models.PROTECT, null=True, blank=True, related_name="rules",
        help_text="Only attributions through this code.",
    )
    starts_at = models.DateTimeField(null=True, blank=True, help_text="The rule fires from.")
    ends_at = models.DateTimeField(null=True, blank=True, help_text="The rule fires until (exclusive).")
    cap_per_referrer = models.PositiveIntegerField(
        null=True, blank=True, help_text="Rewarded triggers per referrer per period.",
    )
    cap_period_days = models.PositiveIntegerField(
        null=True, blank=True, help_text="Rolling period of the cap; NULL = all time.",
    )
    budget_count = models.PositiveIntegerField(null=True, blank=True, help_text="Rewarded triggers in total.")
    budget_value = _value_field(null=True, blank=True, help_text="Summed reward value in total.")
    hold_days = models.PositiveIntegerField(
        null=True, blank=True, help_text="Pending days before the grant; NULL = HOLD_DAYS for orders, 0 for signups.",
    )
    created_at = models.DateTimeField(auto_now_add=True, help_text="When the rule was created.")
    updated_at = models.DateTimeField(auto_now=True, help_text="Last change.")

    class Meta:
        app_label = "referrals"
        ordering = ["id"]

    def __str__(self) -> str:
        return self.name


@access.standard
class Reward(models.Model):
    rule = models.ForeignKey(RewardRule, on_delete=models.PROTECT, related_name="rewards",
                             help_text="The rule that fired.")
    attribution = models.ForeignKey(Attribution, on_delete=models.PROTECT, related_name="rewards",
                                    help_text="The referral this reward pays for.")
    trigger = models.CharField(max_length=16, choices=TriggerKind.choices, help_text="signup or order.")
    trigger_id = models.CharField(
        max_length=TRIGGER_ID_MAX_LENGTH,
        help_text="Referee id for a signup, order id for an order.",
    )
    role = models.CharField(max_length=16, choices=Role.choices, help_text="Who this row pays.")
    beneficiary_id = models.CharField(
        max_length=USER_ID_MAX_LENGTH, db_index=True, help_text="str(user.pk) paid.",
    )
    referrer_id = models.CharField(
        max_length=USER_ID_MAX_LENGTH, db_index=True, help_text="Snapshot for the per-referrer cap.",
    )
    status = models.CharField(
        max_length=16, choices=RewardStatus.choices, default=RewardStatus.PENDING, db_index=True,
        help_text="Moved only through the service (fsm in services/rewards.py).",
    )
    strategy = models.CharField(max_length=STRATEGY_MAX_LENGTH, help_text="Strategy at creation.")
    params = models.JSONField(default=dict, blank=True, help_text="Params snapshot at creation.")
    value = _value_field(default=0, help_text="Value counted against budget_value.")
    unit = models.CharField(max_length=UNIT_MAX_LENGTH, default="none", help_text="credits, code, none, ...")
    grant_ref = models.CharField(
        max_length=GRANT_REF_MAX_LENGTH, blank=True, default="",
        help_text="What the strategy returned (transaction id, issued code).",
    )
    reason = models.CharField(
        max_length=REASON_MAX_LENGTH, blank=True, default="",
        help_text="Why it was rejected or reversed.",
    )
    hold_until = models.DateTimeField(null=True, blank=True, db_index=True,
                                      help_text="Granted no earlier than this.")
    granted_at = models.DateTimeField(null=True, blank=True, help_text="When the strategy confirmed.")
    rejected_at = models.DateTimeField(null=True, blank=True, help_text="When it was rejected.")
    reversed_at = models.DateTimeField(null=True, blank=True, help_text="When the reversal was confirmed.")
    reverse_requested_at = models.DateTimeField(
        null=True, blank=True, help_text="A reversal waits for the strategy.",
    )
    reversal_outcome = models.CharField(
        max_length=16, choices=ReversalOutcome.choices, blank=True, default="",
        help_text="What the reversal took back; empty until reversed_at.",
    )
    reversed_value = _value_field(
        null=True, blank=True,
        help_text="Value taken back; the strategy's plan while a reversal is in flight.",
    )
    attempts = models.PositiveIntegerField(default=0, help_text="Strategy calls made.")
    last_error = models.CharField(
        max_length=ERROR_MAX_LENGTH, blank=True, default="",
        help_text="Last infrastructure failure (never a domain decision).",
    )
    created_at = models.DateTimeField(auto_now_add=True, help_text="When the trigger was recorded.")
    updated_at = models.DateTimeField(auto_now=True, help_text="Last change.")

    class Meta:
        app_label = "referrals"
        ordering = ["-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["rule", "attribution", "trigger_id", "role"],
                name="referrals_reward_idempotent",
            ),
        ]
        indexes = [
            models.Index(fields=["rule", "referrer_id", "created_at"], name="referrals_reward_cap"),
            models.Index(fields=["beneficiary_id", "-id"], name="referrals_reward_mine"),
        ]

    def __str__(self) -> str:
        return f"Reward {self.pk} {self.role} {self.status}"


@access.ops
class SignupMarker(models.Model):
    """``user.signed_up`` was seen for this account."""

    user_id = models.CharField(max_length=USER_ID_MAX_LENGTH, unique=True, help_text="The account.")
    signed_up_at = models.DateTimeField(help_text="registered_at from the fact (auth's clock).")
    auth_type = models.CharField(
        max_length=32, blank=True, default="",
        help_text="auth_type from the fact: the identity a paying brick may materialise the user from.",
    )

    class Meta:
        app_label = "referrals"


@access.ops
class QualifyingOrder(models.Model):
    """An order a referee placed inside the qualifying window."""

    order_id = models.CharField(max_length=TRIGGER_ID_MAX_LENGTH, unique=True, help_text="orders' id.")
    attribution = models.ForeignKey(Attribution, on_delete=models.CASCADE, related_name="orders",
                                    help_text="The referral it counts for.")
    total = _value_field(help_text="Order total at placement.")
    currency = models.CharField(max_length=CURRENCY_MAX_LENGTH, help_text="Order currency.")
    placed_at = models.DateTimeField(help_text="placed_at from the fact.")
    cancelled_at = models.DateTimeField(null=True, blank=True, help_text="Cancelled orders stop counting.")

    class Meta:
        app_label = "referrals"
        ordering = ["placed_at", "id"]


@access.ops
class DeviceSighting(models.Model):
    """A device hash seen on a user (memory of the ``same_device`` guard)."""

    user_id = models.CharField(max_length=USER_ID_MAX_LENGTH, help_text="The account.")
    device_hash = models.CharField(max_length=DEVICE_HASH_MAX_LENGTH, help_text="HMAC of the client device id.")
    seen_at = models.DateTimeField(help_text="Last time seen.")

    class Meta:
        app_label = "referrals"
        constraints = [
            models.UniqueConstraint(fields=["user_id", "device_hash"], name="referrals_device_once"),
        ]
        indexes = [models.Index(fields=["device_hash"], name="referrals_device_hash")]
