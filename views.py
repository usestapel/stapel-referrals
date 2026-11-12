"""DRF views for stapel-referrals — thin over ``services``.

Every view declares its guest stance. The referrer's and referee's views
admit a guest session (a stapel-auth guest row is authenticated); the claim
enforces ``CLAIM_REQUIRES_ACCOUNT`` in the body; a vanity change is refused
to guests by the policy; the code lookup is public; staff views refuse
guests and non-staff.
"""
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import permissions
from stapel_core.django.api.errors import StapelErrorResponse
from stapel_core.django.api.permissions import ANONYMOUS_ALLOWED, ANONYMOUS_DENIED, IsStaffUser
from stapel_core.django.api.views import StapelAPIView
from stapel_core.flows import flow_step

from . import codes as code_text
from .flows import CLAIM, INVITE, STAFF
from .models import CodeKind, ReferralCode, RewardRule, RewardStatus
from .pagination import IdAnchorPagination
from .presenters import (
    get_claim_presenter,
    get_me_presenter,
    get_referral_presenter,
    get_reward_presenter,
    get_staff_presenter,
)
from .serializers import (
    CampaignCodePageSerializer,
    CampaignCodeRequestSerializer,
    CampaignCodeResponseSerializer,
    ClaimRequestSerializer,
    ClaimResponseSerializer,
    CodeLookupResponseSerializer,
    MyReferralsResponseSerializer,
    ReferralItemSerializer,
    ReferralPageSerializer,
    ReferralStatsResponseSerializer,
    RewardStrategyListSerializer,
    RewardPageSerializer,
    RewardResponseSerializer,
    RewardRuleRequestSerializer,
    RewardRuleResponseSerializer,
    RewardTransitionRequestSerializer,
    StaffRewardPageSerializer,
    StaffRewardResponseSerializer,
    VanityCodeRequestSerializer,
)
from .services import ReferralsError
from .services import attribution as attribution_service
from .services import codes as code_service
from .services import queries
from .services import rewards as reward_service
from .services import rules as rule_service

TAGS = ["Referrals"]
STAFF_TAGS = ["Referrals staff"]


class _ReferralsView(StapelAPIView):
    """Renders a service refusal as the fleet error envelope."""

    def handle_exception(self, exc):
        if isinstance(exc, ReferralsError):
            return StapelErrorResponse(exc.status, exc.key, exc.params)
        return super().handle_exception(exc)

    def _page(self, request, qs, serializer_class, present):
        paginator = IdAnchorPagination()
        page = paginator.paginate_queryset(qs, request, view=self)
        data = serializer_class([present(row) for row in page], many=True).data
        return paginator.get_paginated_response(data)


def _anchor_parameters():
    p = IdAnchorPagination
    return [
        OpenApiParameter("anchor", str, OpenApiParameter.QUERY, required=False,
                         description="Opaque anchor from next_anchor/prev_anchor (exclusive)."),
        OpenApiParameter("limit", int, OpenApiParameter.QUERY, required=False,
                         description=f"Page size, default {p.page_size}, max {p.max_page_size}."),
        OpenApiParameter("direction", str, OpenApiParameter.QUERY, required=False,
                         enum=["next", "prev"], description="Walk direction from the anchor."),
    ]


def _me(request):
    user_id = str(request.user.pk)
    code = code_service.primary_code(user_id)
    return get_me_presenter()().present(
        code=code,
        link=code_text.share_link(code.code),
        vanity_allowed=code_service.vanity_allowed(request.user),
        referred=attribution_service.for_referee(user_id) is not None,
        counts=queries.my_counts(user_id),
        totals=queries.granted_totals(queries.my_rewards(user_id)),
    )


@extend_schema(tags=TAGS)
class MeView(_ReferralsView):
    """My code (minted on first read), share link, counts and reward totals."""

    permission_classes = [permissions.IsAuthenticated]
    stapel_anonymous_access = ANONYMOUS_ALLOWED
    response_serializer_class = MyReferralsResponseSerializer

    @extend_schema(
        operation_id="referrals_me_retrieve",
        parameters=[OpenApiParameter(
            "device_id", str, OpenApiParameter.QUERY, required=False,
            description="Opaque client device id (same_device guard memory; only its HMAC is kept).",
        )],
        responses={200: MyReferralsResponseSerializer},
    )
    @flow_step(INVITE, order=1, note="See my code, link and counts")
    def get(self, request):
        device_id = (request.query_params.get("device_id") or "")[:128]
        if device_id:
            attribution_service.record_device(str(request.user.pk), device_id)
        return self.serialized_response(_me(request))


@extend_schema(tags=TAGS)
class VanityCodeView(_ReferralsView):
    """Choose my own code (VANITY_CODES policy); the old code stays claimable."""

    permission_classes = [permissions.IsAuthenticated]
    stapel_anonymous_access = ANONYMOUS_ALLOWED
    request_serializer_class = VanityCodeRequestSerializer
    response_serializer_class = MyReferralsResponseSerializer

    @extend_schema(operation_id="referrals_me_code_update", request=VanityCodeRequestSerializer,
                   responses={200: MyReferralsResponseSerializer})
    @flow_step(INVITE, order=2, note="Pick a vanity code when the policy allows")
    def patch(self, request):
        data = self.validated_request_data(request)
        code_service.set_vanity(request.user, data["code"])
        return self.serialized_response(_me(request))


@extend_schema(tags=TAGS)
class MyReferralsView(_ReferralsView):
    """The accounts my codes brought in, newest first, labels masked by policy."""

    permission_classes = [permissions.IsAuthenticated]
    stapel_anonymous_access = ANONYMOUS_ALLOWED
    response_serializer_class = ReferralItemSerializer

    @extend_schema(operation_id="referrals_me_referrals_list", parameters=_anchor_parameters(),
                   responses={200: ReferralPageSerializer})
    @flow_step(INVITE, order=3, note="Follow the friends who joined")
    def get(self, request):
        qs = queries.my_referrals(str(request.user.pk))
        paginator = IdAnchorPagination()
        page = paginator.paginate_queryset(qs, request, view=self)
        labels = queries.referee_labels([a.referee_id for a in page])
        presenter = get_referral_presenter()()
        data = ReferralItemSerializer(
            [presenter.present(a, labels.get(a.referee_id)) for a in page], many=True
        ).data
        return paginator.get_paginated_response(data)


@extend_schema(tags=TAGS)
class MyRewardsView(_ReferralsView):
    """Rewards paid (or waiting) to me, as referrer or referee."""

    permission_classes = [permissions.IsAuthenticated]
    stapel_anonymous_access = ANONYMOUS_ALLOWED
    response_serializer_class = RewardResponseSerializer

    @extend_schema(operation_id="referrals_me_rewards_list", parameters=_anchor_parameters(),
                   responses={200: RewardPageSerializer})
    @flow_step(INVITE, order=4, note="See the rewards I earned")
    def get(self, request):
        presenter = get_reward_presenter()()
        return self._page(request, queries.my_rewards(str(request.user.pk)),
                          RewardResponseSerializer, presenter.present)


@extend_schema(tags=TAGS)
class ClaimView(_ReferralsView):
    """Attribute my account to a code — once; a replay of the same code is a 200."""

    permission_classes = [permissions.IsAuthenticated]
    stapel_anonymous_access = ANONYMOUS_ALLOWED
    request_serializer_class = ClaimRequestSerializer
    response_serializer_class = ClaimResponseSerializer

    @extend_schema(operation_id="referrals_claim_create", request=ClaimRequestSerializer,
                   responses={201: ClaimResponseSerializer, 200: ClaimResponseSerializer})
    @flow_step(CLAIM, order=2, note="Right after sign-up the client claims the stored code")
    def post(self, request):
        refusal = attribution_service.requires_account_refusal(request.user)
        if refusal is not None:
            raise refusal
        data = self.validated_request_data(request)
        attribution, created = attribution_service.claim(
            request.user, data["code"], captured_at=data.get("captured_at"),
            device_id=data.get("device_id") or "",
        )
        label = queries.user_labels([attribution.referrer_id]).get(attribution.referrer_id)
        if attribution.code_kind == CodeKind.CAMPAIGN:
            label = attribution.code.label or None
        dto = get_claim_presenter()().present_claim(attribution, label)
        return self.serialized_response(dto, status=201 if created else 200)


@extend_schema(tags=TAGS)
class CodeLookupView(_ReferralsView):
    """Is this code claimable now, and whose is it (masked)? Public."""

    permission_classes = [permissions.AllowAny]
    stapel_anonymous_access = ANONYMOUS_ALLOWED
    response_serializer_class = CodeLookupResponseSerializer

    @extend_schema(operation_id="referrals_codes_retrieve", responses={200: CodeLookupResponseSerializer})
    @flow_step(CLAIM, order=1, note="The landing page checks the code and names the inviter")
    def get(self, request, code):
        text = code_text.normalize(code)[:32]
        found = code_service.lookup(text)
        valid = code_service.claimable(found)
        label = None
        if found is not None:
            label = (found.label or None) if found.kind == CodeKind.CAMPAIGN else \
                queries.user_labels([found.owner_id]).get(found.owner_id)
        return self.serialized_response(get_claim_presenter()().present_lookup(text, found, valid, label))


# ── Staff ─────────────────────────────────────────────────────────────────


class _StaffView(_ReferralsView):
    permission_classes = [IsStaffUser]
    stapel_anonymous_access = ANONYMOUS_DENIED


def _rule_response(rule):
    counts = queries.rule_stats([rule.pk])[rule.pk]
    return get_staff_presenter()().present_rule(rule, counts)


@extend_schema(tags=STAFF_TAGS)
class RuleListView(_StaffView):
    """All reward rules (a programme has a handful) and rule creation."""

    request_serializer_class = RewardRuleRequestSerializer
    response_serializer_class = RewardRuleResponseSerializer

    @extend_schema(operation_id="referrals_staff_rules_list",
                   responses={200: RewardRuleResponseSerializer(many=True)})
    @flow_step(STAFF, order=1, note="Review the reward rules")
    def get(self, request):
        rules = list(RewardRule.objects.order_by("id")[:500])
        counts = queries.rule_stats([r.pk for r in rules])
        presenter = get_staff_presenter()()
        return self.serialized_response([presenter.present_rule(r, counts[r.pk]) for r in rules], many=True)

    @extend_schema(operation_id="referrals_staff_rules_create", request=RewardRuleRequestSerializer,
                   responses={201: RewardRuleResponseSerializer})
    @flow_step(STAFF, order=2, note="Create a rule: trigger, beneficiary, strategy, caps")
    def post(self, request):
        rule = rule_service.create(self.validated_request_data(request))
        return self.serialized_response(_rule_response(rule), status=201)


@extend_schema(tags=STAFF_TAGS)
class RuleDetailView(_StaffView):
    request_serializer_class = RewardRuleRequestSerializer
    response_serializer_class = RewardRuleResponseSerializer

    @extend_schema(operation_id="referrals_staff_rules_retrieve", responses={200: RewardRuleResponseSerializer})
    @flow_step(STAFF, order=8, note="Open one rule with its ledger counts")
    def get(self, request, rule_id):
        return self.serialized_response(_rule_response(rule_service.get(rule_id)))

    @extend_schema(operation_id="referrals_staff_rules_update", request=RewardRuleRequestSerializer,
                   responses={200: RewardRuleResponseSerializer})
    @flow_step(STAFF, order=3, note="Change or deactivate a rule")
    def patch(self, request, rule_id):
        rule = rule_service.update(rule_id, self.validated_request_data(request, partial=True))
        return self.serialized_response(_rule_response(rule))


@extend_schema(tags=STAFF_TAGS)
class CampaignCodeListView(_StaffView):
    request_serializer_class = CampaignCodeRequestSerializer
    response_serializer_class = CampaignCodeResponseSerializer

    @extend_schema(operation_id="referrals_staff_campaign_codes_list", parameters=_anchor_parameters(),
                   responses={200: CampaignCodePageSerializer})
    @flow_step(STAFF, order=9, note="Review campaign codes and their use")
    def get(self, request):
        presenter = get_staff_presenter()()
        qs = ReferralCode.objects.filter(kind=CodeKind.CAMPAIGN)
        return self._page(request, qs, CampaignCodeResponseSerializer,
                          lambda c: presenter.present_campaign(c, code_text.share_link(c.code)))

    @extend_schema(operation_id="referrals_staff_campaign_codes_create", request=CampaignCodeRequestSerializer,
                   responses={201: CampaignCodeResponseSerializer})
    @flow_step(STAFF, order=4, note="Issue a campaign code with a window and a cap")
    def post(self, request):
        code = code_service.create_campaign(request.user, self.validated_request_data(request))
        dto = get_staff_presenter()().present_campaign(code, code_text.share_link(code.code))
        return self.serialized_response(dto, status=201)


@extend_schema(tags=STAFF_TAGS)
class CampaignCodeDetailView(_StaffView):
    request_serializer_class = CampaignCodeRequestSerializer
    response_serializer_class = CampaignCodeResponseSerializer

    @extend_schema(operation_id="referrals_staff_campaign_codes_update", request=CampaignCodeRequestSerializer,
                   responses={200: CampaignCodeResponseSerializer})
    @flow_step(STAFF, order=10, note="Close, extend or re-cap a campaign code")
    def patch(self, request, code_id):
        data = dict(self.validated_request_data(request, partial=True))
        data.pop("code", None)
        code = code_service.update_campaign(code_id, data)
        dto = get_staff_presenter()().present_campaign(code, code_text.share_link(code.code))
        return self.serialized_response(dto)


@extend_schema(tags=STAFF_TAGS)
class StaffRewardListView(_StaffView):
    response_serializer_class = StaffRewardResponseSerializer

    @extend_schema(
        operation_id="referrals_staff_rewards_list",
        parameters=[
            OpenApiParameter("status", str, OpenApiParameter.QUERY, required=False, many=True,
                             enum=list(RewardStatus.values), description="Filter by status (repeatable)."),
            OpenApiParameter("rule", OpenApiTypes.INT, OpenApiParameter.QUERY, required=False,
                             description="Filter by rule id."),
            *_anchor_parameters(),
        ],
        responses={200: StaffRewardPageSerializer},
    )
    @flow_step(STAFF, order=5, note="Watch the ledger")
    def get(self, request):
        qs = queries.staff_rewards(request.query_params.getlist("status"), request.query_params.get("rule"))
        presenter = get_staff_presenter()()
        return self._page(request, qs, StaffRewardResponseSerializer, presenter.present_reward)


@extend_schema(tags=STAFF_TAGS)
class StaffRewardTransitionView(_StaffView):
    request_serializer_class = RewardTransitionRequestSerializer
    response_serializer_class = StaffRewardResponseSerializer

    @extend_schema(operation_id="referrals_staff_rewards_transition", request=RewardTransitionRequestSerializer,
                   responses={200: StaffRewardResponseSerializer})
    @flow_step(STAFF, order=6, note="Grant, reject or reverse a reward by hand")
    def post(self, request, reward_id):
        data = self.validated_request_data(request)
        reward = reward_service.staff_transition(reward_id, data["to"], data.get("reason", ""))
        reward = type(reward).objects.select_related("rule").get(pk=reward.pk)
        return self.serialized_response(get_staff_presenter()().present_reward(reward))


@extend_schema(tags=STAFF_TAGS)
class StatsView(_StaffView):
    response_serializer_class = ReferralStatsResponseSerializer

    @extend_schema(operation_id="referrals_staff_stats_retrieve", responses={200: ReferralStatsResponseSerializer})
    @flow_step(STAFF, order=7, note="Programme statistics")
    def get(self, request):
        return self.serialized_response(get_staff_presenter()().present_stats(queries.stats()))


@extend_schema(tags=STAFF_TAGS)
class StrategyListView(_StaffView):
    """Every registered reward strategy with the JSON Schema of its params."""

    response_serializer_class = RewardStrategyListSerializer

    @extend_schema(operation_id="referrals_staff_strategies_list", responses={200: RewardStrategyListSerializer})
    @flow_step(STAFF, order=11, note="List the reward strategies a rule may name, with their params schema")
    def get(self, request):
        from .strategies import registered_strategies

        return self.serialized_response(get_staff_presenter()().present_strategies(registered_strategies()))
