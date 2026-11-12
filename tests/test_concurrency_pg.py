"""The same race proofs with real threads and real row locks (PostgreSQL only).

Skipped on SQLite (no row locks); run with ``STAPEL_TEST_DATABASE_URL``
pointing at a PostgreSQL. Two OS threads meet at a barrier right before the
locked section, so both are inside the race window at once; the lock is the
only thing that orders them. The inversion removes the lock and holds both
threads after their count: then both spend.
"""
import threading

import pytest
from django.db import connection, connections
from django.db.models import QuerySet

from conftest import make_user
from stapel_referrals import guards
from stapel_referrals.models import Attribution, ReferralCode, Reward, RewardRule
from stapel_referrals.services import ReferralsError
from stapel_referrals.services import codes as code_service
from stapel_referrals.services import rewards as reward_service

from .helpers import claim, my_code, rule, signed_up

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(connection.vendor != "postgresql", reason="row locks need PostgreSQL"),
]


def run_both(*jobs):
    """Run each job in its own thread and connection; collect results/errors."""
    results = [None] * len(jobs)

    def wrap(i, job):
        try:
            results[i] = job()
        except Exception as exc:  # noqa: BLE001
            results[i] = exc
        finally:
            connections.close_all()

    threads = [threading.Thread(target=wrap, args=(i, job)) for i, job in enumerate(jobs)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert not any(t.is_alive() for t in threads), "a thread hung"
    return results


def meet_before(monkeypatch, module, name, parties=2):
    """Each thread's first call of module.name waits for the other threads."""
    barrier = threading.Barrier(parties, timeout=10)
    real = getattr(module, name)
    seen = threading.local()

    def waiting(*a, **kw):
        if threading.current_thread() is not threading.main_thread() and not getattr(seen, "met", False):
            seen.met = True
            barrier.wait()
        return real(*a, **kw)

    monkeypatch.setattr(module, name, waiting)


def _referees(n=2):
    owner = make_user("owner")
    code = my_code(owner)
    people = [make_user(f"ref-{i}") for i in range(n)]
    for person in people:
        claim(person, code)
    return people


class TestRuleCapThreads:
    def test_cap_two_threads_one_slot(self, monkeypatch):
        rule(beneficiary="referrer", cap_per_referrer=1)
        a, b = _referees()
        meet_before(monkeypatch, reward_service, "get_strategy")
        run_both(lambda: signed_up(a), lambda: signed_up(b))
        assert sorted(Reward.objects.values_list("status", "reason")) == [("granted", ""),
                                                                          ("rejected", "cap_reached")]

    def test_budget_value_two_threads(self, monkeypatch):
        rule(beneficiary="referrer", budget_value="10", referrer_params={"value": "10"})
        a, b = _referees()
        meet_before(monkeypatch, reward_service, "get_strategy")
        run_both(lambda: signed_up(a), lambda: signed_up(b))
        assert Reward.objects.filter(status="granted").count() == 1

    def test_twenty_threads_cap_of_five(self, monkeypatch):
        rule(beneficiary="referrer", cap_per_referrer=5)
        people = _referees(20)
        meet_before(monkeypatch, reward_service, "get_strategy", parties=20)
        run_both(*[(lambda p=p: signed_up(p)) for p in people])
        assert Reward.objects.filter(status="granted").count() == 5
        assert Reward.objects.filter(status="rejected", reason="cap_reached").count() == 15

    def test_inversion_without_the_lock_both_spend(self, monkeypatch):
        rule(beneficiary="referrer", cap_per_referrer=1)
        a, b = _referees()
        real_sfu = QuerySet.select_for_update
        monkeypatch.setattr(QuerySet, "select_for_update",
                            lambda self, *x, **k: self if self.model is RewardRule else real_sfu(self, *x, **k))
        barrier = threading.Barrier(2, timeout=10)
        real_verdict = reward_service._cap_verdict

        def count_then_wait(*args, **kwargs):
            verdict = real_verdict(*args, **kwargs)
            barrier.wait()
            return verdict

        monkeypatch.setattr(reward_service, "_cap_verdict", count_then_wait)
        run_both(lambda: signed_up(a), lambda: signed_up(b))
        assert Reward.objects.filter(status="granted").count() == 2  # the defect is visible


class TestCodeCapThreads:
    def test_two_referees_last_use(self, monkeypatch):
        staff = make_user("staff", is_staff=True)
        code_service.create_campaign(staff, {"code": "ONCE", "max_attributions": 1})
        a, b = make_user("a"), make_user("b")
        meet_before(monkeypatch, guards, "first_objection")
        results = run_both(lambda: claim(a, "ONCE"), lambda: claim(b, "ONCE"))
        errors = [r for r in results if isinstance(r, Exception)]
        assert len(errors) == 1 and isinstance(errors[0], ReferralsError)
        assert errors[0].key == "error.409.code_exhausted"
        assert Attribution.objects.count() == 1
        assert ReferralCode.objects.get(code="ONCE").attribution_count == 1


class TestOneAttributionThreads:
    def test_two_codes_one_referee(self, monkeypatch):
        x, y, referee = make_user("x"), make_user("y"), make_user("r")
        code_x, code_y = my_code(x), my_code(y)
        meet_before(monkeypatch, guards, "first_objection")
        results = run_both(lambda: claim(referee, code_x), lambda: claim(referee, code_y))
        errors = [r for r in results if isinstance(r, Exception)]
        assert len(errors) == 1 and errors[0].key == "error.409.already_attributed", results
        assert Attribution.objects.filter(referee_id=str(referee.pk)).count() == 1

    def test_same_code_twice_is_one_row_and_one_count(self, monkeypatch):
        x, referee = make_user("x"), make_user("r")
        code = my_code(x)
        meet_before(monkeypatch, guards, "first_objection")
        results = run_both(lambda: claim(referee, code), lambda: claim(referee, code))
        assert not any(isinstance(r, Exception) for r in results), results
        assert Attribution.objects.count() == 1
        assert ReferralCode.objects.get(code=code).attribution_count == 1

    def test_lazy_code_minting_two_threads(self, monkeypatch):
        owner = make_user("owner")
        meet_before(monkeypatch, code_service, "_primary")
        results = run_both(lambda: my_code(owner), lambda: my_code(owner))
        assert results[0] == results[1] and ReferralCode.objects.count() == 1


class TestSignupFactAndClaimThreads:
    def test_fact_and_claim_at_once_make_one_attribution(self, monkeypatch):
        from .test_signup_context import ctx, signed_up_with

        rule()
        x, referee = make_user("x"), make_user("r")
        code = my_code(x)
        meet_before(monkeypatch, guards, "first_objection")
        results = run_both(lambda: signed_up_with(referee, ctx(code)), lambda: claim(referee, code))
        assert not any(isinstance(r, Exception) for r in results), results
        assert Attribution.objects.filter(referee_id=str(referee.pk)).count() == 1
        assert ReferralCode.objects.get(code=code).attribution_count == 1
        assert Reward.objects.count() == 2
