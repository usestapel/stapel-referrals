"""Race proofs for every counter: rule caps/budgets, the campaign-code cap,
one attribution per referee.

SQLite has no row locks (Django drops FOR UPDATE there), so two OS threads
cannot prove a lock on this harness. What is proved, per counter:

1. the lock is taken — ``select_for_update()`` on the counter's row —
   BEFORE the count (ordering spy);
2. two interleaved writers: the rival commits after our unlocked
   preparation and before our lock; we count after the lock, so the end is
   one success and one refusal;
3. inversion: with the count taken before the lock (the classic defect),
   the same interleaving over-spends — the test can see the defect.
On Postgres the lock makes step 2 the only possible interleaving. The
attribution has no counter: its uniqueness is the database constraint.
"""
import pytest
from django.db.models import QuerySet

from conftest import make_user
from stapel_referrals import guards
from stapel_referrals.models import Attribution, ReferralCode, Reward
from stapel_referrals.services import ReferralsError
from stapel_referrals.services import attribution as attribution_service
from stapel_referrals.services import codes as code_service
from stapel_referrals.services import rewards as reward_service

from .helpers import claim, my_code, rule, signed_up

pytestmark = pytest.mark.django_db(transaction=True)


def _spy_locks(monkeypatch, order):
    real_sfu = QuerySet.select_for_update

    def sfu_spy(self, *a, **kw):
        order.append(f"lock:{self.model.__name__}")
        return real_sfu(self, *a, **kw)

    monkeypatch.setattr(QuerySet, "select_for_update", sfu_spy)


class TestRuleCap:
    def _two_referees(self):
        owner = make_user("owner")
        a, b = make_user("ref-a"), make_user("ref-b")
        code = my_code(owner)
        claim(a, code)
        claim(b, code)
        return a, b

    def test_lock_is_taken_before_the_count(self, monkeypatch):
        rule(beneficiary="referrer", cap_per_referrer=1)
        a, _ = self._two_referees()
        order = []
        _spy_locks(monkeypatch, order)
        real = reward_service._cap_verdict

        def verdict_spy(*args, **kwargs):
            order.append("count")
            return real(*args, **kwargs)

        monkeypatch.setattr(reward_service, "_cap_verdict", verdict_spy)
        signed_up(a)
        assert order.index("lock:RewardRule") < order.index("count")

    def _interleave(self, monkeypatch, a, b):
        """b's signup commits after a's strategy lookup, before a's lock."""
        real_get = reward_service.get_strategy
        state = {"raced": False}

        def rival_first(name):
            if not state["raced"]:
                state["raced"] = True
                signed_up(b)
            return real_get(name)

        monkeypatch.setattr(reward_service, "get_strategy", rival_first)
        signed_up(a)

    def test_two_triggers_one_cap_slot(self, monkeypatch):
        rule(beneficiary="referrer", cap_per_referrer=1)
        a, b = self._two_referees()
        self._interleave(monkeypatch, a, b)
        assert sorted(Reward.objects.values_list("status", "reason")) == [("granted", ""),
                                                                          ("rejected", "cap_reached")]

    def test_two_triggers_one_budget_unit(self, monkeypatch):
        rule(beneficiary="referrer", budget_count=1)
        a, b = self._two_referees()
        self._interleave(monkeypatch, a, b)
        assert Reward.objects.filter(status="granted").count() == 1
        assert Reward.objects.filter(status="rejected", reason="budget_exhausted").count() == 1

    def test_inversion_counting_before_the_lock_overspends(self, monkeypatch):
        rule(beneficiary="referrer", cap_per_referrer=1)
        a, b = self._two_referees()
        real = reward_service._cap_verdict
        stale = {}

        def early_verdict(rule_row, referrer_id, value, now):
            # A lock-less check: the verdict computed once, before the rival wrote.
            return stale.setdefault(referrer_id, real(rule_row, referrer_id, value, now))

        real_get = reward_service.get_strategy
        state = {"raced": False}

        def snapshot_then_rival(name):
            if not state["raced"]:
                state["raced"] = True
                from django.utils import timezone

                from stapel_referrals.models import RewardRule

                early_verdict(RewardRule.objects.get(), str(Attribution.objects.first().referrer_id),
                              0, timezone.now())
                signed_up(b)
            return real_get(name)

        monkeypatch.setattr(reward_service, "_cap_verdict", early_verdict)
        monkeypatch.setattr(reward_service, "get_strategy", snapshot_then_rival)
        signed_up(a)
        assert Reward.objects.filter(status="granted").count() == 2  # the defect is visible


class TestCodeCap:
    def test_lock_is_taken_before_the_count(self, monkeypatch):
        staff = make_user("staff", is_staff=True)
        code_service.create_campaign(staff, {"code": "ONCE", "max_attributions": 1})
        order = []
        _spy_locks(monkeypatch, order)
        real_create = Attribution.objects.create

        def create_spy(**kw):
            order.append("insert")
            return real_create(**kw)

        monkeypatch.setattr(Attribution.objects, "create", create_spy)
        claim(make_user("a"), "ONCE")
        assert order.index("lock:ReferralCode") < order.index("insert")

    def _interleave(self, monkeypatch, rival_user):
        real = guards.first_objection
        state = {"raced": False}

        def rival_first(ctx):
            if not state["raced"]:
                state["raced"] = True
                claim(rival_user, "ONCE")
            return real(ctx)

        monkeypatch.setattr(guards, "first_objection", rival_first)

    def test_two_referees_last_use(self, monkeypatch):
        staff = make_user("staff", is_staff=True)
        code_service.create_campaign(staff, {"code": "ONCE", "max_attributions": 1})
        a, b = make_user("a"), make_user("b")
        self._interleave(monkeypatch, b)
        with pytest.raises(ReferralsError) as caught:
            claim(a, "ONCE")
        assert caught.value.key == "error.409.code_exhausted"
        assert Attribution.objects.get().referee_id == str(b.pk)
        assert ReferralCode.objects.get(code="ONCE").attribution_count == 1

    def test_inversion_reading_the_counter_unlocked_overspends(self, monkeypatch):
        staff = make_user("staff", is_staff=True)
        code_service.create_campaign(staff, {"code": "ONCE", "max_attributions": 1})
        a, b = make_user("a"), make_user("b")
        stale = ReferralCode.objects.get(code="ONCE")  # read before the rival, as an unlocked check would

        class StaleQS:
            def get(self, **kw):
                return stale

        real_sfu = QuerySet.select_for_update
        monkeypatch.setattr(QuerySet, "select_for_update",
                            lambda self, *a, **k: StaleQS() if self.model is ReferralCode else real_sfu(self, *a, **k))
        self._interleave(monkeypatch, b)
        claim(a, "ONCE")
        assert Attribution.objects.count() == 2  # the defect is visible


class TestOneAttributionPerReferee:
    def _interleave(self, monkeypatch, referee, rival_code):
        real = guards.first_objection
        state = {"raced": False}

        def rival_first(ctx):
            if not state["raced"]:
                state["raced"] = True
                claim(referee, rival_code)
            return real(ctx)

        monkeypatch.setattr(guards, "first_objection", rival_first)

    def test_racing_claims_with_two_codes(self, monkeypatch):
        x, y, referee = make_user("x"), make_user("y"), make_user("r")
        code_x, code_y = my_code(x), my_code(y)
        self._interleave(monkeypatch, referee, code_y)
        with pytest.raises(ReferralsError) as caught:
            claim(referee, code_x)
        assert caught.value.key == "error.409.already_attributed"
        assert Attribution.objects.get().referrer_id == str(y.pk)

    def test_racing_claims_with_one_code_replay(self, monkeypatch):
        x, referee = make_user("x"), make_user("r")
        code = my_code(x)
        self._interleave(monkeypatch, referee, code)
        row, created = attribution_service.claim(referee, code)
        assert created is False and Attribution.objects.count() == 1
        assert ReferralCode.objects.get(code=code).attribution_count == 1
