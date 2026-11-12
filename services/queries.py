"""Read paths: my referrals, my rewards, staff lists and stats, labels."""
from __future__ import annotations

from collections import defaultdict

from django.db.models import Count, Q, Sum

from ..models import (
    Attribution,
    AttributionStatus,
    CodeKind,
    ReferralCode,
    Reward,
    RewardRule,
    RewardStatus,
)

TOP_REFERRERS = 10


def user_labels(user_ids) -> dict[str, str]:
    """Masked labels (``a***@e***.com``) for the ids this service has rows for."""
    from django.contrib.auth import get_user_model
    from django.core.exceptions import ValidationError

    from ..codes import mask

    ids = [str(i) for i in dict.fromkeys(user_ids) if i]
    if not ids:
        return {}
    try:
        users = list(get_user_model().objects.filter(pk__in=ids))
    except (ValidationError, ValueError, TypeError):
        users = []
    labels = {}
    for user in users:
        if getattr(user, "is_anonymous", False) is True:
            continue
        text = getattr(user, "email", "") or getattr(user, "username", "") or ""
        if text:
            labels[str(user.pk)] = mask(text)
    return labels


def referee_labels(user_ids) -> dict[str, str]:
    from ..conf import referrals_settings

    if referrals_settings.REFEREE_DISPLAY != "masked":
        return {}
    return user_labels(user_ids)


def my_counts(user_id: str) -> dict:
    user_id = str(user_id)
    referrals = Attribution.objects.filter(referrer_id=user_id, status=AttributionStatus.ACTIVE)
    mine = Reward.objects.filter(beneficiary_id=user_id)
    return {
        "referrals": referrals.count(),
        "rewarded_referrals": Reward.objects.filter(
            referrer_id=user_id, status=RewardStatus.GRANTED
        ).values("attribution_id").distinct().count(),
        "pending_rewards": mine.filter(status=RewardStatus.PENDING).count(),
        "granted_rewards": mine.filter(status=RewardStatus.GRANTED).count(),
    }


def granted_totals(qs) -> list[dict]:
    rows = qs.filter(status=RewardStatus.GRANTED).values("unit").annotate(total=Sum("value")).order_by("unit")
    return [{"unit": r["unit"], "value": r["total"]} for r in rows]


def my_referrals(user_id: str):
    return Attribution.objects.filter(referrer_id=str(user_id), status=AttributionStatus.ACTIVE).annotate(
        rewards_granted=Count("rewards", filter=Q(rewards__status=RewardStatus.GRANTED,
                                                  rewards__role="referrer")),
        rewards_pending=Count("rewards", filter=Q(rewards__status=RewardStatus.PENDING,
                                                  rewards__role="referrer")),
    )


def my_rewards(user_id: str):
    return Reward.objects.filter(beneficiary_id=str(user_id)).select_related("rule")


def staff_rewards(statuses=(), rule_id=None):
    qs = Reward.objects.select_related("rule")
    statuses = [s for s in statuses if s in RewardStatus.values]
    if statuses:
        qs = qs.filter(status__in=statuses)
    if rule_id not in (None, "") and str(rule_id).isdigit():
        qs = qs.filter(rule_id=int(rule_id))
    return qs


def rule_stats(rule_ids) -> dict[int, dict]:
    out: dict[int, dict] = defaultdict(lambda: {s: 0 for s in RewardStatus.values})
    rows = Reward.objects.filter(rule_id__in=list(rule_ids)).values("rule_id", "status").annotate(n=Count("id"))
    for row in rows:
        out[row["rule_id"]][row["status"]] = row["n"]
    return out


def stats() -> dict:
    counts = {s: 0 for s in RewardStatus.values}
    for row in Reward.objects.values("status").annotate(n=Count("id")):
        counts[row["status"]] = row["n"]
    top = (Attribution.objects.filter(status=AttributionStatus.ACTIVE)
           .values("referrer_id").annotate(n=Count("id")).order_by("-n", "referrer_id")[:TOP_REFERRERS])
    return {
        "personal_codes": ReferralCode.objects.filter(kind=CodeKind.PERSONAL).count(),
        "campaign_codes": ReferralCode.objects.filter(kind=CodeKind.CAMPAIGN).count(),
        "attributions": Attribution.objects.filter(status=AttributionStatus.ACTIVE).count(),
        "attributions_refused": Attribution.objects.filter(status=AttributionStatus.REFUSED).count(),
        "active_rules": RewardRule.objects.filter(active=True).count(),
        "rewards": counts,
        "granted_value": granted_totals(Reward.objects.all()),
        "top_referrers": [{"referrer_id": r["referrer_id"], "referrals": r["n"]} for r in top],
    }
