"""Reward rules (staff): validation and writes.

A rule is checked whole on every write — the strategy must resolve and
clean each beneficiary's params — so a rule that cannot fire never lands.
"""
from __future__ import annotations

from django.db import transaction

from ..errors import (
    ERR_400_RULE_INVALID,
    ERR_400_STRATEGY_PARAMS_INVALID,
    ERR_400_STRATEGY_UNKNOWN,
    ERR_404_RULE_NOT_FOUND,
)
from ..models import Beneficiary, CodeKind, ReferralCode, RewardRule, Trigger
from ..strategies import get_strategy
from . import ReferralsError

WRITABLE = (
    "name", "active", "trigger", "order_ordinal", "min_order_total", "currency", "beneficiary",
    "strategy", "referrer_params", "referee_params", "code_kind", "campaign_code",
    "starts_at", "ends_at", "cap_per_referrer", "cap_period_days", "budget_count",
    "budget_value", "hold_days",
)


def _invalid(problem: str) -> ReferralsError:
    return ReferralsError(400, ERR_400_RULE_INVALID, {"problem": problem})


def clean_values(values: dict) -> dict:
    try:
        strategy = get_strategy(values["strategy"])
    except LookupError:
        raise ReferralsError(400, ERR_400_STRATEGY_UNKNOWN, {"strategy": values["strategy"]}) from None
    roles = []
    if values["beneficiary"] in (Beneficiary.REFERRER, Beneficiary.BOTH):
        roles.append("referrer_params")
    if values["beneficiary"] in (Beneficiary.REFEREE, Beneficiary.BOTH):
        roles.append("referee_params")
    for key in roles:
        try:
            values[key] = strategy.clean_params(values.get(key) or {})
        except ValueError as exc:
            raise ReferralsError(400, ERR_400_STRATEGY_PARAMS_INVALID,
                                 {"problem": f"{key}: {exc}"}) from None
    if values["trigger"] == Trigger.FIRST_ORDER:
        values["order_ordinal"] = 1
    elif values["trigger"] == Trigger.SIGNUP:
        values["order_ordinal"] = 1
        if values.get("min_order_total") is not None:
            raise _invalid("min_order_total applies to order triggers only")
    if values.get("min_order_total") is not None and not values.get("currency"):
        raise _invalid("min_order_total needs a currency")
    if values.get("currency"):
        values["currency"] = str(values["currency"]).upper()
    if values.get("starts_at") and values.get("ends_at") and values["ends_at"] <= values["starts_at"]:
        raise _invalid("ends_at must be after starts_at")
    if values.get("cap_period_days") and values.get("cap_per_referrer") is None:
        raise _invalid("cap_period_days needs cap_per_referrer")
    code = values.get("campaign_code")
    if code is not None and code.kind != CodeKind.CAMPAIGN:
        raise _invalid("campaign_code must be a campaign code")
    return values


def resolve_campaign_code(code_id):
    if code_id in (None, ""):
        return None
    code = ReferralCode.objects.filter(pk=code_id).first()
    if code is None:
        raise _invalid("campaign_code does not exist")
    return code


def create(data: dict) -> RewardRule:
    values = {k: data[k] for k in WRITABLE if k in data and k != "campaign_code"}
    values.setdefault("strategy", "noop")
    values.setdefault("beneficiary", Beneficiary.BOTH)
    values["campaign_code"] = resolve_campaign_code(data.get("campaign_code"))
    values = clean_values(values)
    return RewardRule.objects.create(**values)


def get(rule_id) -> RewardRule:
    rule = RewardRule.objects.filter(pk=rule_id).first()
    if rule is None:
        raise ReferralsError(404, ERR_404_RULE_NOT_FOUND)
    return rule


def update(rule_id, data: dict) -> RewardRule:
    with transaction.atomic():
        rule = RewardRule.objects.select_for_update().filter(pk=rule_id).first()
        if rule is None:
            raise ReferralsError(404, ERR_404_RULE_NOT_FOUND)
        values = {k: getattr(rule, k) for k in WRITABLE}
        for key in WRITABLE:
            if key in data:
                values[key] = data[key] if key != "campaign_code" else resolve_campaign_code(data[key])
        values = clean_values(values)
        for key, value in values.items():
            setattr(rule, key, value)
        rule.save()
    return rule
