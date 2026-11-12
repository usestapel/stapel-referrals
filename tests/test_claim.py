"""The claim: first touch wins, one attribution per referee, windows, guards."""
import datetime as dt

import pytest
from django.utils import timezone

from stapel_referrals.models import Attribution, ReferralCode, SignupMarker
from stapel_referrals.services import codes as code_service

from .helpers import V1, claim, my_code

pytestmark = pytest.mark.django_db


def post(client, code, **extra):
    return client.post(f"{V1}/claim/", {"code": code, **extra}, format="json")


def err(response):
    return response.json()["localizable_error"]


class TestClaim:
    def test_created_then_replayed(self, referrer, referee, client_for, captured_events):
        code = my_code(referrer)
        first = post(client_for(referee), code.lower())
        assert first.status_code == 201, first.content
        assert first.json()["code"] == code and first.json()["code_kind"] == "personal"
        assert first.json()["referrer_label"] == "a***@e***.com"
        again = post(client_for(referee), code)
        assert again.status_code == 200
        assert Attribution.objects.count() == 1
        assert ReferralCode.objects.get(code=code).attribution_count == 1
        names = [e.event_type for e in captured_events]
        assert names == ["referrals.attribution.created"]
        payload = captured_events[0].payload
        assert payload["referee_id"] == str(referee.pk) and payload["referrer_id"] == str(referrer.pk)

    def test_first_touch_wins(self, referrer, referee, staff, client_for):
        post(client_for(referee), my_code(referrer))
        r = post(client_for(referee), my_code(staff))
        assert r.status_code == 409 and err(r) == "error.409.already_attributed"
        assert Attribution.objects.get().referrer_id == str(referrer.pk)

    def test_unknown_code(self, referee, client_for):
        r = post(client_for(referee), "NOPE2345")
        assert r.status_code == 400 and err(r) == "error.400.code_unknown"

    def test_inactive_code_is_unknown(self, referrer, referee, client_for):
        code = my_code(referrer)
        ReferralCode.objects.filter(code=code).update(active=False)
        assert err(post(client_for(referee), code)) == "error.400.code_unknown"

    def test_self_referral_refused_and_slot_kept_free(self, referrer, referee, client_for):
        r = post(client_for(referrer), my_code(referrer))
        assert r.status_code == 400 and err(r) == "error.400.self_referral"
        assert post(client_for(referrer), my_code(referee)).status_code == 201

    def test_anonymous_request_is_refused(self, referrer, client_for):
        assert post(client_for(), my_code(referrer)).status_code in (401, 403)

    def test_guest_claims_by_default(self, referrer, guest, client_for):
        assert post(client_for(guest), my_code(referrer)).status_code == 201

    def test_guest_refused_when_account_required(self, referrer, guest, client_for, settings):
        settings.STAPEL_REFERRALS = {"CLAIM_REQUIRES_ACCOUNT": True}
        r = post(client_for(guest), my_code(referrer))
        assert r.status_code == 403 and err(r) == "error.403.claim_requires_account"


class TestWindows:
    def test_captured_too_long_ago(self, referrer, referee, client_for):
        old = (timezone.now() - dt.timedelta(days=31)).isoformat()
        r = post(client_for(referee), my_code(referrer), captured_at=old)
        assert r.status_code == 409 and err(r) == "error.409.attribution_window_closed"

    def test_captured_inside_window(self, referrer, referee, client_for):
        recent = (timezone.now() - dt.timedelta(days=29)).isoformat()
        assert post(client_for(referee), my_code(referrer), captured_at=recent).status_code == 201

    def test_future_capture_reads_as_now(self, referrer, referee, client_for):
        future = (timezone.now() + dt.timedelta(days=400)).isoformat()
        assert post(client_for(referee), my_code(referrer), captured_at=future).status_code == 201
        assert Attribution.objects.get().captured_at <= timezone.now()

    def test_old_account_cannot_claim(self, referrer, referee, client_for):
        SignupMarker.objects.create(user_id=str(referee.pk),
                                    signed_up_at=timezone.now() - dt.timedelta(hours=73))
        r = post(client_for(referee), my_code(referrer))
        assert r.status_code == 409 and err(r) == "error.409.claim_window_closed"

    def test_account_age_falls_back_to_the_user_row(self, referrer, referee, client_for):
        type(referee).objects.filter(pk=referee.pk).update(created_at=timezone.now() - dt.timedelta(days=10))
        referee.refresh_from_db()
        assert err(post(client_for(referee), my_code(referrer))) == "error.409.claim_window_closed"

    def test_account_age_limit_off(self, referrer, referee, client_for, settings):
        settings.STAPEL_REFERRALS = {"CLAIM_MAX_ACCOUNT_AGE_HOURS": 0}
        SignupMarker.objects.create(user_id=str(referee.pk), signed_up_at=timezone.now() - dt.timedelta(days=99))
        assert post(client_for(referee), my_code(referrer)).status_code == 201

    def test_guest_is_never_too_old(self, referrer, guest, client_for):
        type(guest).objects.filter(pk=guest.pk).update(created_at=timezone.now() - dt.timedelta(days=20))
        guest.refresh_from_db()
        assert post(client_for(guest), my_code(referrer)).status_code == 201


class TestCampaignCodes:
    def test_window(self, staff, referee, client_for):
        now = timezone.now()
        code_service.create_campaign(staff, {"code": "LATER", "starts_at": now + dt.timedelta(days=1)})
        code_service.create_campaign(staff, {"code": "PAST", "ends_at": now})
        assert err(post(client_for(referee), "later")) == "error.409.code_expired"
        assert err(post(client_for(referee), "past")) == "error.409.code_expired"

    def test_cap(self, staff, referrer, referee, client_for):
        code_service.create_campaign(staff, {"code": "ONCE", "max_attributions": 1, "label": "Fair"})
        first = post(client_for(referee), "once")
        assert first.status_code == 201 and first.json()["referrer_label"] == "Fair"
        r = post(client_for(referrer), "once")
        assert r.status_code == 409 and err(r) == "error.409.code_exhausted"


class TestGuards:
    def test_same_device(self, referrer, referee, client_for, settings):
        settings.STAPEL_REFERRALS = {"ATTRIBUTION_GUARDS": ["same_device"]}
        client_for(referrer).get(f"{V1}/me/", {"device_id": "phone-1"})
        r = post(client_for(referee), my_code(referrer), device_id="phone-1")
        assert r.status_code == 409 and err(r) == "error.409.claim_refused"
        assert "same_device" not in r.content.decode()
        row = Attribution.objects.get()
        assert (row.status, row.refusal_reason) == ("refused", "same_device")
        # The slot stays taken: neither a replay nor another code gets through.
        assert err(post(client_for(referee), my_code(referrer))) == "error.409.claim_refused"
        assert ReferralCode.objects.get(code=my_code(referrer)).attribution_count == 0

    def test_same_device_other_device_passes(self, referrer, referee, client_for, settings):
        settings.STAPEL_REFERRALS = {"ATTRIBUTION_GUARDS": ["same_device"]}
        client_for(referrer).get(f"{V1}/me/", {"device_id": "phone-1"})
        assert post(client_for(referee), my_code(referrer), device_id="laptop-2").status_code == 201

    def test_same_device_without_device_id_is_unmeasured(self, referrer, referee, client_for, settings):
        settings.STAPEL_REFERRALS = {"ATTRIBUTION_GUARDS": ["same_device"]}
        client_for(referrer).get(f"{V1}/me/", {"device_id": "phone-1"})
        assert post(client_for(referee), my_code(referrer)).status_code == 201

    def test_device_ids_are_stored_hashed(self, referrer, client_for):
        from stapel_referrals.models import DeviceSighting

        client_for(referrer).get(f"{V1}/me/", {"device_id": "phone-1"})
        assert DeviceSighting.objects.get().device_hash != "phone-1"

    def test_duplicate_contact(self, referee, client_for, settings):
        from conftest import make_user

        settings.STAPEL_REFERRALS = {"ATTRIBUTION_GUARDS": ["duplicate_contact"]}
        twin = make_user("bob2", email="b.o.b+second@example.com")
        type(referee).objects.filter(pk=referee.pk).update(email="bob@example.com")
        referee.refresh_from_db()
        assert err(post(client_for(referee), my_code(twin))) == "error.409.claim_refused"

    def test_duplicate_contact_distinct_people_pass(self, referrer, referee, client_for, settings):
        settings.STAPEL_REFERRALS = {"ATTRIBUTION_GUARDS": ["duplicate_contact"]}
        assert post(client_for(referee), my_code(referrer)).status_code == 201

    def test_custom_guard_by_dotted_path(self, referrer, referee, client_for, settings):
        settings.STAPEL_REFERRALS = {"ATTRIBUTION_GUARDS": ["stapel_referrals.tests.test_claim.always_no"]}
        assert err(post(client_for(referee), my_code(referrer))) == "error.409.claim_refused"
        assert Attribution.objects.get().refusal_reason == "custom"


def always_no(ctx):
    assert ctx.code and ctx.referrer_id and ctx.referee_id
    return "custom"


def test_service_claim_returns_the_row(referrer, referee):
    row = claim(referee, my_code(referrer))
    assert row.referee_id == str(referee.pk) and row.qualifying_until > row.attributed_at
