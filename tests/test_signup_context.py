"""Attribution carried by the sign-up: ``user.signed_up``'s ``signup_context``.

The client puts ``{"referral_code", "referral_captured_at"}`` into auth's
opaque ``signup_context`` (stapel-auth >= 0.51.0); auth copies it verbatim into
the fact. ``POST claim/`` stays for older auth and for guests; both paths
together still make exactly one attribution.
"""
import datetime as dt

import pytest
from django.utils import timezone

from stapel_referrals import guards
from stapel_referrals.models import Attribution, ReferralCode, Reward, SignupMarker
from stapel_referrals.services import attribution as attribution_service
from stapel_referrals.services import codes as code_service

from .helpers import V1, claim, fact, my_code, rule

pytestmark = pytest.mark.django_db


def signed_up_with(user, context, at=None, auth_type="email"):
    payload = {
        "user_id": str(user.pk), "auth_type": auth_type,
        "registered_at": (at or timezone.now()).isoformat(),
        "was_guest": False, "is_staff": False, "ad_consent": "unknown", "consent_source": "no_resolver",
    }
    if context is not None:
        payload["signup_context"] = context
    fact("user.signed_up", payload)


def ctx(code, captured_at=None):
    out = {"referral_code": code}
    if captured_at is not None:
        out["referral_captured_at"] = captured_at.isoformat()
    return out


class TestSignupContext:
    def test_the_fact_attributes_and_pays_the_signup_rule(self, referrer, referee, captured_events):
        rule()
        code = my_code(referrer)
        signed_up_with(referee, ctx(code.lower(), timezone.now() - dt.timedelta(days=2)))
        row = Attribution.objects.get()
        assert (row.referee_id, row.referrer_id, row.source, row.status) == (
            str(referee.pk), str(referrer.pk), "signup", "active")
        assert row.captured_at is not None
        assert ReferralCode.objects.get(code=code).attribution_count == 1
        assert sorted(Reward.objects.values_list("role", "status")) == [
            ("referee", "granted"), ("referrer", "granted")]
        names = [e.event_type for e in captured_events]
        assert names.count("referrals.attribution.created") == 1
        assert SignupMarker.objects.get(user_id=str(referee.pk)).auth_type == "email"

    def test_no_context_attributes_nothing(self, referrer, referee):
        signed_up_with(referee, None)
        signed_up_with(referee, {"utm_source": "x"})
        assert Attribution.objects.count() == 0

    @pytest.mark.parametrize("context", [
        {"referral_code": ""}, {"referral_code": 42}, {"referral_code": "NOSUCHCODE"},
    ])
    def test_unusable_codes_are_ignored(self, referrer, referee, context):
        signed_up_with(referee, context)
        assert Attribution.objects.count() == 0

    def test_self_referral_is_ignored(self, referrer):
        signed_up_with(referrer, ctx(my_code(referrer)))
        assert Attribution.objects.count() == 0

    def test_window_counts_from_captured_at(self, referrer, referee):
        old = timezone.now() - dt.timedelta(days=31)
        signed_up_with(referee, ctx(my_code(referrer), old))
        assert Attribution.objects.count() == 0

    def test_a_client_clock_ahead_reads_as_the_signup_instant(self, referrer, referee):
        signed_up_with(referee, ctx(my_code(referrer), timezone.now() + dt.timedelta(days=400)))
        row = Attribution.objects.get()
        assert row.captured_at <= row.attributed_at

    def test_unparseable_captured_at_is_no_captured_at(self, referrer, referee):
        signed_up_with(referee, {"referral_code": my_code(referrer), "referral_captured_at": "yesterday"})
        assert Attribution.objects.get().captured_at is None

    def test_judged_at_the_signup_instant_not_the_delivery(self, referrer, referee):
        """A fact delivered two days late: the account is not 'too old', the window starts at sign-up."""
        registered = timezone.now() - dt.timedelta(days=2)
        signed_up_with(referee, ctx(my_code(referrer), registered - dt.timedelta(minutes=5)), at=registered)
        row = Attribution.objects.get()
        assert abs(row.attributed_at - registered) < dt.timedelta(seconds=1)

    def test_auth_clock_far_ahead_reads_as_now(self, referrer, referee):
        ahead = timezone.now() + dt.timedelta(hours=1)
        signed_up_with(referee, ctx(my_code(referrer)), at=ahead)
        assert Attribution.objects.get().attributed_at < ahead - dt.timedelta(minutes=30)

    def test_campaign_window_is_checked(self, referee, staff):
        code_service.create_campaign(staff, {"code": "LATER", "starts_at": timezone.now() + dt.timedelta(days=1)})
        signed_up_with(referee, ctx("LATER"))
        assert Attribution.objects.count() == 0

    def test_guards_apply(self, referrer, referee, settings):
        settings.STAPEL_REFERRALS = {"ATTRIBUTION_GUARDS": ["stapel_referrals.tests.test_signup_context.deny"]}
        signed_up_with(referee, ctx(my_code(referrer)))
        assert Attribution.objects.get().status == "refused"

    def test_replayed_fact_is_one_attribution(self, referrer, referee):
        rule()
        context = ctx(my_code(referrer))
        signed_up_with(referee, context)
        signed_up_with(referee, context)
        assert Attribution.objects.count() == 1 and Reward.objects.count() == 2

    def test_claim_after_the_fact_with_the_same_code_replays(self, referrer, referee, client_for):
        code = my_code(referrer)
        signed_up_with(referee, ctx(code))
        response = client_for(referee).post(f"{V1}/claim/", {"code": code}, format="json")
        assert response.status_code == 200 and Attribution.objects.count() == 1

    def test_claim_before_the_fact_wins_and_the_fact_is_a_no_op(self, referrer, referee, staff):
        code_service.create_campaign(staff, {"code": "OTHER"})
        claim(referee, my_code(referrer))
        signed_up_with(referee, ctx("OTHER"))
        assert Attribution.objects.get().referrer_id == str(referrer.pk)
        assert ReferralCode.objects.get(code="OTHER").attribution_count == 0


def deny(ctx):
    return "denied"


class TestRace:
    """The fact consumer and a claim interleave inside one attribution window."""

    def _interleave(self, monkeypatch, during):
        real = guards.first_objection
        state = {"raced": False}

        def rival_first(context):
            if not state["raced"]:
                state["raced"] = True
                during()
            return real(context)

        monkeypatch.setattr(guards, "first_objection", rival_first)

    def test_claim_lands_inside_the_fact_consumer(self, referrer, referee, monkeypatch):
        rule()
        code = my_code(referrer)
        self._interleave(monkeypatch, lambda: claim(referee, code))
        signed_up_with(referee, ctx(code))
        assert Attribution.objects.count() == 1 and Attribution.objects.get().source == "claim"
        assert ReferralCode.objects.get(code=code).attribution_count == 1
        assert Reward.objects.count() == 2

    def test_fact_lands_inside_the_claim(self, referrer, referee, monkeypatch):
        rule()
        code = my_code(referrer)
        self._interleave(monkeypatch, lambda: signed_up_with(referee, ctx(code)))
        row, created = attribution_service.claim(referee, code)
        assert created is False and Attribution.objects.count() == 1
        assert ReferralCode.objects.get(code=code).attribution_count == 1
        assert Reward.objects.count() == 2
