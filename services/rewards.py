"""Rewards: rules fire on triggers, the ledger moves through its FSM.

Firing a rule (``_fire``) happens under ``select_for_update()`` on the rule
row: that lock serialises every writer of the rule, so the caps and the
budget are counted after it and two triggers racing for the last unit of a
budget end as one ``pending`` and one ``rejected (budget_exhausted)``. The
idempotency key is the unique ``(rule, attribution, trigger_id, role)``: a
replayed fact finds its rows and changes nothing.

Granting calls the strategy OUTSIDE any lock (no lock waits on the
network). A failure leaves the reward ``pending`` with ``last_error`` — an
infrastructure failure is never recorded as a domain refusal — and the beat
retries. Reversal works the same way from ``reverse_requested_at``.

FSM (``TRANSITIONS``): pending → granted | rejected; granted → reversed.
"""
from __future__ import annotations

import datetime as dt
import logging
from decimal import Decimal

from django.db.models import F, Q, Sum
from django.utils import timezone
from stapel_core.comm import mutate_and_emit

from ..errors import (
    ERR_404_REWARD_NOT_FOUND,
    ERR_409_REWARD_TRANSITION_INVALID,
    ERR_503_REWARD_PROVIDER_UNAVAILABLE,
)
from ..models import (
    LIVE_REWARD_STATUSES,
    Attribution,
    AttributionStatus,
    Beneficiary,
    QualifyingOrder,
    Reward,
    RewardRule,
    RewardStatus,
    Role,
    RuleCodeKind,
    Trigger,
    TriggerKind,
)
from ..strategies import StrategyError, get_strategy
from . import ReferralsError, events

logger = logging.getLogger(__name__)

S = RewardStatus
TRANSITIONS = frozenset({
    (S.PENDING, S.GRANTED),
    (S.PENDING, S.REJECTED),
    (S.GRANTED, S.REVERSED),
})

SIGNUP_TRIGGER_ID = "signup"
CAP_REACHED = "cap_reached"
BUDGET_EXHAUSTED = "budget_exhausted"
ORDER_CANCELLED = "order_cancelled"
BATCH = 200


def roles_for(rule: RewardRule) -> list[str]:
    if rule.beneficiary == Beneficiary.REFERRER:
        return [Role.REFERRER]
    if rule.beneficiary == Beneficiary.REFEREE:
        return [Role.REFEREE]
    return [Role.REFERRER, Role.REFEREE]


def _active_rules(trigger_kinds, attribution: Attribution, now):
    qs = RewardRule.objects.filter(active=True, trigger__in=trigger_kinds)
    qs = qs.filter(Q(starts_at__isnull=True) | Q(starts_at__lte=now))
    qs = qs.filter(Q(ends_at__isnull=True) | Q(ends_at__gt=now))
    qs = qs.filter(Q(code_kind=RuleCodeKind.ANY) | Q(code_kind=attribution.code_kind))
    qs = qs.filter(Q(campaign_code__isnull=True) | Q(campaign_code_id=attribution.code_id))
    return list(qs.order_by("id"))


def _hold_until(rule: RewardRule, kind: str, now) -> dt.datetime:
    from ..conf import referrals_settings

    if rule.hold_days is not None:
        days = rule.hold_days
    else:
        days = int(referrals_settings.HOLD_DAYS) if kind == TriggerKind.ORDER else 0
    return now + dt.timedelta(days=days)


def _cap_verdict(rule: RewardRule, referrer_id: str, new_value: Decimal, now) -> str:
    """Why this trigger is over a cap, or ''. Called under the rule-row lock."""
    live = Reward.objects.filter(rule=rule, status__in=LIVE_REWARD_STATUSES)
    lead = roles_for(rule)[0]
    if rule.cap_per_referrer is not None:
        mine = live.filter(referrer_id=referrer_id, role=lead)
        if rule.cap_period_days:
            mine = mine.filter(created_at__gte=now - dt.timedelta(days=rule.cap_period_days))
        if mine.count() >= rule.cap_per_referrer:
            return CAP_REACHED
    if rule.budget_count is not None and live.filter(role=lead).count() >= rule.budget_count:
        return BUDGET_EXHAUSTED
    if rule.budget_value is not None:
        spent = live.aggregate(total=Sum("value"))["total"] or Decimal("0")
        if spent + new_value > rule.budget_value:
            return BUDGET_EXHAUSTED
    return ""


def _params(rule: RewardRule, role: str) -> dict:
    return dict(rule.referrer_params if role == Role.REFERRER else rule.referee_params or {})


def _fire(rule: RewardRule, attribution: Attribution, kind: str, trigger_id: str, now) -> list[Reward]:
    """Record the rewards of one rule for one trigger. Idempotent."""
    try:
        strategy = get_strategy(rule.strategy)
    except LookupError:
        logger.error("referrals: rule %s names unknown strategy %r; not fired", rule.pk, rule.strategy)
        return []
    roles = roles_for(rule)
    plans = []
    for role in roles:
        params = _params(rule, role)
        try:
            params = strategy.clean_params(params)
            value = strategy.value(params)
        except ValueError as exc:
            logger.error("referrals: rule %s has invalid %s params (%s); not fired", rule.pk, role, exc)
            return []
        plans.append((role, params, value))

    with mutate_and_emit() as emit:
        locked = RewardRule.objects.select_for_update().get(pk=rule.pk)
        replay = list(Reward.objects.filter(rule=locked, attribution=attribution, trigger_id=trigger_id))
        if replay:
            return []
        if Reward.objects.filter(rule=locked, attribution=attribution,
                                 status__in=LIVE_REWARD_STATUSES).exists():
            return []
        verdict = _cap_verdict(locked, attribution.referrer_id, sum((p[2] for p in plans), Decimal("0")), now)
        hold_until = _hold_until(locked, kind, now)
        created = []
        for role, params, value in plans:
            reward = Reward.objects.create(
                rule=locked,
                attribution=attribution,
                trigger=kind,
                trigger_id=trigger_id,
                role=role,
                beneficiary_id=attribution.referrer_id if role == Role.REFERRER else attribution.referee_id,
                referrer_id=attribution.referrer_id,
                status=S.REJECTED if verdict else S.PENDING,
                strategy=locked.strategy,
                params=params,
                value=value,
                unit=getattr(strategy, "unit", "none") or "none",
                reason=verdict,
                hold_until=hold_until,
                rejected_at=now if verdict else None,
            )
            if verdict:
                emit(events.EVENT_REWARD_REJECTED, events.reward_payload(reward), key=str(reward.pk))
            created.append(reward)
    return created


def _grant_due(rewards: list[Reward], now) -> None:
    for reward in rewards:
        if reward.status == S.PENDING and (reward.hold_until is None or reward.hold_until <= now):
            grant(reward.pk, now=now)


def _active_attribution(referee_id: str) -> Attribution | None:
    return Attribution.objects.filter(referee_id=str(referee_id), status=AttributionStatus.ACTIVE).first()


def on_signup(user_id: str, now=None) -> list[Reward]:
    """The referee signed up (``user.signed_up`` or a claim after it)."""
    now = now or timezone.now()
    attribution = _active_attribution(user_id)
    if attribution is None:
        return []
    created = []
    for rule in _active_rules([Trigger.SIGNUP], attribution, now):
        created += _fire(rule, attribution, TriggerKind.SIGNUP, SIGNUP_TRIGGER_ID, now)
    _grant_due(created, now)
    return created


def _qualifies(rule: RewardRule, order: QualifyingOrder) -> bool:
    if rule.currency and order.currency != rule.currency:
        return False
    if rule.min_order_total is not None:
        if not rule.currency or order.total < rule.min_order_total:
            return False
    return True


def _ordinal(rule: RewardRule, order: QualifyingOrder) -> int:
    earlier = QualifyingOrder.objects.filter(attribution_id=order.attribution_id, cancelled_at__isnull=True)
    earlier = earlier.filter(Q(placed_at__lt=order.placed_at) | Q(placed_at=order.placed_at, id__lte=order.pk))
    return sum(1 for o in earlier if _qualifies(rule, o))


def on_order_placed(*, order_id: str, user_id: str, total: Decimal, currency: str,
                    placed_at: dt.datetime, now=None) -> list[Reward]:
    now = now or timezone.now()
    attribution = _active_attribution(user_id)
    if attribution is None:
        return []
    if not attribution.attributed_at <= placed_at < attribution.qualifying_until:
        return []
    order, _ = QualifyingOrder.objects.get_or_create(
        order_id=str(order_id),
        defaults={"attribution": attribution, "total": total, "currency": currency, "placed_at": placed_at},
    )
    if order.cancelled_at is not None or order.attribution_id != attribution.pk:
        return []
    created = []
    for rule in _active_rules([Trigger.FIRST_ORDER, Trigger.NTH_ORDER], attribution, now):
        wanted = 1 if rule.trigger == Trigger.FIRST_ORDER else max(1, rule.order_ordinal)
        if _qualifies(rule, order) and _ordinal(rule, order) == wanted:
            created += _fire(rule, attribution, TriggerKind.ORDER, str(order_id), now)
    _grant_due(created, now)
    return created


def on_order_cancelled(order_id: str, now=None) -> dict:
    """Pending rewards of the order are rejected; recent grants are reversed."""
    from ..conf import referrals_settings

    now = now or timezone.now()
    QualifyingOrder.objects.filter(order_id=str(order_id), cancelled_at__isnull=True).update(cancelled_at=now)
    window = dt.timedelta(days=int(referrals_settings.REVERSAL_WINDOW_DAYS))
    outcome = {"rejected": 0, "reversed": 0}
    for reward in Reward.objects.filter(trigger=TriggerKind.ORDER, trigger_id=str(order_id)):
        if reward.status == S.PENDING:
            if _reject(reward.pk, ORDER_CANCELLED, now):
                outcome["rejected"] += 1
        elif reward.status == S.GRANTED and reward.granted_at and now - reward.granted_at <= window:
            if request_reversal(reward.pk, ORDER_CANCELLED, now=now).status == S.REVERSED:
                outcome["reversed"] += 1
    return outcome


# ── Ledger moves ──────────────────────────────────────────────────────────


def grant(reward_id, *, now=None, strict: bool = False) -> Reward:
    """Pay a pending reward through its strategy.

    On a provider failure the reward stays pending (``last_error``); with
    *strict* the failure is raised as ``503 reward_provider_unavailable``.
    """
    now = now or timezone.now()
    reward = Reward.objects.select_related("rule").get(pk=reward_id)
    if reward.status != S.PENDING:
        return reward
    try:
        ref = get_strategy(reward.strategy).grant(reward)
    except (StrategyError, LookupError, KeyError, ValueError) as exc:
        Reward.objects.filter(pk=reward.pk, status=S.PENDING).update(
            attempts=F("attempts") + 1, last_error=str(exc)[:500] or exc.__class__.__name__,
        )
        logger.warning("referrals: reward %s not granted yet: %s", reward.pk, exc)
        if strict:
            raise ReferralsError(503, ERR_503_REWARD_PROVIDER_UNAVAILABLE) from exc
        reward.refresh_from_db()
        return reward
    with mutate_and_emit() as emit:
        locked = Reward.objects.select_for_update().get(pk=reward.pk)
        locked.attempts += 1
        locked.grant_ref = str(ref or "")[:255]
        if locked.status != S.PENDING:
            # Rejected while the provider was paying: take it back.
            locked.reverse_requested_at = now
            locked.save()
            return locked
        locked.status = S.GRANTED
        locked.granted_at = now
        locked.last_error = ""
        locked.save()
        emit(events.EVENT_REWARD_GRANTED, events.reward_payload(locked), key=str(locked.pk))
        events.reward_granted_after_commit(locked)
    return locked


def _reject(reward_id, reason: str, now) -> bool:
    with mutate_and_emit() as emit:
        locked = Reward.objects.select_for_update().get(pk=reward_id)
        if locked.status != S.PENDING:
            return False
        locked.status = S.REJECTED
        locked.reason = reason[:64]
        locked.rejected_at = now
        locked.save()
        emit(events.EVENT_REWARD_REJECTED, events.reward_payload(locked), key=str(locked.pk))
    return True


def request_reversal(reward_id, reason: str, *, now=None, strict: bool = False) -> Reward:
    """Ask the strategy to take a granted reward back; confirmed -> reversed."""
    now = now or timezone.now()
    Reward.objects.filter(pk=reward_id, reverse_requested_at__isnull=True).update(
        reverse_requested_at=now, reason=reason[:64],
    )
    return _try_reverse(reward_id, now=now, strict=strict)


def _try_reverse(reward_id, *, now, strict: bool = False) -> Reward:
    reward = Reward.objects.get(pk=reward_id)
    if reward.reversed_at is not None or reward.reverse_requested_at is None:
        return reward
    try:
        get_strategy(reward.strategy).reverse(reward)
    except (StrategyError, LookupError, KeyError, ValueError) as exc:
        Reward.objects.filter(pk=reward.pk).update(
            attempts=F("attempts") + 1, last_error=str(exc)[:500] or exc.__class__.__name__,
        )
        logger.warning("referrals: reward %s not reversed yet: %s", reward.pk, exc)
        if strict:
            raise ReferralsError(503, ERR_503_REWARD_PROVIDER_UNAVAILABLE) from exc
        reward.refresh_from_db()
        return reward
    with mutate_and_emit() as emit:
        locked = Reward.objects.select_for_update().get(pk=reward.pk)
        if locked.reversed_at is not None:
            return locked
        locked.reversed_at = now
        locked.last_error = ""
        was_granted = locked.status == S.GRANTED
        if was_granted:
            locked.status = S.REVERSED
        locked.save()
        if was_granted:
            emit(events.EVENT_REWARD_REVERSED, events.reward_payload(locked), key=str(locked.pk))
    return locked


def staff_transition(reward_id, to_status: str, reason: str = "", now=None) -> Reward:
    """Staff moves a reward along the FSM; an illegal edge is a 409."""
    now = now or timezone.now()
    reward = Reward.objects.filter(pk=reward_id).first()
    if reward is None:
        raise ReferralsError(404, ERR_404_REWARD_NOT_FOUND)
    if (reward.status, to_status) not in TRANSITIONS:
        raise ReferralsError(409, ERR_409_REWARD_TRANSITION_INVALID, {"from": reward.status, "to": to_status})
    if to_status == S.GRANTED:
        return grant(reward.pk, now=now, strict=True)
    if to_status == S.REJECTED:
        _reject(reward.pk, reason or "staff", now)
        return Reward.objects.get(pk=reward.pk)
    return request_reversal(reward.pk, reason or "staff", now=now, strict=True)


# ── Beat passes ───────────────────────────────────────────────────────────


def grant_due(now=None) -> int:
    """Grant pending rewards whose hold has passed (and retry failed grants)."""
    now = now or timezone.now()
    ids = list(Reward.objects.filter(status=S.PENDING)
               .filter(Q(hold_until__isnull=True) | Q(hold_until__lte=now))
               .order_by("id").values_list("id", flat=True)[:BATCH])
    granted = 0
    for reward_id in ids:
        if grant(reward_id, now=now).status == S.GRANTED:
            granted += 1
    return granted


def retry_reversals(now=None) -> int:
    now = now or timezone.now()
    ids = list(Reward.objects.filter(reverse_requested_at__isnull=False, reversed_at__isnull=True)
               .order_by("id").values_list("id", flat=True)[:BATCH])
    done = 0
    for reward_id in ids:
        if _try_reverse(reward_id, now=now).reversed_at is not None:
            done += 1
    return done
