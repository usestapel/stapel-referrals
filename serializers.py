"""Serializers for the stapel-referrals API.

The class names are the codegen contract: drf-spectacular names each
component after its serializer minus the ``Serializer`` suffix, and the
frontend pair generates its client from those names. Every CharField
declares a ``max_length``; values are decimals rendered as strings;
instants are ISO 8601 in UTC.
"""
import datetime

from rest_framework import serializers

from . import models as m

UTC = datetime.timezone.utc


def _value(**kwargs):
    return serializers.DecimalField(
        max_digits=m.VALUE_MAX_DIGITS, decimal_places=m.VALUE_DECIMAL_PLACES,
        coerce_to_string=True, **kwargs,
    )


def _instant(**kwargs):
    return serializers.DateTimeField(default_timezone=UTC, **kwargs)


# ── Referee and referrer ──────────────────────────────────────────────────


class ReferralCountsSerializer(serializers.Serializer):
    referrals = serializers.IntegerField(help_text="Referees attributed to my codes.")
    rewarded_referrals = serializers.IntegerField(help_text="Referees that earned me a granted reward.")
    pending_rewards = serializers.IntegerField()
    granted_rewards = serializers.IntegerField()


class RewardTotalSerializer(serializers.Serializer):
    unit = serializers.CharField(max_length=m.UNIT_MAX_LENGTH)
    value = _value()


class MyReferralsResponseSerializer(serializers.Serializer):
    code = serializers.CharField(max_length=m.CODE_MAX_LENGTH, help_text="My primary personal code.")
    link = serializers.CharField(max_length=500, allow_null=True,
                                 help_text="Share link from SHARE_URL_TEMPLATE; null when unset.")
    vanity_allowed = serializers.BooleanField(help_text="I may choose my own code (PATCH me/code/).")
    referred = serializers.BooleanField(help_text="I was attributed to someone's code.")
    counts = ReferralCountsSerializer()
    rewards_total = RewardTotalSerializer(many=True, help_text="Granted to me, summed per unit.")


class VanityCodeRequestSerializer(serializers.Serializer):
    code = serializers.CharField(max_length=m.CODE_MAX_LENGTH, min_length=4)


class ClaimRequestSerializer(serializers.Serializer):
    code = serializers.CharField(max_length=m.CODE_MAX_LENGTH)
    captured_at = _instant(required=False, allow_null=True,
                           help_text="When the client stored the code (first touch).")
    device_id = serializers.CharField(max_length=128, required=False, allow_blank=True, default="",
                                      help_text="Opaque client device id; only its HMAC is kept.")


class ClaimResponseSerializer(serializers.Serializer):
    code = serializers.CharField(max_length=m.CODE_MAX_LENGTH)
    code_kind = serializers.ChoiceField(choices=m.CodeKind.choices)
    attributed_at = _instant()
    referrer_label = serializers.CharField(max_length=m.LABEL_MAX_LENGTH, allow_null=True)


class CodeLookupResponseSerializer(serializers.Serializer):
    code = serializers.CharField(max_length=m.CODE_MAX_LENGTH)
    valid = serializers.BooleanField(help_text="A claim of this code would pass the code checks now.")
    kind = serializers.ChoiceField(choices=m.CodeKind.choices, allow_null=True)
    referrer_label = serializers.CharField(max_length=m.LABEL_MAX_LENGTH, allow_null=True)


class ReferralItemSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    referee_label = serializers.CharField(max_length=m.LABEL_MAX_LENGTH, allow_null=True,
                                          help_text="Masked by REFEREE_DISPLAY; null when hidden.")
    code_kind = serializers.ChoiceField(choices=m.CodeKind.choices)
    attributed_at = _instant()
    rewards_granted = serializers.IntegerField()
    rewards_pending = serializers.IntegerField()


class _PageSerializer(serializers.Serializer):
    next_anchor = serializers.CharField(max_length=64, allow_null=True)
    prev_anchor = serializers.CharField(max_length=64, allow_null=True)
    has_next = serializers.BooleanField()
    has_prev = serializers.BooleanField()
    count = serializers.IntegerField()


class ReferralPageSerializer(_PageSerializer):
    items = ReferralItemSerializer(many=True)


class RewardResponseSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    rule_id = serializers.IntegerField()
    rule_name = serializers.CharField(max_length=m.LABEL_MAX_LENGTH)
    role = serializers.ChoiceField(choices=m.Role.choices)
    trigger = serializers.ChoiceField(choices=m.TriggerKind.choices)
    status = serializers.ChoiceField(choices=m.RewardStatus.choices)
    value = _value()
    unit = serializers.CharField(max_length=m.UNIT_MAX_LENGTH)
    grant_ref = serializers.CharField(max_length=m.GRANT_REF_MAX_LENGTH, allow_blank=True,
                                      help_text="Issued code or transaction id.")
    reason = serializers.CharField(max_length=m.REASON_MAX_LENGTH, allow_blank=True)
    hold_until = _instant(allow_null=True)
    granted_at = _instant(allow_null=True)
    created_at = _instant()


class RewardPageSerializer(_PageSerializer):
    items = RewardResponseSerializer(many=True)


# ── Staff ─────────────────────────────────────────────────────────────────


class StaffRewardResponseSerializer(RewardResponseSerializer):
    attribution_id = serializers.IntegerField()
    beneficiary_id = serializers.CharField(max_length=m.USER_ID_MAX_LENGTH)
    referrer_id = serializers.CharField(max_length=m.USER_ID_MAX_LENGTH)
    trigger_id = serializers.CharField(max_length=m.TRIGGER_ID_MAX_LENGTH)
    strategy = serializers.CharField(max_length=m.STRATEGY_MAX_LENGTH)
    attempts = serializers.IntegerField()
    last_error = serializers.CharField(max_length=m.ERROR_MAX_LENGTH, allow_blank=True)
    reverse_requested_at = _instant(allow_null=True)
    reversed_at = _instant(allow_null=True)
    reversal_outcome = serializers.ChoiceField(
        choices=m.ReversalOutcome.choices, allow_null=True,
        help_text="Null until reversed: reversed, reversed_partial (the rest was spent) or already_spent.",
    )
    reversed_value = _value(allow_null=True, help_text="Value taken back; null until reversed.")


class StaffRewardPageSerializer(_PageSerializer):
    items = StaffRewardResponseSerializer(many=True)


class RewardTransitionRequestSerializer(serializers.Serializer):
    to = serializers.ChoiceField(choices=[(s, s) for s in ("granted", "rejected", "reversed")])
    reason = serializers.CharField(max_length=m.REASON_MAX_LENGTH, required=False, allow_blank=True, default="")


class _RuleFieldsSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=m.LABEL_MAX_LENGTH)
    active = serializers.BooleanField(default=True)
    trigger = serializers.ChoiceField(choices=m.Trigger.choices)
    order_ordinal = serializers.IntegerField(min_value=1, max_value=1000, default=1)
    min_order_total = _value(allow_null=True, required=False, min_value=0)
    currency = serializers.RegexField(r"^[A-Za-z]{3}$", max_length=m.CURRENCY_MAX_LENGTH,
                                      allow_blank=True, required=False, default="")
    beneficiary = serializers.ChoiceField(choices=m.Beneficiary.choices, default=m.Beneficiary.BOTH)
    strategy = serializers.CharField(max_length=m.STRATEGY_MAX_LENGTH, default="noop")
    referrer_params = serializers.DictField(required=False, default=dict)
    referee_params = serializers.DictField(required=False, default=dict)
    code_kind = serializers.ChoiceField(choices=m.RuleCodeKind.choices, default=m.RuleCodeKind.ANY)
    campaign_code = serializers.IntegerField(allow_null=True, required=False,
                                             help_text="Id of a campaign code; only its attributions.")
    starts_at = _instant(allow_null=True, required=False)
    ends_at = _instant(allow_null=True, required=False)
    cap_per_referrer = serializers.IntegerField(min_value=1, allow_null=True, required=False)
    cap_period_days = serializers.IntegerField(min_value=1, max_value=3660, allow_null=True, required=False)
    budget_count = serializers.IntegerField(min_value=0, allow_null=True, required=False)
    budget_value = _value(allow_null=True, required=False, min_value=0)
    hold_days = serializers.IntegerField(min_value=0, max_value=365, allow_null=True, required=False)


class RewardRuleRequestSerializer(_RuleFieldsSerializer):
    pass


class RuleRewardCountsSerializer(serializers.Serializer):
    pending = serializers.IntegerField()
    granted = serializers.IntegerField()
    rejected = serializers.IntegerField()
    reversed = serializers.IntegerField()


class RewardRuleResponseSerializer(_RuleFieldsSerializer):
    id = serializers.IntegerField()
    rewards = RuleRewardCountsSerializer()
    created_at = _instant()
    updated_at = _instant()


class CampaignCodeRequestSerializer(serializers.Serializer):
    code = serializers.CharField(max_length=m.CODE_MAX_LENGTH, required=False, allow_blank=True,
                                 help_text="Chosen code; minted when empty. Immutable once created.")
    label = serializers.CharField(max_length=m.LABEL_MAX_LENGTH, required=False, allow_blank=True)
    owner_id = serializers.CharField(max_length=m.USER_ID_MAX_LENGTH, required=False,
                                     help_text="Who is the referrer; default the staff user.")
    active = serializers.BooleanField(required=False)
    starts_at = _instant(allow_null=True, required=False)
    ends_at = _instant(allow_null=True, required=False)
    max_attributions = serializers.IntegerField(min_value=1, allow_null=True, required=False)


class CampaignCodeResponseSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    code = serializers.CharField(max_length=m.CODE_MAX_LENGTH)
    label = serializers.CharField(max_length=m.LABEL_MAX_LENGTH, allow_blank=True)
    owner_id = serializers.CharField(max_length=m.USER_ID_MAX_LENGTH)
    active = serializers.BooleanField()
    starts_at = _instant(allow_null=True)
    ends_at = _instant(allow_null=True)
    max_attributions = serializers.IntegerField(allow_null=True)
    attribution_count = serializers.IntegerField()
    link = serializers.CharField(max_length=500, allow_null=True)
    created_at = _instant()


class CampaignCodePageSerializer(_PageSerializer):
    items = CampaignCodeResponseSerializer(many=True)


class TopReferrerSerializer(serializers.Serializer):
    referrer_id = serializers.CharField(max_length=m.USER_ID_MAX_LENGTH)
    referrals = serializers.IntegerField()


class ReferralStatsResponseSerializer(serializers.Serializer):
    personal_codes = serializers.IntegerField()
    campaign_codes = serializers.IntegerField()
    attributions = serializers.IntegerField()
    attributions_refused = serializers.IntegerField()
    active_rules = serializers.IntegerField()
    rewards = RuleRewardCountsSerializer()
    granted_value = RewardTotalSerializer(many=True)
    top_referrers = TopReferrerSerializer(many=True)
