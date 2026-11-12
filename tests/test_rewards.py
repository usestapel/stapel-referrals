"""Reward rules fire on facts; the ledger is idempotent, capped and held."""
import datetime as dt
from decimal import Decimal

import pytest
from django.utils import timezone

from stapel_referrals.models import Attribution, QualifyingOrder, Reward
from stapel_referrals.services import rewards as reward_service

from .helpers import claim, my_code, order_cancelled, order_placed, rule, signed_up

pytestmark = pytest.mark.django_db


@pytest.fixture
def referred(referrer, referee):
    return claim(referee, my_code(referrer))


def statuses():
    return sorted((r.role, r.status) for r in Reward.objects.all())


class TestSignup:
    def test_signup_after_claim(self, referred, referee, captured_events):
        rule()
        signed_up(referee)
        assert statuses() == [("referee", "granted"), ("referrer", "granted")]
        r = Reward.objects.get(role="referrer")
        assert (r.value, r.unit, r.beneficiary_id) == (Decimal("10.00"), "none", referred.referrer_id)
        assert [e.event_type for e in captured_events].count("referrals.reward.granted") == 2

    def test_claim_after_signup_still_pays(self, referrer, referee):
        rule()
        signed_up(referee)
        assert Reward.objects.count() == 0
        claim(referee, my_code(referrer))
        assert statuses() == [("referee", "granted"), ("referrer", "granted")]

    def test_replayed_fact_is_a_no_op(self, referred, referee):
        rule()
        signed_up(referee)
        signed_up(referee)
        assert Reward.objects.count() == 2

    def test_unattributed_signup_pays_nothing(self, referee):
        rule()
        signed_up(referee)
        assert Reward.objects.count() == 0

    def test_refused_attribution_pays_nothing(self, referred, referee):
        Attribution.objects.update(status="refused")
        rule()
        signed_up(referee)
        assert Reward.objects.count() == 0

    @pytest.mark.parametrize("beneficiary,roles", [("referrer", ["referrer"]), ("referee", ["referee"])])
    def test_beneficiary(self, referred, referee, beneficiary, roles):
        rule(beneficiary=beneficiary)
        signed_up(referee)
        assert sorted(Reward.objects.values_list("role", flat=True)) == roles

    def test_inactive_and_out_of_window_rules_do_not_fire(self, referred, referee):
        now = timezone.now()
        rule(name="off", active=False)
        rule(name="later", starts_at=now + dt.timedelta(days=1))
        rule(name="over", ends_at=now - dt.timedelta(days=1), starts_at=now - dt.timedelta(days=2))
        signed_up(referee)
        assert Reward.objects.count() == 0

    def test_code_kind_filter(self, referred, referee):
        rule(code_kind="campaign")
        signed_up(referee)
        assert Reward.objects.count() == 0


class TestOrders:
    def test_first_qualifying_order(self, referred, referee):
        rule(trigger="first_order", min_order_total="30", currency="USD", hold_days=0)
        order_placed(referee, "20.00")          # below the minimum: not qualifying
        order_placed(referee, "40.00", "EUR")   # other currency: not qualifying
        assert Reward.objects.count() == 0
        order_placed(referee, "35.00")
        assert statuses() == [("referee", "granted"), ("referrer", "granted")]
        order_placed(referee, "99.00")          # the second qualifying order fires nothing
        assert Reward.objects.count() == 2

    def test_nth_order(self, referred, referee):
        rule(trigger="nth_order", order_ordinal=3, beneficiary="referrer", hold_days=0)
        for _ in range(2):
            order_placed(referee)
        assert Reward.objects.count() == 0
        order_placed(referee)
        assert Reward.objects.count() == 1
        order_placed(referee)
        assert Reward.objects.count() == 1

    def test_order_before_attribution_does_not_count(self, referred, referee):
        rule(trigger="first_order", hold_days=0)
        order_placed(referee, at=referred.attributed_at - dt.timedelta(minutes=1))
        assert Reward.objects.count() == 0 and QualifyingOrder.objects.count() == 0

    def test_order_after_the_qualifying_window(self, referred, referee):
        rule(trigger="first_order", hold_days=0)
        order_placed(referee, at=referred.qualifying_until + dt.timedelta(seconds=1))
        assert Reward.objects.count() == 0

    def test_replayed_order_fact(self, referred, referee):
        rule(trigger="first_order", hold_days=0)
        order_placed(referee, order_id="o-1")
        order_placed(referee, order_id="o-1")
        assert Reward.objects.count() == 2 and QualifyingOrder.objects.count() == 1

    def test_hold_then_beat_grants(self, referred, referee):
        rule(trigger="first_order")  # HOLD_DAYS default 14
        order_placed(referee, order_id="o-1")
        assert {r.status for r in Reward.objects.all()} == {"pending"}
        assert reward_service.grant_due() == 0
        later = timezone.now() + dt.timedelta(days=15)
        assert reward_service.grant_due(now=later) == 2
        assert {r.status for r in Reward.objects.all()} == {"granted"}

    def test_cancel_during_hold_rejects(self, referred, referee, captured_events):
        rule(trigger="first_order")
        order_placed(referee, order_id="o-1")
        order_cancelled(referee, "o-1")
        assert {(r.status, r.reason) for r in Reward.objects.all()} == {("rejected", "order_cancelled")}
        assert reward_service.grant_due(now=timezone.now() + dt.timedelta(days=30)) == 0
        assert [e.event_type for e in captured_events].count("referrals.reward.rejected") == 2

    def test_cancelled_first_order_lets_the_next_one_qualify(self, referred, referee):
        rule(trigger="first_order")
        order_placed(referee, order_id="o-1")
        order_cancelled(referee, "o-1")
        order_placed(referee, order_id="o-2")
        assert sorted(Reward.objects.values_list("trigger_id", "status")) == [
            ("o-1", "rejected"), ("o-1", "rejected"), ("o-2", "pending"), ("o-2", "pending")]

    def test_cancel_after_grant_reverses(self, referred, referee, providers, captured_events):
        rule(trigger="first_order", strategy="billing_credit", hold_days=0,
             referrer_params={"credits": 100}, referee_params={"credits": 50})
        order_placed(referee, order_id="o-1")
        assert {r.status for r in Reward.objects.all()} == {"granted"}
        order_cancelled(referee, "o-1")
        assert {r.status for r in Reward.objects.all()} == {"reversed"}
        debits = providers.calls["billing.debit"]
        assert sorted(d["credits"] for d in debits) == [50, 100]
        assert all(d["allow_partial"] and d["idempotency_key"].endswith(":reverse") for d in debits)
        assert [e.event_type for e in captured_events].count("referrals.reward.reversed") == 2

    def test_cancel_after_the_reversal_window_keeps_the_grant(self, referred, referee):
        rule(trigger="first_order", hold_days=0)
        order_placed(referee, order_id="o-1")
        Reward.objects.update(granted_at=timezone.now() - dt.timedelta(days=31))
        order_cancelled(referee, "o-1")
        assert {r.status for r in Reward.objects.all()} == {"granted"}

    def test_other_status_changes_are_ignored(self, referred, referee):
        from .helpers import fact

        rule(trigger="first_order")
        order_placed(referee, order_id="o-1")
        fact("orders.order.status_changed", {"order_id": "o-1", "user_id": str(referee.pk),
                                             "from": "new", "to": "confirmed", "actor": "staff"})
        assert {r.status for r in Reward.objects.all()} == {"pending"}


class TestCaps:
    def _referee(self, name):
        from conftest import make_user

        return make_user(name)

    def test_cap_per_referrer(self, referrer):
        rule(beneficiary="referrer", cap_per_referrer=2)
        for i in range(3):
            friend = self._referee(f"f{i}")
            claim(friend, my_code(referrer))
            signed_up(friend)
        assert sorted(Reward.objects.values_list("status", "reason")) == [
            ("granted", ""), ("granted", ""), ("rejected", "cap_reached")]

    def test_cap_period_rolls(self, referrer):
        rule(beneficiary="referrer", cap_per_referrer=1, cap_period_days=30)
        first = self._referee("f1")
        claim(first, my_code(referrer))
        signed_up(first)
        Reward.objects.update(created_at=timezone.now() - dt.timedelta(days=31))
        second = self._referee("f2")
        claim(second, my_code(referrer))
        signed_up(second)
        assert sorted(Reward.objects.values_list("status", flat=True)) == ["granted", "granted"]

    def test_cap_is_per_referrer(self, referrer, staff):
        rule(beneficiary="referrer", cap_per_referrer=1)
        for i, owner in enumerate((referrer, staff)):
            friend = self._referee(f"f{i}")
            claim(friend, my_code(owner))
            signed_up(friend)
        assert set(Reward.objects.values_list("status", flat=True)) == {"granted"}

    def test_budget_count(self, referrer):
        rule(budget_count=1)
        for i in range(2):
            friend = self._referee(f"f{i}")
            claim(friend, my_code(referrer))
            signed_up(friend)
        assert sorted(Reward.objects.values_list("status", "reason")) == [
            ("granted", ""), ("granted", ""), ("rejected", "budget_exhausted"), ("rejected", "budget_exhausted")]

    def test_budget_value(self, referrer):
        rule(budget_value="25")  # one trigger costs 10 + 5
        for i in range(2):
            friend = self._referee(f"f{i}")
            claim(friend, my_code(referrer))
            signed_up(friend)
        assert Reward.objects.filter(status="granted").count() == 2
        assert Reward.objects.filter(status="rejected", reason="budget_exhausted").count() == 2

    def test_reversed_rewards_free_the_budget(self, referrer):
        rule(trigger="first_order", budget_count=1, hold_days=0, beneficiary="referrer")
        a, b = self._referee("f1"), self._referee("f2")
        claim(a, my_code(referrer))
        claim(b, my_code(referrer))
        order_placed(a, order_id="o-a")
        order_cancelled(a, "o-a")
        order_placed(b, order_id="o-b")
        assert sorted(Reward.objects.values_list("trigger_id", "status")) == [
            ("o-a", "reversed"), ("o-b", "granted")]


class TestGrantFailures:
    def test_provider_down_keeps_pending_and_the_beat_retries(self, referred, referee, providers):
        rule(strategy="billing_credit", referrer_params={"credits": 100}, referee_params={"credits": 50})
        providers.fail.add("billing.credit")
        signed_up(referee)
        rows = list(Reward.objects.all())
        assert {r.status for r in rows} == {"pending"}
        assert all(r.attempts == 1 and "billing.credit failed" in r.last_error for r in rows)
        providers.fail.clear()
        assert reward_service.grant_due() == 2
        granted = Reward.objects.filter(status="granted")
        assert granted.count() == 2 and all(r.grant_ref.startswith("txn-") for r in granted)
        keys = [c["idempotency_key"] for c in providers.calls["billing.credit"]]
        assert len(keys) == 4 and len(set(keys)) == 2  # retries reuse the key

    def test_billing_absent_is_never_a_rejection(self, referred, referee):
        rule(strategy="billing_credit", referrer_params={"credits": 1}, referee_params={"credits": 1})
        signed_up(referee)
        assert {(r.status, r.reason) for r in Reward.objects.all()} == {("pending", "")}
        assert "unreachable" in Reward.objects.first().last_error

    def test_reversal_retried_by_the_beat(self, referred, referee, providers):
        rule(trigger="first_order", strategy="billing_credit", hold_days=0, beneficiary="referrer",
             referrer_params={"credits": 10})
        order_placed(referee, order_id="o-1")
        providers.fail.add("billing.debit")
        order_cancelled(referee, "o-1")
        reward = Reward.objects.get()
        assert reward.status == "granted" and reward.reverse_requested_at is not None
        providers.fail.clear()
        assert reward_service.retry_reversals() == 1
        assert Reward.objects.get().status == "reversed"

    def test_rejected_while_paying_is_taken_back(self, referred, referee, providers, monkeypatch):
        from stapel_referrals import strategies

        rule(beneficiary="referrer", strategy="billing_credit", referrer_params={"credits": 5}, hold_days=3)
        signed_up(referee)
        reward = Reward.objects.get()
        real = strategies.BillingCreditStrategy.grant

        def grant_then_rejected(self, r):
            ref = real(self, r)
            Reward.objects.filter(pk=r.pk).update(status="rejected", reason="staff")
            return ref

        monkeypatch.setattr(strategies.BillingCreditStrategy, "grant", grant_then_rejected)
        reward_service.grant(reward.pk)
        assert reward_service.retry_reversals() == 1
        row = Reward.objects.get()
        assert row.status == "rejected" and row.reversed_at is not None
        assert len(providers.calls["billing.debit"]) == 1
