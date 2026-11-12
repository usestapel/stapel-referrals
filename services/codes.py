"""Referral codes: lazy personal codes, vanity changes, campaign codes, lookup.

Uniqueness is the database's answer, never a read-then-write: a minted code
that collides retries on the unique constraint, and two first reads of
"my code" racing each other end on the partial unique constraint (one
primary personal code per owner) — the loser re-reads the winner's row.
"""
from __future__ import annotations

import logging

from django.db import IntegrityError, transaction
from django.utils import timezone

from .. import codes
from ..errors import (
    ERR_400_VANITY_INVALID,
    ERR_403_VANITY_NOT_ALLOWED,
    ERR_404_CODE_NOT_FOUND,
    ERR_409_CODE_TAKEN,
    ERR_409_VANITY_LIMIT,
)
from ..models import CodeKind, ReferralCode
from . import ReferralsError

logger = logging.getLogger(__name__)

MINT_ATTEMPTS = 8


def _primary(owner_id: str) -> ReferralCode | None:
    return ReferralCode.objects.filter(owner_id=owner_id, kind=CodeKind.PERSONAL, is_primary=True).first()


def primary_code(owner_id: str) -> ReferralCode:
    """The owner's primary personal code, minted on first use."""
    from ..conf import referrals_settings

    owner_id = str(owner_id)
    existing = _primary(owner_id)
    if existing is not None:
        return existing
    for _ in range(MINT_ATTEMPTS):
        try:
            with transaction.atomic():
                return ReferralCode.objects.create(
                    code=codes.mint(referrals_settings.CODE_LENGTH),
                    kind=CodeKind.PERSONAL,
                    owner_id=owner_id,
                    is_primary=True,
                )
        except IntegrityError:
            existing = _primary(owner_id)
            if existing is not None:
                return existing  # a concurrent first read minted it
    raise RuntimeError("could not mint a unique referral code")  # pragma: no cover


def vanity_allowed(user) -> bool:
    from ..conf import referrals_settings

    policy = referrals_settings.VANITY_CODES
    if policy == "users":
        return not getattr(user, "is_anonymous", False)
    if policy == "staff":
        return bool(getattr(user, "is_staff", False))
    return False


def set_vanity(user, raw_code) -> ReferralCode:
    """Replace the user's primary code with a chosen one.

    The old code is retired, not deleted: it stays claimable, because a link
    already shared must keep naming its owner.
    """
    from ..conf import referrals_settings

    if not vanity_allowed(user):
        raise ReferralsError(403, ERR_403_VANITY_NOT_ALLOWED)
    code = codes.normalize(raw_code)
    problem = codes.vanity_problem(code, referrals_settings.RESERVED_CODES)
    if problem:
        raise ReferralsError(400, ERR_400_VANITY_INVALID, {"problem": problem})
    owner_id = str(user.pk)
    current = primary_code(owner_id)
    if current.code == code:
        return current
    limit = int(referrals_settings.VANITY_MAX_CHANGES)
    used = ReferralCode.objects.filter(owner_id=owner_id, kind=CodeKind.PERSONAL, vanity=True).count()
    if used >= limit:
        raise ReferralsError(409, ERR_409_VANITY_LIMIT, {"max": limit})
    try:
        with transaction.atomic():
            ReferralCode.objects.select_for_update().filter(pk=current.pk).update(
                is_primary=False, updated_at=timezone.now()
            )
            return ReferralCode.objects.create(
                code=code, kind=CodeKind.PERSONAL, owner_id=owner_id, is_primary=True, vanity=True,
            )
    except IntegrityError:
        raise ReferralsError(409, ERR_409_CODE_TAKEN) from None


def lookup(raw_code) -> ReferralCode | None:
    """The code row for client input, or None (bad shape reads as unknown)."""
    code = codes.normalize(raw_code)
    if not codes.is_lookup_shaped(code):
        return None
    return ReferralCode.objects.filter(code=code).first()


def in_window(code: ReferralCode, now=None) -> bool:
    now = now or timezone.now()
    if code.starts_at and now < code.starts_at:
        return False
    if code.ends_at and now >= code.ends_at:
        return False
    return True


def claimable(code: ReferralCode | None, now=None) -> bool:
    """Would a claim of this code pass the code-side checks right now?"""
    if code is None or not code.active or not in_window(code, now):
        return False
    return code.max_attributions is None or code.attribution_count < code.max_attributions


# ── Campaign codes (staff) ────────────────────────────────────────────────

CAMPAIGN_FIELDS = ("label", "active", "starts_at", "ends_at", "max_attributions", "owner_id")


def create_campaign(staff_user, data: dict) -> ReferralCode:
    from ..conf import referrals_settings

    raw = data.get("code") or ""
    fields = {k: data[k] for k in CAMPAIGN_FIELDS if k in data and data[k] is not None}
    fields.setdefault("owner_id", str(staff_user.pk))
    if raw:
        code = codes.normalize(raw)
        if codes.vanity_problem(code, ()) is not None:
            raise ReferralsError(400, ERR_400_VANITY_INVALID, {"problem": "shape"})
        try:
            with transaction.atomic():
                return ReferralCode.objects.create(
                    code=code, kind=CodeKind.CAMPAIGN, is_primary=False, vanity=True,
                    created_by=str(staff_user.pk), **fields,
                )
        except IntegrityError:
            raise ReferralsError(409, ERR_409_CODE_TAKEN) from None
    for _ in range(MINT_ATTEMPTS):
        try:
            with transaction.atomic():
                return ReferralCode.objects.create(
                    code=codes.mint(referrals_settings.CODE_LENGTH), kind=CodeKind.CAMPAIGN,
                    is_primary=False, created_by=str(staff_user.pk), **fields,
                )
        except IntegrityError:
            continue
    raise RuntimeError("could not mint a unique campaign code")  # pragma: no cover


def get_campaign(code_id) -> ReferralCode:
    found = ReferralCode.objects.filter(pk=code_id, kind=CodeKind.CAMPAIGN).first()
    if found is None:
        raise ReferralsError(404, ERR_404_CODE_NOT_FOUND)
    return found


def update_campaign(code_id, data: dict) -> ReferralCode:
    with transaction.atomic():
        code = get_campaign(code_id)
        code = ReferralCode.objects.select_for_update().get(pk=code.pk)
        for key in CAMPAIGN_FIELDS:
            if key in data:
                setattr(code, key, data[key] if key != "owner_id" else str(data[key]))
        code.save()
    return code
