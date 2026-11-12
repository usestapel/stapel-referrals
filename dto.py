"""Dataclass DTOs — the API models of stapel-referrals (never ORM instances)."""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal


@dataclass
class ReferralCountsDTO:
    """How many referees and rewards a member has."""

    referrals: int
    rewarded_referrals: int
    pending_rewards: int
    granted_rewards: int


@dataclass
class RewardTotalDTO:
    """Granted value summed for one unit."""

    unit: str
    value: Decimal


@dataclass
class MyReferralsDTO:
    """A member's referral dashboard: code, link, counts, totals."""

    code: str
    link: str | None
    vanity_allowed: bool
    referred: bool
    counts: ReferralCountsDTO
    rewards_total: list[RewardTotalDTO] = field(default_factory=list)


@dataclass
class ClaimDTO:
    """The attribution a claim made (or replayed)."""

    code: str
    code_kind: str
    attributed_at: dt.datetime
    referrer_label: str | None


@dataclass
class CodeLookupDTO:
    """Whether a code is claimable now, and whose it is (masked)."""

    code: str
    valid: bool
    kind: str | None
    referrer_label: str | None


@dataclass
class ReferralItemDTO:
    """One referee of mine, labelled by REFEREE_DISPLAY."""

    id: int
    referee_label: str | None
    code_kind: str
    attributed_at: dt.datetime
    rewards_granted: int
    rewards_pending: int


@dataclass
class RewardDTO:
    """One reward-ledger row as its beneficiary sees it."""

    id: int
    rule_id: int
    rule_name: str
    role: str
    trigger: str
    status: str
    value: Decimal
    unit: str
    grant_ref: str
    reason: str
    hold_until: dt.datetime | None
    granted_at: dt.datetime | None
    created_at: dt.datetime


@dataclass
class StaffRewardDTO(RewardDTO):
    """One reward-ledger row with the fields staff need."""

    attribution_id: int = 0
    beneficiary_id: str = ""
    referrer_id: str = ""
    trigger_id: str = ""
    strategy: str = ""
    attempts: int = 0
    last_error: str = ""
    reverse_requested_at: dt.datetime | None = None
    reversed_at: dt.datetime | None = None
    reversal_outcome: str | None = None
    reversed_value: Decimal | None = None


@dataclass
class RuleRewardCountsDTO:
    """Rewards per status."""

    pending: int = 0
    granted: int = 0
    rejected: int = 0
    reversed: int = 0


@dataclass
class RewardRuleDTO:
    """A reward rule with its ledger counts."""

    id: int
    name: str
    active: bool
    trigger: str
    order_ordinal: int
    min_order_total: Decimal | None
    currency: str
    beneficiary: str
    strategy: str
    referrer_params: dict
    referee_params: dict
    code_kind: str
    campaign_code: int | None
    starts_at: dt.datetime | None
    ends_at: dt.datetime | None
    cap_per_referrer: int | None
    cap_period_days: int | None
    budget_count: int | None
    budget_value: Decimal | None
    hold_days: int | None
    rewards: RuleRewardCountsDTO
    created_at: dt.datetime
    updated_at: dt.datetime


@dataclass
class CampaignCodeDTO:
    """A staff campaign code."""

    id: int
    code: str
    label: str
    owner_id: str
    active: bool
    starts_at: dt.datetime | None
    ends_at: dt.datetime | None
    max_attributions: int | None
    attribution_count: int
    link: str | None
    created_at: dt.datetime


@dataclass
class TopReferrerDTO:
    """A referrer and how many referees they brought."""

    referrer_id: str
    referrals: int


@dataclass
class ReferralStatsDTO:
    """Programme statistics for staff."""

    personal_codes: int
    campaign_codes: int
    attributions: int
    attributions_refused: int
    active_rules: int
    rewards: RuleRewardCountsDTO
    granted_value: list[RewardTotalDTO]
    top_referrers: list[TopReferrerDTO]


@dataclass
class RewardStrategyDTO:
    """A registered reward strategy as the staff rule form needs it."""

    name: str
    unit: str
    builtin: bool
    description: str
    params_schema: dict


@dataclass
class RewardStrategyListDTO:
    """Every registered reward strategy."""

    items: list[RewardStrategyDTO]
