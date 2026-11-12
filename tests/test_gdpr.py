"""GDPR: export everything about a person; erase the person, keep the ledger."""
import pytest
from stapel_core.gdpr import gdpr_registry, registered_gdpr_owners

from stapel_referrals.gdpr import erase_subject
from stapel_referrals.models import Attribution, DeviceSighting, ReferralCode, Reward, SignupMarker

from .helpers import V1, claim, rule, signed_up

pytestmark = pytest.mark.django_db


@pytest.fixture
def history(referrer, referee, client_for, settings):
    settings.STAPEL_REFERRALS = {"VANITY_CODES": "users"}
    client_for(referrer).patch(f"{V1}/me/code/", {"code": "alice-smith"}, format="json")
    client_for(referrer).get(f"{V1}/me/", {"device_id": "d1"})
    rule()
    claim(referee, "ALICE-SMITH")
    signed_up(referee)


def test_registered():
    assert "referrals" in registered_gdpr_owners()
    assert any(p.section == "referrals" for p in gdpr_registry.providers)


def test_export(history, referrer, referee):
    provider = next(p for p in gdpr_registry.providers if p.section == "referrals")
    mine = provider.export(referrer.pk)
    assert {c["code"] for c in mine["codes"]} >= {"ALICE-SMITH"}
    assert len(mine["referrals"]) == 1 and len(mine["rewards"]) == 1
    theirs = provider.export(referee.pk)
    assert theirs["referred_by"]["code"] == "ALICE-SMITH" and len(theirs["rewards"]) == 1


def test_erase_referrer_keeps_the_ledger(history, referrer):
    before = sorted(Reward.objects.values_list("status", "value", "unit"))
    counts = erase_subject("account", str(referrer.pk))
    assert counts["codes"] == 2 and counts["device_sightings"] == 1
    uid = str(referrer.pk)
    assert not ReferralCode.objects.filter(owner_id=uid).exists()
    assert not ReferralCode.objects.filter(code="ALICE-SMITH").exists()  # a vanity code can be a name
    assert not ReferralCode.objects.filter(kind="personal", active=True).exists()
    assert not Attribution.objects.filter(referrer_id=uid).exists()
    assert not Reward.objects.filter(beneficiary_id=uid).exists()
    assert not Reward.objects.filter(referrer_id=uid).exists()
    assert not DeviceSighting.objects.filter(user_id=uid).exists()
    assert sorted(Reward.objects.values_list("status", "value", "unit")) == before
    assert erase_subject("account", uid)["codes"] == 0  # idempotent


def test_erase_referee(history, referee):
    erase_subject("account", str(referee.pk))
    uid = str(referee.pk)
    assert not Attribution.objects.filter(referee_id=uid).exists()
    assert Attribution.objects.count() == 1 and Reward.objects.count() == 2
    assert not SignupMarker.objects.filter(user_id=uid).exists()


def test_erased_ids_fit_the_columns(history, referrer):
    erase_subject("account", str(referrer.pk))
    assert all(len(r.owner_id) <= 64 for r in ReferralCode.objects.all())


def test_other_subject_types_are_ignored(history, referrer):
    assert erase_subject("workspace", str(referrer.pk)) is None
