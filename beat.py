"""The housekeeping schedule — shipped by the library.

Wire it in the settings module itself::

    from stapel_referrals.beat import get_referrals_beat_schedule

    CELERY_BEAT_SCHEDULE = {**get_referrals_beat_schedule(), ...}

Three passes: grant due rewards and retry failed grants (every 5 minutes),
retry requested reversals (every 15 minutes), purge signup markers and
device sightings (daily). Imports settings and nothing else, so it is safe
from a settings module. Celery is optional: ``manage.py
referrals_housekeeping`` runs the same passes from any scheduler.
"""
from __future__ import annotations

GRANT_TASK_NAME = "stapel_referrals.beat.grant_due_rewards"
REVERSE_TASK_NAME = "stapel_referrals.beat.retry_reversals"
PURGE_TASK_NAME = "stapel_referrals.beat.purge_markers"

GRANT_BEAT_KEY = "referrals-grant-due"
REVERSE_BEAT_KEY = "referrals-retry-reversals"
PURGE_BEAT_KEY = "referrals-purge-markers"

GRANT_INTERVAL_SECONDS = 5 * 60
REVERSE_INTERVAL_SECONDS = 15 * 60
PURGE_INTERVAL_SECONDS = 24 * 60 * 60


def grant_due_rewards() -> int:
    """Grant pending rewards past their hold; retry failed grants."""
    from .services.rewards import grant_due

    return grant_due()


def retry_reversals() -> int:
    from .services.rewards import retry_reversals as _retry

    return _retry()


def purge_markers() -> int:
    """Signup markers and device sightings past their TTL are deleted."""
    import datetime as dt

    from django.utils import timezone

    from .conf import referrals_settings
    from .models import DeviceSighting, SignupMarker

    now = timezone.now()
    markers, _ = SignupMarker.objects.filter(
        signed_up_at__lt=now - dt.timedelta(days=int(referrals_settings.SIGNUP_MARKER_TTL_DAYS))
    ).delete()
    devices, _ = DeviceSighting.objects.filter(
        seen_at__lt=now - dt.timedelta(days=int(referrals_settings.DEVICE_SIGHTING_TTL_DAYS))
    ).delete()
    return markers + devices


def get_referrals_beat_schedule() -> dict:
    """Beat entries for the three passes. Add to ``CELERY_BEAT_SCHEDULE``."""
    return {
        GRANT_BEAT_KEY: {"task": GRANT_TASK_NAME, "schedule": float(GRANT_INTERVAL_SECONDS)},
        REVERSE_BEAT_KEY: {"task": REVERSE_TASK_NAME, "schedule": float(REVERSE_INTERVAL_SECONDS)},
        PURGE_BEAT_KEY: {"task": PURGE_TASK_NAME, "schedule": float(PURGE_INTERVAL_SECONDS)},
    }


try:  # pragma: no cover - exercised by whichever profile the host installs
    from celery import shared_task
except ImportError:
    pass
else:
    grant_due_rewards = shared_task(name=GRANT_TASK_NAME)(grant_due_rewards)
    retry_reversals = shared_task(name=REVERSE_TASK_NAME)(retry_reversals)
    purge_markers = shared_task(name=PURGE_TASK_NAME)(purge_markers)


__all__ = [
    "GRANT_BEAT_KEY",
    "GRANT_TASK_NAME",
    "PURGE_BEAT_KEY",
    "PURGE_TASK_NAME",
    "REVERSE_BEAT_KEY",
    "REVERSE_TASK_NAME",
    "get_referrals_beat_schedule",
    "grant_due_rewards",
    "purge_markers",
    "retry_reversals",
]
