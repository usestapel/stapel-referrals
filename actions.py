"""Action subscriptions of stapel-referrals.

Handlers are idempotent: delivery is at-least-once. Every fact here belongs
to another brick; the module knows only its name and the fields it reads
(``schemas/consumes/`` holds this consumer's view, not a copy of the
owner's schema). Without auth or orders installed, nothing arrives and
nothing breaks.

- ``user.signed_up`` (stapel-auth) — the one self-serve sign-up per
  account: the signup marker is written and signup rules fire.
- ``user.merged`` (stapel-auth) — a guest folded into an account.
- ``orders.order.placed`` / ``orders.order.status_changed`` (stapel-orders)
  — order rules fire; a cancel rejects or reverses.
- ``user.deleted`` / ``gdpr.erasure.requested`` are answered by
  ``register_gdpr_owner`` (``apps.py``), not here.
"""
import datetime as dt
import json
import logging
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from stapel_core.comm import on_action

logger = logging.getLogger(__name__)

_CONSUMES = Path(__file__).resolve().parent / "schemas" / "consumes"


def _schema(name: str) -> dict:
    return json.loads((_CONSUMES / f"{name}.json").read_text(encoding="utf-8"))


def _instant(raw) -> dt.datetime | None:
    value = parse_datetime(str(raw or ""))
    if value is None:
        return None
    if timezone.is_naive(value):
        value = value.replace(tzinfo=dt.timezone.utc)
    return value


@on_action("user.signed_up", schema=_schema("user.signed_up"))
def handle_user_signed_up(event):
    """Write the signup marker, then fire signup rules for an attributed account."""
    from .models import SignupMarker
    from .services import rewards

    payload = event.payload or {}
    user_id = str(payload.get("user_id") or "")
    if not user_id:
        return
    signed_up_at = _instant(payload.get("registered_at")) or timezone.now()
    try:
        with transaction.atomic():
            SignupMarker.objects.get_or_create(user_id=user_id, defaults={"signed_up_at": signed_up_at})
    except IntegrityError:
        pass
    rewards.on_signup(user_id)


@on_action("user.merged", schema=_schema("user.merged"))
def handle_user_merged(event):
    """Re-parent the guest's codes, attributions, rewards and markers."""
    from .services.attribution import merge_users

    payload = event.payload or {}
    from_id = str(payload.get("from_user_id") or "")
    into_id = str(payload.get("into_user_id") or "")
    if not from_id or not into_id or from_id == into_id:
        return
    with transaction.atomic():
        moved = merge_users(from_id, into_id)
    if any(moved.values()):
        logger.info("user.merged %s -> %s: %s", from_id, into_id, moved)


@on_action("orders.order.placed", schema=_schema("orders.order.placed"))
def handle_order_placed(event):
    from .services import rewards

    payload = event.payload or {}
    placed_at = _instant(payload.get("placed_at"))
    try:
        total = Decimal(str(payload.get("total")))
    except (InvalidOperation, ValueError):
        logger.error("orders.order.placed %s: total %r is not a decimal", event.event_id, payload.get("total"))
        return
    if placed_at is None or not payload.get("order_id") or not payload.get("user_id"):
        logger.error("orders.order.placed %s: missing order_id/user_id/placed_at", event.event_id)
        return
    rewards.on_order_placed(
        order_id=str(payload["order_id"]), user_id=str(payload["user_id"]), total=total,
        currency=str(payload.get("currency") or "").upper(), placed_at=placed_at,
    )


@on_action("orders.order.status_changed", schema=_schema("orders.order.status_changed"))
def handle_order_status_changed(event):
    from .services import rewards

    payload = event.payload or {}
    if payload.get("to") != "cancelled" or not payload.get("order_id"):
        return
    outcome = rewards.on_order_cancelled(str(payload["order_id"]))
    if any(outcome.values()):
        logger.info("order %s cancelled: %s", payload["order_id"], outcome)
