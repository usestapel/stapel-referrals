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

from stapel_referrals.services.rewards import grant_due, retry_reversals

from .helpers import claim, my_code, order_cancelled, order_placed, rule, signed_up


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
        assert call["identity"] == {"is_anonymous": False, "auth_type": "email"}
        assert (reward.status, reward.unit, reward.grant_ref) == ("granted", "credits", "txn-1")
        assert providers.wallets[str(referee.pk)] == 50

    def test_no_identity_without_a_signup_fact(self, referrer, referee, providers):
        rule(trigger="first_order", strategy="billing_credit", beneficiary="referrer", hold_days=0,
             referrer_params={"credits": 5})
        claim(referee, my_code(referrer))
        providers.unmet.add(str(referrer.pk))
        order_placed(referee)
        (call,) = providers.calls["billing.credit"]
        reward = Reward.objects.get()
        assert "identity" not in call and (reward.status, reward.reason) == ("rejected", "user_unknown")

    def test_identity_lets_billing_meet_a_new_referee(self, referrer, referee, providers):
        rule(strategy="billing_credit", beneficiary="referee", referee_params={"credits": 3})
        providers.unmet.add(str(referee.pk))
        claim(referee, my_code(referrer))
        signed_up(referee)
        assert Reward.objects.get().status == "granted" and providers.wallets[str(referee.pk)] == 3

    @pytest.mark.parametrize("reason", ["user_unknown", "user_inactive"])
    def test_unknown_or_inactive_beneficiary_rejects(self, referrer, referee, providers, reason,
                                                       captured_events):
        providers.refuse_credit = reason
        rule(strategy="billing_credit", beneficiary="referee", referee_params={"credits": 1})
        claim(referee, my_code(referrer))
        signed_up(referee)
        reward = Reward.objects.get()
        assert (reward.status, reward.reason, reward.last_error) == ("rejected", reason, "")
        assert [e.event_type for e in captured_events][-1] == "referrals.reward.rejected"

    def test_idempotency_conflict_stays_pending_with_an_error_log(self, referrer, referee, providers,
                                                                 caplog):
        rule(strategy="billing_credit", beneficiary="referee", referee_params={"credits": 7}, hold_days=1)
        claim(referee, my_code(referrer))
        signed_up(referee)
        reward_id = Reward.objects.get().pk
        providers.wallets[str(referee.pk)] = 0
        providers.keys[(str(referee.pk), f"referrals:reward:{reward_id}")] = ("debit", 3, "txn-x")
        Reward.objects.update(hold_until=None)
        with caplog.at_level("ERROR", logger="stapel_referrals.services.rewards"):
            grant_due()
        reward = Reward.objects.get()
        assert reward.status == "pending"
        assert "idempotency_conflict" in reward.last_error
        assert any(r.levelname == "ERROR" and str(reward_id) in r.getMessage() for r in caplog.records)
        assert providers.wallets[str(referee.pk)] == 0

    @pytest.mark.parametrize("reason", ["invalid_expires_at", "something_new", None])
    def test_other_refusals_retry(self, referrer, referee, providers, reason):
        providers.refuse_credit = reason or ""
        rule(strategy="billing_credit", beneficiary="referee", referee_params={"credits": 1})
        claim(referee, my_code(referrer))
        if reason is None:
            providers.fail.add("billing.credit")  # unanswered
        signed_up(referee)
        reward = Reward.objects.get()
        assert reward.status == "pending" and reward.last_error

    def test_lost_credit_answer_is_retried_under_the_same_key(self, referrer, referee, providers):
        rule(strategy="billing_credit", beneficiary="referee", referee_params={"credits": 9})
        claim(referee, my_code(referrer))
        providers.lose_answer.add("billing.credit")
        signed_up(referee)
        assert Reward.objects.get().status == "pending"
        grant_due()
        assert Reward.objects.get().status == "granted"
        assert providers.wallets[str(referee.pk)] == 9  # credited once

    def _granted_credit(self, referrer, referee, credits=10):
        rule(trigger="first_order", strategy="billing_credit", beneficiary="referee", hold_days=0,
             referee_params={"credits": credits})
        claim(referee, my_code(referrer))
        signed_up(referee)
        order_placed(referee, order_id="o-1")
        return Reward.objects.get()

    def test_reversal_of_an_unspent_credit(self, referrer, referee, providers):
        self._granted_credit(referrer, referee)
        order_cancelled(referee, "o-1")
        reward = Reward.objects.get()
        (debit,) = providers.calls["billing.debit"]
        assert debit["idempotency_key"] == f"referrals:reward:{reward.pk}:reverse"
        assert "allow_partial" not in debit and debit["credits"] == 10
        assert (reward.status, reward.reversal_outcome, reward.reversed_value) == (
            "reversed", "reversed", Decimal("10.00"))
        assert providers.wallets[str(referee.pk)] == 0

    def test_reversal_of_a_partly_spent_credit_takes_what_is_left(self, referrer, referee, providers,
                                                                  captured_events):
        self._granted_credit(referrer, referee)
        providers.wallets[str(referee.pk)] = 4  # six credits were spent
        order_cancelled(referee, "o-1")
        reward = Reward.objects.get()
        assert [d["credits"] for d in providers.calls["billing.debit"]] == [10, 4]
        assert len({d["idempotency_key"] for d in providers.calls["billing.debit"]}) == 1
        assert (reward.status, reward.reversal_outcome, reward.reversed_value) == (
            "reversed", "reversed_partial", Decimal("4.00"))
        assert providers.wallets[str(referee.pk)] == 0  # no debt, no negative balance
        reversed_fact = [e for e in captured_events if e.event_type == "referrals.reward.reversed"][-1]
        assert reversed_fact.payload["reversal_outcome"] == "reversed_partial"
        assert reversed_fact.payload["reversed_value"] == "4.00"

    def test_reversal_of_a_spent_credit(self, referrer, referee, providers):
        self._granted_credit(referrer, referee)
        providers.wallets[str(referee.pk)] = 0
        order_cancelled(referee, "o-1")
        reward = Reward.objects.get()
        assert [d["credits"] for d in providers.calls["billing.debit"]] == [10]
        assert (reward.status, reward.reversal_outcome, reward.reversed_value) == (
            "reversed", "already_spent", Decimal("0.00"))

    def test_lost_reversal_answer_replays_the_planned_amount(self, referrer, referee, providers):
        self._granted_credit(referrer, referee)
        providers.wallets[str(referee.pk)] = 4
        providers.lose_answer.add("billing.debit")
        order_cancelled(referee, "o-1")
        assert Reward.objects.get().status == "granted"  # the partial debit happened, its answer did not
        providers.wallets[str(referee.pk)] += 100  # a top-up before the retry
        assert retry_reversals() == 1
        reward = Reward.objects.get()
        assert [d["credits"] for d in providers.calls["billing.debit"]] == [10, 4, 4]
        assert reward.reversal_outcome == "reversed_partial" and providers.wallets[str(referee.pk)] == 100

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
        assert {c["idempotency_key"] for c in issued} == {
            f"referrals:reward:{r.pk}" for r in Reward.objects.all()}
        assert set(Reward.objects.values_list("unit", flat=True)) == {"code"}
        assert all(r.grant_ref.startswith("GIFT") for r in Reward.objects.all())

    def test_lost_issue_answer_does_not_mint_a_second_code(self, referrer, referee, providers):
        rule(strategy="promo_code", beneficiary="referee", referee_params={"promotion_key": "welcome-5"})
        claim(referee, my_code(referrer))
        providers.lose_answer.add("promotions.codes.issue")
        signed_up(referee)
        assert Reward.objects.get().status == "pending"
        grant_due()
        reward = Reward.objects.get()
        assert reward.status == "granted" and list(providers.codes) == [reward.grant_ref]

    def _granted_code(self, referrer, referee):
        rule(strategy="promo_code", beneficiary="referrer", referrer_params={"promotion_key": "friend-10"})
        claim(referee, my_code(referrer))
        signed_up(referee)
        return Reward.objects.get()

    def test_promo_reversal_revokes_the_code(self, referrer, referee, providers):
        from stapel_referrals.services.rewards import request_reversal

        reward = self._granted_code(referrer, referee)
        reward = request_reversal(reward.pk, "order_cancelled")
        (revoke,) = providers.calls["promotions.codes.revoke"]
        assert revoke == {"code": reward.grant_ref, "reason": "referrals:order_cancelled"}
        assert (reward.status, reward.reversal_outcome) == ("reversed", "reversed")
        assert providers.codes[reward.grant_ref]["revoked"] is True

    def test_promo_reversal_of_a_redeemed_code(self, referrer, referee, providers):
        from stapel_referrals.services.rewards import request_reversal

        reward = self._granted_code(referrer, referee)
        providers.codes[reward.grant_ref]["redeemed"] = True
        reward = request_reversal(reward.pk, "staff")
        assert (reward.status, reward.reversal_outcome, reward.reversed_value) == (
            "reversed", "already_spent", Decimal("0.00"))

    def test_promo_reversal_of_an_already_revoked_code(self, referrer, referee, providers):
        from stapel_referrals.services.rewards import request_reversal

        reward = self._granted_code(referrer, referee)
        providers.codes[reward.grant_ref]["revoked"] = True
        reward = request_reversal(reward.pk, "staff")
        assert (reward.status, reward.reversal_outcome) == ("reversed", "reversed")

    def test_promo_reversal_without_a_revoke_function_waits(self, referrer, referee, providers, settings):
        from stapel_referrals.services.rewards import request_reversal

        settings.STAPEL_REFERRALS = {"PROMO_REVOKE_FUNCTION": ""}
        reward = request_reversal(self._granted_code(referrer, referee).pk, "staff")
        assert reward.status == "granted" and "PROMO_REVOKE_FUNCTION" in reward.last_error

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
