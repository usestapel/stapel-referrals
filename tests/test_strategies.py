"""The strategy registry and the payloads each built-in sends."""
from decimal import Decimal

import pytest

from stapel_referrals.models import Reward
from stapel_referrals.strategies import (
    BillingCreditStrategy,
    NoopStrategy,
    PromoCodeStrategy,
    RewardStrategy,
    StrategyError,
    call_function,
    get_strategy,
)

from .helpers import claim, my_code, rule, signed_up


class Custom(RewardStrategy):
    name = "custom"
    unit = "points"

    def value(self, params):
        return Decimal(params.get("points", 0))

    def grant(self, reward):
        return f"points:{reward.params['points']}"


class TestRegistry:
    def test_builtins(self):
        assert isinstance(get_strategy("noop"), NoopStrategy)
        assert isinstance(get_strategy("billing_credit"), BillingCreditStrategy)
        assert isinstance(get_strategy("promo_code"), PromoCodeStrategy)

    def test_unknown(self):
        with pytest.raises(LookupError):
            get_strategy("nope")
        with pytest.raises(LookupError):
            get_strategy("not.a.module.Strategy")

    def test_merge_and_remove(self, settings):
        settings.STAPEL_REFERRALS = {"REWARD_STRATEGIES": {
            "points": "stapel_referrals.tests.test_strategies.Custom", "promo_code": None}}
        assert type(get_strategy("points")).__name__ == "Custom"
        with pytest.raises(LookupError):
            get_strategy("promo_code")

    def test_dotted_path(self):
        assert type(get_strategy("stapel_referrals.tests.test_strategies.Custom")).__name__ == "Custom"

    def test_not_a_strategy(self):
        with pytest.raises(LookupError):
            get_strategy("stapel_referrals.tests.test_strategies.TestRegistry")


class TestParams:
    @pytest.mark.parametrize("params", [{}, {"credits": 0}, {"credits": "5"}, {"credits": True},
                                        {"credits": 5, "expires_in_days": -1},
                                        {"credits": 5, "description": "x" * 201}])
    def test_billing_credit_refuses(self, params):
        with pytest.raises(ValueError):
            BillingCreditStrategy().clean_params(params)

    def test_billing_credit_value_is_credits(self):
        assert BillingCreditStrategy().value({"credits": 7}) == Decimal("7.00")

    @pytest.mark.parametrize("params", [{}, {"promotion_key": ""}, {"promotion_key": "x" * 59},
                                        {"promotion": "old-name"}, {"promotion_key": "p", "value": "-1"}])
    def test_promo_refuses(self, params):
        with pytest.raises(ValueError):
            PromoCodeStrategy().clean_params(params)

    @pytest.mark.parametrize("value", ["abc", "-1", "1e20", "NaN"])
    def test_noop_value(self, value):
        with pytest.raises(ValueError):
            NoopStrategy().clean_params({"value": value})


@pytest.mark.django_db
class TestPayloads:
    def test_billing_credit(self, referrer, referee, providers):
        rule(strategy="billing_credit", beneficiary="referee",
             referee_params={"credits": 50, "expires_in_days": 30, "description": "Welcome"})
        claim(referee, my_code(referrer))
        signed_up(referee)
        (call,) = providers.calls["billing.credit"]
        reward = Reward.objects.get()
        assert call["user_id"] == str(referee.pk) and call["credits"] == 50
        assert call["idempotency_key"] == f"referrals:reward:{reward.pk}"
        assert (call["source"], call["type"], call["description"]) == ("grant", "adjustment", "Welcome")
        assert "expires_at" in call and call["metadata"]["role"] == "referee"
        assert (reward.status, reward.unit, reward.grant_ref) == ("granted", "credits", "txn-1")

    def test_billing_refusal_keeps_pending(self, referrer, referee, providers):
        providers.refuse_credit = "wallet_frozen"
        rule(strategy="billing_credit", beneficiary="referee", referee_params={"credits": 1})
        claim(referee, my_code(referrer))
        signed_up(referee)
        reward = Reward.objects.get()
        assert reward.status == "pending" and "wallet_frozen" in reward.last_error

    def test_promo_code(self, referrer, referee, providers):
        rule(strategy="promo_code", beneficiary="both",
             referrer_params={"promotion_key": "friend-10", "value": "10"},
             referee_params={"promotion_key": "welcome-5"})
        claim(referee, my_code(referrer))
        signed_up(referee)
        issued = providers.calls["promotions.codes.issue"]
        assert sorted(c["promotion_key"] for c in issued) == ["friend-10", "welcome-5"]
        assert all(c["usage_limit"] == 1 for c in issued)
        assert {c["assigned_user_id"] for c in issued} == {str(referrer.pk), str(referee.pk)}
        assert set(Reward.objects.values_list("unit", flat=True)) == {"code"}
        assert all(r.grant_ref.startswith("GIFT") for r in Reward.objects.all())

    def test_promo_reversal_without_a_revoke_seam(self, referrer, referee, providers):
        from stapel_referrals.services.rewards import request_reversal

        rule(strategy="promo_code", beneficiary="referrer", referrer_params={"promotion_key": "friend-10"})
        claim(referee, my_code(referrer))
        signed_up(referee)
        reward = request_reversal(Reward.objects.get().pk, "staff")
        assert reward.status == "reversed"
        assert providers.calls["promotions.codes.revoke"] == []  # nothing to call yet (ask P-RF1)

    def test_promo_reversal_with_a_revoke_seam(self, referrer, referee, providers, settings):
        from stapel_referrals.services.rewards import request_reversal

        settings.STAPEL_REFERRALS = {"PROMO_REVOKE_FUNCTION": "promotions.codes.revoke"}
        rule(strategy="promo_code", beneficiary="referrer", referrer_params={"promotion_key": "friend-10"})
        claim(referee, my_code(referrer))
        signed_up(referee)
        request_reversal(Reward.objects.get().pk, "staff")
        (revoke,) = providers.calls["promotions.codes.revoke"]
        assert revoke["code"].startswith("GIFT") and revoke["idempotency_key"].endswith(":reverse")

    def test_unknown_promotion_keeps_pending(self, referrer, referee, providers):
        rule(strategy="promo_code", beneficiary="referee", referee_params={"promotion_key": "missing"})
        claim(referee, my_code(referrer))
        signed_up(referee)
        reward = Reward.objects.get()
        assert reward.status == "pending" and "promotions.codes.issue failed" in reward.last_error

    def test_custom_strategy(self, referrer, referee, settings):
        settings.STAPEL_REFERRALS = {"REWARD_STRATEGIES": {
            "points": "stapel_referrals.tests.test_strategies.Custom"}}
        rule(strategy="points", beneficiary="referrer", referrer_params={"points": 7})
        claim(referee, my_code(referrer))
        signed_up(referee)
        reward = Reward.objects.get()
        assert (reward.unit, reward.value, reward.grant_ref) == ("points", Decimal("7.00"), "points:7")

    def test_unreachable_function(self, db):
        with pytest.raises(StrategyError, match="unreachable"):
            call_function("BILLING_CREDIT_FUNCTION", {})

    def test_empty_function_name(self, settings):
        settings.STAPEL_REFERRALS = {"BILLING_CREDIT_FUNCTION": ""}
        with pytest.raises(StrategyError, match="empty"):
            call_function("BILLING_CREDIT_FUNCTION", {})
