"""comm Functions provided by stapel-referrals (schemas in ``schemas/functions/``).

- ``referrals.attribution.get`` — was this account referred, and by whom.
- ``referrals.code.resolve`` — a code's owner and whether it is claimable.
- ``referrals.export`` — the reward ledger, cursor snapshot
  (``source_of_truth`` shape).

The Functions the reward strategies CALL are owned by billing and
promotions (their schemas live there); the payloads this module sends are
in ``strategies.py``.
"""
from stapel_core.comm import function

EXPORT_DEFAULT_LIMIT = 100
EXPORT_MAX_LIMIT = 500


@function("referrals.attribution.get")
def attribution_get(payload):
    from .models import AttributionStatus
    from .services.attribution import for_referee

    attribution = for_referee(str(payload["user_id"]))
    if attribution is None or attribution.status != AttributionStatus.ACTIVE:
        return {"referred": False, "referrer_id": None, "code_kind": None, "attributed_at": None}
    return {
        "referred": True,
        "referrer_id": attribution.referrer_id,
        "code_kind": attribution.code_kind,
        "attributed_at": attribution.attributed_at.isoformat(),
    }


@function("referrals.code.resolve")
def code_resolve(payload):
    from .services.codes import claimable, lookup

    code = lookup(payload.get("code"))
    if code is None:
        return {"found": False, "claimable": False, "kind": None, "owner_id": None}
    return {"found": True, "claimable": claimable(code), "kind": code.kind, "owner_id": code.owner_id}


@function("referrals.export")
def referrals_export(payload):
    from .models import Reward

    limit = max(1, min(int(payload.get("limit") or EXPORT_DEFAULT_LIMIT), EXPORT_MAX_LIMIT))
    cursor = payload.get("cursor")
    qs = Reward.objects.order_by("id")
    if cursor and str(cursor).isdigit():
        qs = qs.filter(id__gt=int(cursor))
    page = list(qs[: limit + 1])
    more = len(page) > limit
    page = page[:limit]
    rows = [
        {
            "reward_id": str(r.pk),
            "rule_id": str(r.rule_id),
            "attribution_id": str(r.attribution_id),
            "role": r.role,
            "beneficiary_id": r.beneficiary_id,
            "referrer_id": r.referrer_id,
            "trigger": r.trigger,
            "status": r.status,
            "strategy": r.strategy,
            "value": f"{r.value:.2f}",
            "unit": r.unit,
            "created_at": r.created_at.isoformat(),
            "seq": r.pk,
        }
        for r in page
    ]
    return {
        "rows": rows,
        "cursor": str(page[-1].pk) if more and page else None,
        "total": Reward.objects.count(),
    }
