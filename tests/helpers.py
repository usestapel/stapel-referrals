"""Builders shared by the test modules."""
from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal

from django.utils import timezone

V1 = "/referrals/api/v1"


def rule(**fields):
    from stapel_referrals.services import rules

    fields.setdefault("name", "Invite bonus")
    fields.setdefault("trigger", "signup")
    fields.setdefault("beneficiary", "both")
    fields.setdefault("strategy", "noop")
    fields.setdefault("referrer_params", {"value": "10"})
    fields.setdefault("referee_params", {"value": "5"})
    return rules.create(fields)


def claim(user, code, **kwargs):
    from stapel_referrals.services.attribution import claim as _claim

    return _claim(user, code, **kwargs)[0]


def my_code(user):
    from stapel_referrals.services.codes import primary_code

    return primary_code(str(user.pk)).code


def fact(name, payload):
    """Emit a neighbour's fact the way its owner does (inside mutate_and_emit)."""
    from stapel_core.comm import emit, mutate_and_emit

    with mutate_and_emit():
        emit(name, payload)


def signed_up(user, at=None):
    """Deliver user.signed_up for *user* the way auth emits it."""
    fact("user.signed_up", {
        "user_id": str(user.pk),
        "auth_type": "email",
        "registered_at": (at or timezone.now()).isoformat(),
        "was_guest": False,
        "is_staff": False,
        "ad_consent": "unknown",
        "consent_source": "no_resolver",
    })


def order_placed(user, total="50.00", currency="USD", order_id=None, at=None):
    order_id = order_id or str(uuid.uuid4().int)[:12]
    fact("orders.order.placed", {
        "order_id": order_id,
        "user_id": str(user.pk),
        "currency": currency,
        "total": str(Decimal(total).quantize(Decimal("0.01"))),
        "placed_at": (at or timezone.now()).isoformat(),
        "lines": [{"purchasable_type": "listing", "purchasable_key": "1", "quantity": 1,
                   "unit_price": str(Decimal(total).quantize(Decimal("0.01"))), "seller_id": None}],
    })
    return order_id


def order_cancelled(user, order_id, frm="new"):
    fact("orders.order.status_changed", {
        "order_id": order_id, "user_id": str(user.pk), "from": frm, "to": "cancelled", "actor": "owner",
    })


def days(n):
    return dt.timedelta(days=n)
