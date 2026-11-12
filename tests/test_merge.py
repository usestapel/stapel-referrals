"""user.merged folds a guest's referral rows into the survivor, idempotently."""
import datetime as dt

import pytest
from django.utils import timezone

from stapel_referrals.models import Attribution, DeviceSighting, ReferralCode, Reward, SignupMarker

from .helpers import claim, fact, my_code, order_placed, rule

pytestmark = pytest.mark.django_db


def merged(frm, into):
    fact("user.merged", {"from_user_id": str(frm.pk), "into_user_id": str(into.pk)})


def test_guest_attribution_moves_to_a_new_survivor(referrer, guest, referee):
    claim(guest, my_code(referrer))
    merged(guest, referee)
    row = Attribution.objects.get()
    assert (row.referee_id, row.source) == (str(referee.pk), "merge")
    merged(guest, referee)  # redelivery
    assert Attribution.objects.count() == 1


def test_old_survivor_drops_the_guest_attribution(referrer, guest, referee):
    claim(guest, my_code(referrer))
    SignupMarker.objects.create(user_id=str(referee.pk), signed_up_at=timezone.now() - dt.timedelta(days=60))
    merged(guest, referee)
    assert Attribution.objects.count() == 0
    assert ReferralCode.objects.get(code=my_code(referrer)).attribution_count == 1  # history stays counted


def test_survivor_attribution_wins(referrer, staff, guest, referee):
    claim(referee, my_code(staff))
    claim(guest, my_code(referrer))
    merged(guest, referee)
    assert list(Attribution.objects.values_list("referee_id", "referrer_id")) == [
        (str(referee.pk), str(staff.pk))]


def test_guest_attribution_with_rewards_is_kept(referrer, staff, guest, referee):
    rule(trigger="first_order", hold_days=0)
    claim(referee, my_code(staff))
    claim(guest, my_code(referrer))
    order_placed(guest, order_id="o-1")
    merged(guest, referee)
    assert Attribution.objects.count() == 2
    assert Reward.objects.get(role="referee").beneficiary_id == str(referee.pk)


def test_codes_follow_and_the_guest_primary_retires(guest, referee, referrer):
    guest_code = my_code(guest)
    survivor_code = my_code(referee)
    claim(referrer, guest_code)  # someone used the guest's code
    merged(guest, referee)
    assert my_code(referee) == survivor_code
    retired = ReferralCode.objects.get(code=guest_code)
    assert (retired.owner_id, retired.is_primary) == (str(referee.pk), False)
    assert Attribution.objects.get().referrer_id == str(referee.pk)


def test_guest_code_becomes_primary_when_survivor_has_none(guest, referee):
    code = my_code(guest)
    merged(guest, referee)
    assert my_code(referee) == code


def test_markers_and_devices_follow(guest, referee):
    SignupMarker.objects.create(user_id=str(guest.pk), signed_up_at=timezone.now())
    DeviceSighting.objects.create(user_id=str(guest.pk), device_hash="h1", seen_at=timezone.now())
    DeviceSighting.objects.create(user_id=str(referee.pk), device_hash="h1", seen_at=timezone.now())
    DeviceSighting.objects.create(user_id=str(guest.pk), device_hash="h2", seen_at=timezone.now())
    merged(guest, referee)
    assert SignupMarker.objects.get().user_id == str(referee.pk)
    assert sorted(DeviceSighting.objects.values_list("user_id", "device_hash")) == [
        (str(referee.pk), "h1"), (str(referee.pk), "h2")]


def test_merge_into_the_referrer_never_self_refers(referrer, guest):
    claim(guest, my_code(referrer))
    merged(guest, referrer)
    assert Attribution.objects.count() == 0
