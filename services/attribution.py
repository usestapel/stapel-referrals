"""Attribution: the claim, the account merge, and the referee's account age.

One attribution per referee is the database's answer (``referee_id``
unique), so two claims racing for one referee end as one row and one
``already_attributed`` (or a replay). The campaign cap is counted under the
code-row lock, so two referees racing for the last use end as one
attribution and one ``code_exhausted``.
"""
from __future__ import annotations

import datetime as dt
import logging

from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone
from stapel_core.comm import mutate_and_emit

from .. import guards
from ..errors import (
    ERR_400_CODE_UNKNOWN,
    ERR_400_SELF_REFERRAL,
    ERR_403_CLAIM_REQUIRES_ACCOUNT,
    ERR_409_ALREADY_ATTRIBUTED,
    ERR_409_ATTRIBUTION_WINDOW_CLOSED,
    ERR_409_CLAIM_REFUSED,
    ERR_409_CLAIM_WINDOW_CLOSED,
    ERR_409_CODE_EXHAUSTED,
    ERR_409_CODE_EXPIRED,
)
from ..models import (
    Attribution,
    AttributionSource,
    AttributionStatus,
    CodeKind,
    DeviceSighting,
    ReferralCode,
    Reward,
    SignupMarker,
)
from . import ReferralsError, events
from . import codes as code_service

logger = logging.getLogger(__name__)

def _is_guest(user) -> bool:
    from types import SimpleNamespace

    from stapel_core.django.api.permissions import IsNotAnonymousUser

    return not IsNotAnonymousUser().has_permission(SimpleNamespace(user=user), None)


def requires_account_refusal(user) -> ReferralsError | None:
    from ..conf import referrals_settings

    if referrals_settings.CLAIM_REQUIRES_ACCOUNT and _is_guest(user):
        return ReferralsError(403, ERR_403_CLAIM_REQUIRES_ACCOUNT)
    return None


def account_born_at(user_id: str, user=None) -> dt.datetime | None:
    """When the account signed up: auth's clock from the marker, else the user row."""
    marker = SignupMarker.objects.filter(user_id=str(user_id)).first()
    if marker is not None:
        return marker.signed_up_at
    if user is None:
        from django.contrib.auth import get_user_model
        from django.core.exceptions import ValidationError

        try:
            user = get_user_model().objects.filter(pk=user_id).first()
        except (ValidationError, ValueError, TypeError):
            user = None
    for attr in ("created_at", "date_joined"):
        value = getattr(user, attr, None)
        if isinstance(value, dt.datetime):
            return value
    return None


def account_too_old(user_id: str, user=None, now=None) -> bool:
    """True when the account is past CLAIM_MAX_ACCOUNT_AGE_HOURS. Guests never are."""
    from ..conf import referrals_settings

    hours = int(referrals_settings.CLAIM_MAX_ACCOUNT_AGE_HOURS or 0)
    if hours <= 0:
        return False
    if user is not None and getattr(user, "is_anonymous", False) is True:
        return False
    born = account_born_at(user_id, user)
    if born is None:
        return False  # unmeasured resolves in the customer's favour
    return (now or timezone.now()) - born > dt.timedelta(hours=hours)


def _replay_or_conflict(existing: Attribution, code: ReferralCode) -> Attribution:
    if existing.code_id != code.pk:
        raise ReferralsError(409, ERR_409_ALREADY_ATTRIBUTED)
    if existing.status == AttributionStatus.REFUSED:
        raise ReferralsError(409, ERR_409_CLAIM_REFUSED)
    return existing


def claim(user, raw_code, *, captured_at=None, device_id: str = "", now=None) -> tuple[Attribution, bool]:
    """Attribute *user* to the code. Returns ``(attribution, created)``."""
    from ..conf import referrals_settings
    from . import rewards

    refusal = requires_account_refusal(user)
    if refusal is not None:
        raise refusal
    now = now or timezone.now()
    referee_id = str(user.pk)
    code = code_service.lookup(raw_code)
    if code is None or not code.active:
        raise ReferralsError(400, ERR_400_CODE_UNKNOWN)
    if code.owner_id == referee_id:
        raise ReferralsError(400, ERR_400_SELF_REFERRAL)
    existing = Attribution.objects.filter(referee_id=referee_id).first()
    if existing is not None:
        return _replay_or_conflict(existing, code), False
    if not code_service.in_window(code, now):
        raise ReferralsError(409, ERR_409_CODE_EXPIRED)
    if captured_at is not None:
        captured_at = min(captured_at, now)  # a client clock ahead of ours reads as now
        window = dt.timedelta(days=int(referrals_settings.ATTRIBUTION_WINDOW_DAYS))
        if now - captured_at > window:
            raise ReferralsError(409, ERR_409_ATTRIBUTION_WINDOW_CLOSED)
    if account_too_old(referee_id, user, now):
        raise ReferralsError(409, ERR_409_CLAIM_WINDOW_CLOSED)

    ctx = guards.ClaimContext(
        referee_id=referee_id, referrer_id=code.owner_id, code=code.code,
        code_kind=code.kind, device_hash=guards.device_hash(device_id),
    )
    objection = guards.first_objection(ctx)
    qualifying = dt.timedelta(days=int(referrals_settings.QUALIFYING_WINDOW_DAYS))

    with mutate_and_emit() as emit:
        locked = ReferralCode.objects.select_for_update().get(pk=code.pk)
        if objection is None and locked.max_attributions is not None \
                and locked.attribution_count >= locked.max_attributions:
            raise ReferralsError(409, ERR_409_CODE_EXHAUSTED)
        try:
            with transaction.atomic():
                attribution = Attribution.objects.create(
                    referee_id=referee_id,
                    code=locked,
                    referrer_id=locked.owner_id,
                    code_kind=locked.kind,
                    status=AttributionStatus.REFUSED if objection else AttributionStatus.ACTIVE,
                    refusal_reason=objection or "",
                    source=AttributionSource.CLAIM,
                    captured_at=captured_at,
                    attributed_at=now,
                    qualifying_until=now + qualifying,
                )
        except IntegrityError:
            existing = Attribution.objects.get(referee_id=referee_id)
            return _replay_or_conflict(existing, code), False
        if objection is None:
            ReferralCode.objects.filter(pk=locked.pk).update(attribution_count=F("attribution_count") + 1)
            emit(events.EVENT_ATTRIBUTION_CREATED, events.attribution_payload(attribution),
                 key=str(attribution.pk))
    if objection is not None:
        logger.info("referrals: claim by %s refused by guard %s", referee_id, objection)
        raise ReferralsError(409, ERR_409_CLAIM_REFUSED)
    if SignupMarker.objects.filter(user_id=referee_id).exists():
        rewards.on_signup(referee_id, now=now)
    return attribution, True


def record_device(user_id: str, device_id: str, now=None) -> None:
    """Remember a device on a (prospective) referrer — the same_device memory."""
    digest = guards.device_hash(device_id)
    if not digest:
        return
    now = now or timezone.now()
    updated = DeviceSighting.objects.filter(user_id=str(user_id), device_hash=digest).update(seen_at=now)
    if not updated:
        try:
            with transaction.atomic():
                DeviceSighting.objects.create(user_id=str(user_id), device_hash=digest, seen_at=now)
        except IntegrityError:
            pass


def for_referee(user_id: str) -> Attribution | None:
    return Attribution.objects.select_related("code").filter(referee_id=str(user_id)).first()


def merge_users(from_id: str, into_id: str, now=None) -> dict:
    """Fold a guest's referral rows into the surviving account. Idempotent.

    - personal codes follow their owner; the guest's primary code is retired
      when the survivor already has one (it stays claimable);
    - attributions the guest made as referrer follow it;
    - the guest's own attribution moves only when the survivor has none and
      is still a new account; otherwise it is dropped (one with rewards in
      the ledger is kept as it is);
    - rewards follow their beneficiary; the signup marker and device
      sightings follow the account.
    """
    from_id, into_id = str(from_id), str(into_id)
    now = now or timezone.now()
    moved = {"codes": 0, "as_referrer": 0, "as_referee": 0, "rewards": 0}
    if code_service._primary(into_id) is not None:
        ReferralCode.objects.filter(owner_id=from_id, kind=CodeKind.PERSONAL).update(is_primary=False)
    moved["codes"] = ReferralCode.objects.filter(owner_id=from_id).update(owner_id=into_id)
    moved["as_referrer"] = Attribution.objects.filter(referrer_id=from_id).update(referrer_id=into_id)
    Reward.objects.filter(referrer_id=from_id).update(referrer_id=into_id)
    moved["rewards"] = Reward.objects.filter(beneficiary_id=from_id).update(beneficiary_id=into_id)

    guest = Attribution.objects.filter(referee_id=from_id).first()
    if guest is not None:
        survivor_has = Attribution.objects.filter(referee_id=into_id).exists()
        if not survivor_has and not account_too_old(into_id, now=now) and guest.referrer_id != into_id:
            Attribution.objects.filter(pk=guest.pk).update(referee_id=into_id, source=AttributionSource.MERGE)
            moved["as_referee"] = 1
        elif not guest.rewards.exists():
            guest.delete()

    if SignupMarker.objects.filter(user_id=into_id).exists():
        SignupMarker.objects.filter(user_id=from_id).delete()
    else:
        SignupMarker.objects.filter(user_id=from_id).update(user_id=into_id)
    for sighting in DeviceSighting.objects.filter(user_id=from_id):
        if DeviceSighting.objects.filter(user_id=into_id, device_hash=sighting.device_hash).exists():
            sighting.delete()
        else:
            DeviceSighting.objects.filter(pk=sighting.pk).update(user_id=into_id)
    return moved
