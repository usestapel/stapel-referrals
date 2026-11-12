"""v1 URL set for stapel-referrals (plain paths, no router)."""
from typing import NamedTuple

from django.urls import path

from .views import (
    CampaignCodeDetailView,
    CampaignCodeListView,
    ClaimView,
    CodeLookupView,
    MeView,
    MyReferralsView,
    MyRewardsView,
    RuleDetailView,
    RuleListView,
    StaffRewardListView,
    StaffRewardTransitionView,
    StatsView,
    StrategyListView,
    VanityCodeView,
)

urlpatterns = [
    path("me/", MeView.as_view(), name="referrals-me"),
    path("me/code/", VanityCodeView.as_view(), name="referrals-me-code"),
    path("me/referrals/", MyReferralsView.as_view(), name="referrals-me-referrals"),
    path("me/rewards/", MyRewardsView.as_view(), name="referrals-me-rewards"),
    path("claim/", ClaimView.as_view(), name="referrals-claim"),
    path("codes/<str:code>/", CodeLookupView.as_view(), name="referrals-code"),
    path("staff/rules/", RuleListView.as_view(), name="referrals-staff-rules"),
    path("staff/rules/<int:rule_id>/", RuleDetailView.as_view(), name="referrals-staff-rule"),
    path("staff/campaign-codes/", CampaignCodeListView.as_view(), name="referrals-staff-campaign-codes"),
    path("staff/campaign-codes/<int:code_id>/", CampaignCodeDetailView.as_view(),
         name="referrals-staff-campaign-code"),
    path("staff/rewards/", StaffRewardListView.as_view(), name="referrals-staff-rewards"),
    path("staff/rewards/<int:reward_id>/transition/", StaffRewardTransitionView.as_view(),
         name="referrals-staff-reward-transition"),
    path("staff/stats/", StatsView.as_view(), name="referrals-staff-stats"),
    path("staff/strategies/", StrategyListView.as_view(), name="referrals-staff-strategies"),
]


class GateEntry(NamedTuple):
    """One gated URL block: which flags gate which url patterns."""

    name: str
    flags: tuple
    patterns: tuple


#: No axis unmounts an endpoint (VANITY_CODES, CLAIM_REQUIRES_ACCOUNT,
#: HOLD_DAYS and REFEREE_DISPLAY are behavioural), so the surface is one
#: always-on block.
GATE_REGISTRY: dict = {
    "referrals.api": GateEntry("referrals.api", (), tuple(urlpatterns)),
}
