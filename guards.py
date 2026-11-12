"""Attribution guards — opt-in hooks that may object to a claim.

A guard is ``(ClaimContext) -> reason | None``. ``ATTRIBUTION_GUARDS`` lists
short names of the built-ins or dotted paths; the first reason wins. A
reason records the attribution as ``refused`` (the referee's one slot stays
taken) and the claim answers ``error.409.claim_refused`` without the reason.

A guard that cannot measure (no device id sent, a referrer this service has
never seen) returns None: an unmeasured claim resolves in the customer's
favour.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import re
from dataclasses import dataclass

from django.utils.module_loading import import_string

logger = logging.getLogger(__name__)

BUILTIN_GUARDS = {
    "same_device": "stapel_referrals.guards.same_device",
    "duplicate_contact": "stapel_referrals.guards.duplicate_contact",
}


@dataclass(frozen=True)
class ClaimContext:
    referee_id: str
    referrer_id: str
    code: str
    code_kind: str
    device_hash: str = ""


def device_hash(raw: str) -> str:
    """HMAC of a client device id; the raw id is never stored."""
    from django.conf import settings

    raw = str(raw or "").strip()
    if not raw:
        return ""
    key = str(settings.SECRET_KEY).encode()
    return hmac.new(key, raw.encode(), hashlib.sha256).hexdigest()


def same_device(ctx: ClaimContext) -> str | None:
    """The claiming device was seen on the referrer's account."""
    from .models import DeviceSighting

    if not ctx.device_hash:
        return None
    if DeviceSighting.objects.filter(user_id=ctx.referrer_id, device_hash=ctx.device_hash).exists():
        return "same_device"
    return None


def _email(value: str) -> str:
    value = (value or "").strip().lower()
    local, at, domain = value.partition("@")
    if not at:
        return ""
    return f"{local.split('+', 1)[0].replace('.', '')}@{domain}"


def _phone(value: str) -> str:
    digits = re.sub(r"\D", "", value or "")
    return digits if len(digits) >= 6 else ""


def _contacts(user_id: str) -> set[str] | None:
    from django.contrib.auth import get_user_model
    from django.core.exceptions import ValidationError

    try:
        user = get_user_model().objects.filter(pk=user_id).first()
    except (ValidationError, ValueError, TypeError):
        return None
    if user is None:
        return None
    found = {_email(getattr(user, "email", "") or ""), _phone(getattr(user, "phone", "") or "")}
    return {c for c in found if c}


def duplicate_contact(ctx: ClaimContext) -> str | None:
    """The referee's e-mail (dots and +tags ignored) or phone equals the referrer's."""
    referee, referrer = _contacts(ctx.referee_id), _contacts(ctx.referrer_id)
    if not referee or not referrer:
        return None
    return "duplicate_contact" if referee & referrer else None


def configured_guards() -> list:
    """Callables from ``ATTRIBUTION_GUARDS``; ``ImportError`` names a bad entry."""
    from .conf import referrals_settings

    guards = []
    for entry in referrals_settings.ATTRIBUTION_GUARDS or ():
        path = BUILTIN_GUARDS.get(str(entry), str(entry))
        guards.append(import_string(path))
    return guards


def first_objection(ctx: ClaimContext) -> str | None:
    for guard in configured_guards():
        reason = guard(ctx)
        if reason:
            return str(reason)[:64]
    return None


__all__ = ["BUILTIN_GUARDS", "ClaimContext", "configured_guards", "device_hash",
           "duplicate_contact", "first_objection", "same_device"]
