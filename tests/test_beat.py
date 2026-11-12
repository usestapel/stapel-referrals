"""The beat schedule and its passes."""
import datetime as dt

import pytest
from django.core.management import call_command
from django.utils import timezone

from stapel_referrals import beat
from stapel_referrals.models import DeviceSighting, SignupMarker


def test_schedule_names_real_tasks():
    schedule = beat.get_referrals_beat_schedule()
    assert {e["task"] for e in schedule.values()} == {
        beat.GRANT_TASK_NAME, beat.REVERSE_TASK_NAME, beat.PURGE_TASK_NAME}
    for entry in schedule.values():
        module, _, name = entry["task"].rpartition(".")
        assert module == "stapel_referrals.beat" and hasattr(beat, name)


@pytest.mark.django_db
def test_purge(db):
    old = timezone.now() - dt.timedelta(days=31)
    SignupMarker.objects.create(user_id="old", signed_up_at=old)
    SignupMarker.objects.create(user_id="new", signed_up_at=timezone.now())
    DeviceSighting.objects.create(user_id="u", device_hash="h", seen_at=old)
    assert beat.purge_markers() == 2
    assert list(SignupMarker.objects.values_list("user_id", flat=True)) == ["new"]


@pytest.mark.django_db
def test_housekeeping_command(capsys):
    call_command("referrals_housekeeping")
    assert "granted=0 reversed=0 purged=0" in capsys.readouterr().out
