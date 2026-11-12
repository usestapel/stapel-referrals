"""Presenters for stapel-referrals — the DTO-building layer (§55).

Views never instantiate a ``dto.py`` dataclass (SWAP002) nor import a
concrete presenter (SWAP001); they resolve one through ``get_presenter`` so
a host can swap the presentation via ``STAPEL_SWAP`` without forking.
"""
from __future__ import annotations

from stapel_core.django.swappable import declare_swap, get_presenter

from .dto import (
    CampaignCodeDTO,
    ClaimDTO,
    CodeLookupDTO,
    MyReferralsDTO,
    ReferralCountsDTO,
    ReferralItemDTO,
    ReferralStatsDTO,
    RewardDTO,
    RewardRuleDTO,
    RewardTotalDTO,
    RuleRewardCountsDTO,
    StaffRewardDTO,
    TopReferrerDTO,
)

ME_PRESENTER_KEY = "STAPEL_REFERRALS_ME_PRESENTER"
CLAIM_PRESENTER_KEY = "STAPEL_REFERRALS_CLAIM_PRESENTER"
REFERRAL_PRESENTER_KEY = "STAPEL_REFERRALS_REFERRAL_PRESENTER"
REWARD_PRESENTER_KEY = "STAPEL_REFERRALS_REWARD_PRESENTER"
STAFF_PRESENTER_KEY = "STAPEL_REFERRALS_STAFF_PRESENTER"

DEFAULT_ME_PRESENTER = "stapel_referrals.presenters.MyReferralsPresenter"
DEFAULT_CLAIM_PRESENTER = "stapel_referrals.presenters.ClaimPresenter"
DEFAULT_REFERRAL_PRESENTER = "stapel_referrals.presenters.ReferralPresenter"
DEFAULT_REWARD_PRESENTER = "stapel_referrals.presenters.RewardPresenter"
DEFAULT_STAFF_PRESENTER = "stapel_referrals.presenters.StaffPresenter"

declare_swap(ME_PRESENTER_KEY, DEFAULT_ME_PRESENTER)
declare_swap(CLAIM_PRESENTER_KEY, DEFAULT_CLAIM_PRESENTER)
declare_swap(REFERRAL_PRESENTER_KEY, DEFAULT_REFERRAL_PRESENTER)
declare_swap(REWARD_PRESENTER_KEY, DEFAULT_REWARD_PRESENTER)
declare_swap(STAFF_PRESENTER_KEY, DEFAULT_STAFF_PRESENTER)


def _totals(rows) -> list[RewardTotalDTO]:
    return [RewardTotalDTO(unit=r["unit"], value=r["value"]) for r in rows]


class MyReferralsPresenter:
    """The referrer's dashboard: code, link, counts, totals."""

    def present(self, *, code, link, vanity_allowed, referred, counts, totals) -> MyReferralsDTO:
        return MyReferralsDTO(
            code=code.code, link=link, vanity_allowed=vanity_allowed, referred=referred,
            counts=ReferralCountsDTO(**counts), rewards_total=_totals(totals),
        )


class ClaimPresenter:
    def present_claim(self, attribution, referrer_label) -> ClaimDTO:
        return ClaimDTO(
            code=attribution.code.code, code_kind=attribution.code_kind,
            attributed_at=attribution.attributed_at, referrer_label=referrer_label,
        )

    def present_lookup(self, code_text, code, valid, referrer_label) -> CodeLookupDTO:
        return CodeLookupDTO(
            code=code.code if code is not None else code_text,
            valid=valid, kind=code.kind if code is not None else None,
            referrer_label=referrer_label if valid else None,
        )


class ReferralPresenter:
    def present(self, attribution, label) -> ReferralItemDTO:
        return ReferralItemDTO(
            id=attribution.pk, referee_label=label, code_kind=attribution.code_kind,
            attributed_at=attribution.attributed_at,
            rewards_granted=getattr(attribution, "rewards_granted", 0),
            rewards_pending=getattr(attribution, "rewards_pending", 0),
        )


def _reward_fields(reward) -> dict:
    return dict(
        id=reward.pk, rule_id=reward.rule_id, rule_name=reward.rule.name, role=reward.role,
        trigger=reward.trigger, status=reward.status, value=reward.value, unit=reward.unit,
        grant_ref=reward.grant_ref, reason=reward.reason, hold_until=reward.hold_until,
        granted_at=reward.granted_at, created_at=reward.created_at,
    )


class RewardPresenter:
    def present(self, reward) -> RewardDTO:
        return RewardDTO(**_reward_fields(reward))


class StaffPresenter:
    """Staff views: the full reward row, rules with counts, campaign codes, stats."""

    def present_reward(self, reward) -> StaffRewardDTO:
        return StaffRewardDTO(
            **_reward_fields(reward),
            attribution_id=reward.attribution_id, beneficiary_id=reward.beneficiary_id,
            referrer_id=reward.referrer_id, trigger_id=reward.trigger_id, strategy=reward.strategy,
            attempts=reward.attempts, last_error=reward.last_error,
            reverse_requested_at=reward.reverse_requested_at, reversed_at=reward.reversed_at,
            reversal_outcome=reward.reversal_outcome or None,
            reversed_value=reward.reversed_value if reward.reversed_at else None,
        )

    def present_rule(self, rule, counts: dict) -> RewardRuleDTO:
        return RewardRuleDTO(
            id=rule.pk, name=rule.name, active=rule.active, trigger=rule.trigger,
            order_ordinal=rule.order_ordinal, min_order_total=rule.min_order_total,
            currency=rule.currency, beneficiary=rule.beneficiary, strategy=rule.strategy,
            referrer_params=rule.referrer_params or {}, referee_params=rule.referee_params or {},
            code_kind=rule.code_kind, campaign_code=rule.campaign_code_id,
            starts_at=rule.starts_at, ends_at=rule.ends_at,
            cap_per_referrer=rule.cap_per_referrer, cap_period_days=rule.cap_period_days,
            budget_count=rule.budget_count, budget_value=rule.budget_value, hold_days=rule.hold_days,
            rewards=RuleRewardCountsDTO(**counts), created_at=rule.created_at, updated_at=rule.updated_at,
        )

    def present_campaign(self, code, link) -> CampaignCodeDTO:
        return CampaignCodeDTO(
            id=code.pk, code=code.code, label=code.label, owner_id=code.owner_id, active=code.active,
            starts_at=code.starts_at, ends_at=code.ends_at, max_attributions=code.max_attributions,
            attribution_count=code.attribution_count, link=link, created_at=code.created_at,
        )

    def present_stats(self, data: dict) -> ReferralStatsDTO:
        return ReferralStatsDTO(
            personal_codes=data["personal_codes"], campaign_codes=data["campaign_codes"],
            attributions=data["attributions"], attributions_refused=data["attributions_refused"],
            active_rules=data["active_rules"], rewards=RuleRewardCountsDTO(**data["rewards"]),
            granted_value=_totals(data["granted_value"]),
            top_referrers=[TopReferrerDTO(**row) for row in data["top_referrers"]],
        )


def get_me_presenter():
    return get_presenter(ME_PRESENTER_KEY, default=DEFAULT_ME_PRESENTER)


def get_claim_presenter():
    return get_presenter(CLAIM_PRESENTER_KEY, default=DEFAULT_CLAIM_PRESENTER)


def get_referral_presenter():
    return get_presenter(REFERRAL_PRESENTER_KEY, default=DEFAULT_REFERRAL_PRESENTER)


def get_reward_presenter():
    return get_presenter(REWARD_PRESENTER_KEY, default=DEFAULT_REWARD_PRESENTER)


def get_staff_presenter():
    return get_presenter(STAFF_PRESENTER_KEY, default=DEFAULT_STAFF_PRESENTER)
