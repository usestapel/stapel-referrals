"""Reward strategies — how a reward reaches its beneficiary.

A strategy never imports the brick that pays: it calls a comm Function by
the name in settings. Built-ins (``REWARD_STRATEGIES`` merges over them,
``None`` removes one; a rule may also name a dotted path):

- ``noop`` (default) — records the reward, pays nothing;
- ``billing_credit`` — wallet credits through ``BILLING_CREDIT_FUNCTION``
  (``billing.credit``, stapel-billing >= 0.25.0), reversed through
  ``BILLING_DEBIT_FUNCTION`` (``billing.debit``, never ``allow_partial``);
- ``promo_code`` — a single-use code of an existing promotion, assigned to
  the beneficiary, through ``PROMO_ISSUE_FUNCTION`` (``promotions.codes.issue``)
  and revoked through ``PROMO_REVOKE_FUNCTION`` (``promotions.codes.revoke``),
  stapel-promotions 0.1.0 with the issue key.

Outbound keys are namespaced ``referrals:reward:<id>`` (``:reverse`` for a
reversal): billing's key space is per wallet and shared with debits and
holds, so a bare id would collide. Answers: :class:`StrategyRefused` is a
domain "no" (the reward is rejected with its reason), :class:`StrategyError`
is "not now" (pending, retried), :class:`StrategyConflict` is a key
conflict (pending, ERROR). ``reverse()`` returns a :class:`Reversal` when
less than everything came back.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal, InvalidOperation

from dataclasses import dataclass

from django.utils.module_loading import import_string

BUILTIN_STRATEGIES = {
    "noop": "stapel_referrals.strategies.NoopStrategy",
    "billing_credit": "stapel_referrals.strategies.BillingCreditStrategy",
    "promo_code": "stapel_referrals.strategies.PromoCodeStrategy",
}


class StrategyError(Exception):
    """The strategy could not complete now (unreachable, refused, malformed).

    Never a domain decision: the reward stays where it was and is retried.
    """


class StrategyConflict(StrategyError):
    """The provider says our idempotency key names a different operation.

    Retrying cannot fix it and granting it would be a guess: the reward stays
    pending with ``last_error`` and the service logs an ERROR for a person.
    """


class StrategyRefused(Exception):
    """The provider answered a domain "no" (the beneficiary is unknown or
    inactive there): the reward is rejected with ``reason``, not retried."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class Reversal:
    """What ``reverse()`` took back. ``None`` from ``reverse()`` means "all of it"."""

    outcome: str
    value: Decimal


class RewardStrategy:
    """Base class. Subclass, set ``name``/``unit``, implement the four calls."""

    name = ""
    unit = "none"
    #: comm Functions this strategy calls, as settings keys (for checks/docs).
    function_settings: tuple[str, ...] = ()
    #: JSON Schema (2020-12) of the params; ``GET staff/strategies/`` serves it
    #: and every rule write validates against it before ``clean_params``.
    params_schema: dict = {"type": "object"}
    #: One line for the staff rule form.
    description = ""

    def clean_params(self, params: dict) -> dict:
        """Validated params; raise ``ValueError(problem)`` on a bad dict."""
        if not isinstance(params, dict):
            raise ValueError("params must be an object")
        return dict(params)

    def value(self, params: dict) -> Decimal:
        """The number counted against a rule's ``budget_value``."""
        return _decimal(params.get("value", 0))

    def grant(self, reward) -> str:
        """Pay *reward*; return a reference (transaction id, code, '')."""
        return ""

    def reverse(self, reward) -> Reversal | None:
        """Take *reward* back. Raise ``StrategyError`` to retry later.

        Return a :class:`Reversal` when less than the whole value came back
        (``reversed_partial`` / ``already_spent``); ``None`` is a full reversal.
        """


def _decimal(raw) -> Decimal:
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, ValueError):
        raise ValueError("value must be a decimal number") from None
    if not value.is_finite() or value < 0 or value >= Decimal(10) ** 16:
        raise ValueError("value must be between 0 and 10^16")
    return value.quantize(Decimal("0.01"))


def _positive_int(params: dict, key: str, *, required: bool) -> int | None:
    raw = params.get(key)
    if raw is None:
        if required:
            raise ValueError(f"{key} is required")
        return None
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 1 or raw > 10**9:
        raise ValueError(f"{key} must be an integer from 1 to 10^9")
    return raw


def call_function(setting_key: str, payload: dict) -> dict:
    """Call the Function named by *setting_key*; any failure is StrategyError."""
    from stapel_core.comm import call, function_unreachable_reason

    from .conf import referrals_settings

    name = str(getattr(referrals_settings, setting_key) or "")
    if not name:
        raise StrategyError(f"{setting_key} is empty")
    reason = function_unreachable_reason(name)
    if reason:
        raise StrategyError(f"{name} unreachable: {reason}")
    try:
        result = call(name, payload, timeout=float(referrals_settings.FUNCTION_TIMEOUT_SECONDS))
    except Exception as exc:  # noqa: BLE001 — every provider failure is a retry
        raise StrategyError(f"{name} failed: {exc}") from exc
    if not isinstance(result, dict):
        raise StrategyError(f"{name} answered {type(result).__name__}, not an object")
    return result


def idempotency_key(reward, suffix: str = "") -> str:
    return f"referrals:reward:{reward.pk}{suffix}"


def _expires_at(params: dict) -> str | None:
    days = params.get("expires_in_days")
    if not days:
        return None
    from django.utils import timezone

    return (timezone.now() + dt.timedelta(days=int(days))).isoformat()


_VALUE_SCHEMA = {
    "type": ["string", "number"],
    "pattern": r"^[0-9]{1,16}(\.[0-9]{1,2})?$",
    "minimum": 0,
    "description": "Value counted against the rule's budget_value (decimal, 2 places).",
}


class NoopStrategy(RewardStrategy):
    """Records the reward; the host pays by listening to ``referrals.reward.granted``."""

    name = "noop"
    unit = "none"
    description = "Records the reward and pays nothing; the host listens to referrals.reward.granted."
    params_schema = {
        "type": "object",
        "properties": {"value": _VALUE_SCHEMA},
    }

    def clean_params(self, params):
        params = super().clean_params(params)
        if "value" in params:
            params["value"] = str(_decimal(params["value"]))
        return params


def _beneficiary_identity(user_id: str) -> dict | None:
    """The ``identity`` a paying brick may create a user it has not met from.

    Only an account whose ``user.signed_up`` this module saw has one: a
    signed-up account is not anonymous and its ``auth_type`` came with the
    fact. Anyone else goes without, and the provider decides on the bare id
    (which never creates a row).
    """
    from .models import SignupMarker

    marker = SignupMarker.objects.filter(user_id=str(user_id)).first()
    if marker is None:
        return None
    identity = {"is_anonymous": False}
    if marker.auth_type:
        identity["auth_type"] = marker.auth_type
    return identity


class BillingCreditStrategy(RewardStrategy):
    """Wallet credits: ``{credits, expires_in_days?, description?}``.

    Grant: ``billing.credit`` (stapel-billing >= 0.25.0) with the key
    ``referrals:reward:<id>`` and the beneficiary's ``identity`` when known.
    Answers: ``ok`` -> granted (``grant_ref`` = transaction id);
    ``user_unknown`` / ``user_inactive`` -> rejected with that reason;
    ``idempotency_conflict`` -> :class:`StrategyConflict` (pending, ERROR);
    anything else or no answer -> retried.

    Reversal: ``billing.debit`` with ``referrals:reward:<id>:reverse``, never
    ``allow_partial`` (that would record a debt the referral never was). The
    planned amount is stored on the reward before the call, so a retry after
    a lost answer replays the same request under the same key. When the
    wallet holds less than the credit, the plan drops to the balance and the
    reversal ends ``reversed_partial`` (or ``already_spent`` at zero): spent
    referral credits are not clawed back as debt.
    """

    name = "billing_credit"
    unit = "credits"
    function_settings = ("BILLING_CREDIT_FUNCTION", "BILLING_DEBIT_FUNCTION")
    description = "Wallet credits through billing.credit; a reversal debits what is left, never a debt."
    params_schema = {
        "type": "object",
        "required": ["credits"],
        "properties": {
            "credits": {"type": "integer", "minimum": 1, "maximum": 10**9,
                        "description": "Credits put on the beneficiary's wallet."},
            "expires_in_days": {"type": "integer", "minimum": 1, "maximum": 10**9,
                                "description": "The credit lot dies this many days after the grant."},
            "description": {"type": "string", "maxLength": 200,
                            "description": "Ledger line shown in the wallet."},
        },
        "additionalProperties": False,
    }

    #: billing.credit / billing.debit answers this strategy maps.
    REFUSALS = frozenset({"user_unknown", "user_inactive"})
    CONFLICT = "idempotency_conflict"
    INSUFFICIENT = "insufficient_credits"

    def clean_params(self, params):
        params = super().clean_params(params)
        _positive_int(params, "credits", required=True)
        _positive_int(params, "expires_in_days", required=False)
        description = params.get("description", "")
        if not isinstance(description, str) or len(description) > 200:
            raise ValueError("description must be a string of at most 200 characters")
        return params

    def value(self, params):
        return Decimal(int(params.get("credits") or 0)).quantize(Decimal("0.01"))

    def _metadata(self, reward) -> dict:
        return {"referral_reward_id": str(reward.pk), "rule_id": str(reward.rule_id), "role": reward.role}

    def _with_identity(self, payload: dict, reward) -> dict:
        identity = _beneficiary_identity(reward.beneficiary_id)
        if identity:
            payload["identity"] = identity
        return payload

    def _answer(self, result: dict, what: str) -> None:
        if result.get("ok") is True:
            return
        reason = str(result.get("reason") or "")
        if reason in self.REFUSALS:
            raise StrategyRefused(reason)
        if reason == self.CONFLICT:
            raise StrategyConflict(f"billing refused the {what}: {reason}")
        raise StrategyError(f"billing refused the {what}: {reason or 'no reason'}")

    def grant(self, reward):
        params = reward.params or {}
        payload = {
            "user_id": reward.beneficiary_id,
            "credits": int(params["credits"]),
            "idempotency_key": idempotency_key(reward),
            "source": "grant",
            "type": "adjustment",
            "description": params.get("description") or "Referral reward",
            "metadata": self._metadata(reward),
        }
        expires_at = _expires_at(params)
        if expires_at:
            payload["expires_at"] = expires_at
        result = call_function("BILLING_CREDIT_FUNCTION", self._with_identity(payload, reward))
        self._answer(result, "credit")
        return str(result.get("transaction_id") or "")

    def _plan(self, reward, credits: int) -> None:
        type(reward).objects.filter(pk=reward.pk).update(reversed_value=Decimal(credits))
        reward.reversed_value = Decimal(credits)

    def reverse(self, reward):
        full = int((reward.params or {})["credits"])
        planned = full if reward.reversed_value is None else int(reward.reversed_value)
        if reward.reversed_value is None:
            self._plan(reward, planned)
        while planned > 0:
            result = call_function("BILLING_DEBIT_FUNCTION", self._with_identity({
                "user_id": reward.beneficiary_id,
                "credits": planned,
                "idempotency_key": idempotency_key(reward, ":reverse"),
                "type": "adjustment",
                "description": "Referral reward reversed",
                "metadata": {**self._metadata(reward), "requested": full},
            }, reward))
            if result.get("ok") is True:
                break
            reason = result.get("reason")
            balance = result.get("balance")
            if reason in self.REFUSALS:
                planned = 0  # no wallet to take from: the account is gone or never paid
            elif reason == self.INSUFFICIENT and isinstance(balance, int) and balance < planned:
                planned = max(0, balance)  # nothing was debited under the key yet
            else:
                self._answer(result, "reversal")
                raise StrategyError(f"billing refused the reversal: {reason or 'no reason'}")
            self._plan(reward, planned)
        if planned == full:
            return None
        outcome = "reversed_partial" if planned else "already_spent"
        return Reversal(outcome=outcome, value=Decimal(planned).quantize(Decimal("0.01")))


class PromoCodeStrategy(RewardStrategy):
    """A single-use promo code of an existing promotion: ``{promotion_key, value?}``.

    Grant: ``promotions.codes.issue {promotion_key, assigned_user_id,
    usage_limit: 1, idempotency_key}`` -> ``{promotion_key, codes: [str]}``
    (stapel-promotions >= 0.1.0 with the issue key). The key is
    ``referrals:reward:<id>``: a retry after a lost answer gets the same code
    back and mints nothing. An unknown promotion or a key conflict fails the
    call (the reward stays pending with ``last_error``).

    Reversal: ``promotions.codes.revoke {code, reason}`` (idempotent by
    state; it takes no key). A code already redeemed cannot be un-spent:
    it is revoked against further use and the reversal ends
    ``already_spent``.
    """

    name = "promo_code"
    unit = "code"
    function_settings = ("PROMO_ISSUE_FUNCTION", "PROMO_REVOKE_FUNCTION")

    #: promotions.codes.issue: promotion_key maxLength.
    PROMOTION_KEY_MAX_LENGTH = 58
    description = "A single-use code of an existing promotion, assigned to the beneficiary."
    params_schema = {
        "type": "object",
        "required": ["promotion_key"],
        "properties": {
            "promotion_key": {"type": "string", "minLength": 1, "maxLength": PROMOTION_KEY_MAX_LENGTH,
                              "description": "Key of an existing promotion in stapel-promotions."},
            "value": _VALUE_SCHEMA,
        },
        "additionalProperties": False,
    }
    #: promotions.codes.revoke: reason maxLength.
    REVOKE_REASON_MAX_LENGTH = 64

    def clean_params(self, params):
        params = super().clean_params(params)
        key = params.get("promotion_key")
        if not isinstance(key, str) or not key or len(key) > self.PROMOTION_KEY_MAX_LENGTH:
            raise ValueError(
                f"promotion_key must be a promotion key of at most {self.PROMOTION_KEY_MAX_LENGTH} characters"
            )
        if "value" in params:
            params["value"] = str(_decimal(params["value"]))
        return params

    def grant(self, reward):
        params = reward.params or {}
        result = call_function("PROMO_ISSUE_FUNCTION", {
            "promotion_key": params["promotion_key"],
            "assigned_user_id": reward.beneficiary_id,
            "usage_limit": 1,
            "idempotency_key": idempotency_key(reward),
        })
        codes = result.get("codes")
        if not isinstance(codes, list) or not codes or not isinstance(codes[0], str) or not codes[0]:
            raise StrategyError("promotions answered without a code")
        return codes[0]

    def reverse(self, reward):
        from .conf import referrals_settings

        if not reward.grant_ref:
            return None  # nothing was issued
        if not referrals_settings.PROMO_REVOKE_FUNCTION:
            raise StrategyError("PROMO_REVOKE_FUNCTION is empty: the issued code cannot be revoked")
        result = call_function("PROMO_REVOKE_FUNCTION", {
            "code": reward.grant_ref,
            "reason": (f"referrals:{reward.reason}" if reward.reason else "referrals:reversed")
            [: self.REVOKE_REASON_MAX_LENGTH],
        })
        if result.get("revoked") is not True:
            raise StrategyError("promotions did not confirm the revocation")
        if result.get("redeemed") is True:
            return Reversal(outcome="already_spent", value=Decimal("0.00"))
        return None


def strategy_paths() -> dict:
    """Built-ins merged with ``REWARD_STRATEGIES`` (``None`` removes)."""
    from .conf import referrals_settings

    merged = dict(BUILTIN_STRATEGIES)
    extra = referrals_settings.REWARD_STRATEGIES or {}
    if isinstance(extra, dict):
        for name, path in extra.items():
            if path is None:
                merged.pop(name, None)
            else:
                merged[str(name)] = str(path)
    return merged


def params_problem(strategy, params) -> str | None:
    """Why *params* break the strategy's ``params_schema``, or None."""
    from jsonschema import Draft202012Validator

    schema = getattr(strategy, "params_schema", None) or {"type": "object"}
    error = next(iter(sorted(Draft202012Validator(schema).iter_errors(params), key=lambda e: list(e.path))), None)
    if error is None:
        return None
    where = "/".join(str(p) for p in error.path)
    return f"{where}: {error.message}" if where else error.message


def registered_strategies() -> list[tuple[str, RewardStrategy]]:
    """``(name, instance)`` for every registered strategy that resolves, by name."""
    out = []
    for name in sorted(strategy_paths()):
        try:
            out.append((name, get_strategy(name)))
        except LookupError:
            continue
    return out


def get_strategy(name: str) -> RewardStrategy:
    """Instance for a registered name or a dotted path; ``LookupError`` otherwise."""
    paths = strategy_paths()
    path = paths.get(name) or (name if "." in (name or "") else None)
    if not path:
        raise LookupError(name)
    try:
        cls = import_string(path)
    except ImportError as exc:
        raise LookupError(name) from exc
    strategy = cls() if isinstance(cls, type) else cls
    if not all(hasattr(strategy, attr) for attr in ("clean_params", "value", "grant", "reverse")):
        raise LookupError(name)
    return strategy


__all__ = [
    "BUILTIN_STRATEGIES",
    "BillingCreditStrategy",
    "NoopStrategy",
    "PromoCodeStrategy",
    "Reversal",
    "RewardStrategy",
    "StrategyConflict",
    "StrategyError",
    "StrategyRefused",
    "get_strategy",
    "params_problem",
    "registered_strategies",
    "strategy_paths",
]
