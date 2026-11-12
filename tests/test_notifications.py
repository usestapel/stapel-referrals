"""``referrals.reward_granted``: the variables match the type stapel-notifications ships.

stapel-notifications (>= 0.30.1) registers the type with the caller variables
``value``, ``unit`` and ``grant_ref``: the credits line renders for
``unit == "credits"``, the code line when ``grant_ref`` is set.
"""
import pytest

from stapel_referrals.services import events

from .helpers import claim, my_code, rule, signed_up

pytestmark = pytest.mark.django_db(transaction=True)

#: stapel-notifications' docs/templates.json, referrals.reward_granted context.caller.
TEMPLATE_CALLER_VARIABLES = {"value", "unit", "grant_ref"}


@pytest.fixture
def sent(monkeypatch):
    out = []
    monkeypatch.setattr(events, "notify", lambda kind, user_id, data: out.append((kind, user_id, data)))
    return out


def test_credit_reward_variables(referrer, referee, providers, sent):
    rule(strategy="billing_credit", beneficiary="referee", referee_params={"credits": 50})
    claim(referee, my_code(referrer))
    signed_up(referee)
    ((kind, user_id, data),) = sent
    assert (kind, user_id) == ("referrals.reward_granted", str(referee.pk))
    assert TEMPLATE_CALLER_VARIABLES <= set(data)
    assert (data["value"], data["unit"], data["grant_ref"]) == ("50.00", "credits", "")


def test_code_reward_variables(referrer, referee, providers, sent):
    rule(strategy="promo_code", beneficiary="referrer", referrer_params={"promotion_key": "friend-10"})
    claim(referee, my_code(referrer))
    signed_up(referee)
    ((kind, user_id, data),) = sent
    assert user_id == str(referrer.pk) and data["unit"] == "code" and data["grant_ref"].startswith("GIFT")


def test_billing_transaction_id_is_not_a_code_line(referrer, referee, providers, sent):
    rule(strategy="billing_credit", beneficiary="referrer", referrer_params={"credits": 1})
    claim(referee, my_code(referrer))
    signed_up(referee)
    assert sent[0][2]["grant_ref"] == ""
