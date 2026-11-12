"""Reward strategies — how a reward reaches its beneficiary.

A strategy never imports the brick that pays: it calls a comm Function by
the name in settings. Built-ins (``REWARD_STRATEGIES`` merges over them,
``None`` removes one; a rule may also name a dotted path):

- ``noop`` (default) — records the reward, pays nothing;
- ``billing_credit`` — wallet credits through ``BILLING_CREDIT_FUNCTION``
  (``billing.credit``; upstream ask B-RF1), reversed through
  ``BILLING_DEBIT_FUNCTION`` (``billing.debit``, ``allow_partial``);
- ``promo_code`` — a single-use code of an existing promotion, assigned to
  the beneficiary, through ``PROMO_ISSUE_FUNCTION``
  (``promotions.codes.issue``, stapel-promotions >= 0.1.0). Promotions has
  no revoke Function yet: with ``PROMO_REVOKE_FUNCTION`` empty (default) a
  reversal is recorded in the ledger and the issued code stays redeemable
  (upstream ask P-RF1).

Billing calls carry the idempotency key ``referrals:reward:<id>`` (plus
``:reverse`` for a reversal), so the beat may retry freely.
``promotions.codes.issue`` takes no idempotency key: a retry after a lost
answer can mint a second code (ask P-RF1).
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal, InvalidOperation

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


class RewardStrategy:
    """Base class. Subclass, set ``name``/``unit``, implement the four calls."""

    name = ""
    unit = "none"
    #: comm Functions this strategy calls, as settings keys (for checks/docs).
    function_settings: tuple[str, ...] = ()

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

    def reverse(self, reward) -> None:
        """Take *reward* back. Raise ``StrategyError`` to retry later."""


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


class NoopStrategy(RewardStrategy):
    """Records the reward; the host pays by listening to ``referrals.reward.granted``."""

    name = "noop"
    unit = "none"

    def clean_params(self, params):
        params = super().clean_params(params)
        if "value" in params:
            params["value"] = str(_decimal(params["value"]))
        return params


class BillingCreditStrategy(RewardStrategy):
    """Wallet credits: ``{credits, expires_in_days?, description?}``."""

    name = "billing_credit"
    unit = "credits"
    function_settings = ("BILLING_CREDIT_FUNCTION", "BILLING_DEBIT_FUNCTION")

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
        result = call_function("BILLING_CREDIT_FUNCTION", payload)
        if result.get("ok") is False:
            raise StrategyError(f"billing refused the credit: {result.get('reason') or 'no reason'}")
        return str(result.get("transaction_id") or "")

    def reverse(self, reward):
        params = reward.params or {}
        result = call_function("BILLING_DEBIT_FUNCTION", {
            "user_id": reward.beneficiary_id,
            "credits": int(params["credits"]),
            "idempotency_key": idempotency_key(reward, ":reverse"),
            "allow_partial": True,
            "type": "adjustment",
            "description": "Referral reward reversed",
            "metadata": self._metadata(reward),
        })
        if result.get("ok") is False:
            raise StrategyError(f"billing refused the reversal: {result.get('reason') or 'no reason'}")


class PromoCodeStrategy(RewardStrategy):
    """A single-use promo code of an existing promotion: ``{promotion_key, value?}``.

    Calls ``promotions.codes.issue {promotion_key, assigned_user_id,
    usage_limit: 1}`` -> ``{promotion_key, codes: [str]}``; an unknown
    promotion fails the call (the reward stays pending with ``last_error``).
    """

    name = "promo_code"
    unit = "code"
    function_settings = ("PROMO_ISSUE_FUNCTION", "PROMO_REVOKE_FUNCTION")

    #: promotions.codes.issue: promotion_key maxLength.
    PROMOTION_KEY_MAX_LENGTH = 58

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
        })
        codes = result.get("codes")
        if not isinstance(codes, list) or not codes or not isinstance(codes[0], str) or not codes[0]:
            raise StrategyError("promotions answered without a code")
        return codes[0]

    def reverse(self, reward):
        from .conf import referrals_settings

        if not reward.grant_ref or not referrals_settings.PROMO_REVOKE_FUNCTION:
            return  # no revoke seam: the ledger records the reversal, the code stays valid
        call_function("PROMO_REVOKE_FUNCTION", {
            "code": reward.grant_ref,
            "idempotency_key": idempotency_key(reward, ":reverse"),
        })


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
    "RewardStrategy",
    "StrategyError",
    "get_strategy",
    "strategy_paths",
]
