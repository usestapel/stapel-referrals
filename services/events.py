"""Facts and courtesies stapel-referrals sends.

Emits go through the caller's ``mutate_and_emit`` emitter so the outbox row
commits with the mutation. Payloads carry ids and numbers only — never a
code text a person chose, never a contact. Notifications are best-effort
and leave only after commit.
"""
from __future__ import annotations

import logging

from django.db import transaction

logger = logging.getLogger(__name__)

EVENT_ATTRIBUTION_CREATED = "referrals.attribution.created"
EVENT_REWARD_GRANTED = "referrals.reward.granted"
EVENT_REWARD_REJECTED = "referrals.reward.rejected"
EVENT_REWARD_REVERSED = "referrals.reward.reversed"
EMITS = (EVENT_ATTRIBUTION_CREATED, EVENT_REWARD_GRANTED, EVENT_REWARD_REJECTED, EVENT_REWARD_REVERSED)

NOTIFY_REWARD_GRANTED = "referrals.reward_granted"


def attribution_payload(attribution) -> dict:
    return {
        "attribution_id": str(attribution.pk),
        "referee_id": attribution.referee_id,
        "referrer_id": attribution.referrer_id,
        "code_kind": attribution.code_kind,
        "attributed_at": attribution.attributed_at.isoformat(),
    }


def reward_payload(reward) -> dict:
    payload = {
        "reward_id": str(reward.pk),
        "rule_id": str(reward.rule_id),
        "attribution_id": str(reward.attribution_id),
        "role": reward.role,
        "beneficiary_id": reward.beneficiary_id,
        "referrer_id": reward.referrer_id,
        "trigger": reward.trigger,
        "strategy": reward.strategy,
        "value": f"{reward.value:.2f}",
        "unit": reward.unit,
        "status": reward.status,
    }
    if reward.reason:
        payload["reason"] = reward.reason
    return payload


def notify(notification_type: str, user_id: str, data: dict) -> bool:
    """Send one notification now (best-effort). Returns whether it was queued."""
    from ..conf import referrals_settings

    if not referrals_settings.NOTIFY or not user_id:
        return False
    try:
        from stapel_core.notifications.publish import request_notification

        return bool(request_notification(
            notification_type, user_id=user_id, variables=data, source_service="referrals",
        ))
    except Exception:
        logger.warning("referrals: %s notification for reward %s not sent",
                       notification_type, data.get("reward_id"), exc_info=True)
        return False


def reward_granted_after_commit(reward) -> None:
    from ..conf import referrals_settings

    if not referrals_settings.NOTIFY:
        return
    data = {
        "reward_id": str(reward.pk),
        "role": reward.role,
        "value": f"{reward.value:.2f}",
        "unit": reward.unit,
        "grant_ref": reward.grant_ref if reward.unit == "code" else "",
    }
    user_id = reward.beneficiary_id
    transaction.on_commit(lambda: notify(NOTIFY_REWARD_GRANTED, user_id, data))
