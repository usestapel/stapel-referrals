"""Staff API: rules, campaign codes, the ledger, the FSM table, stats."""
import itertools

import pytest

from stapel_referrals.models import Reward, RewardStatus
from stapel_referrals.services.rewards import TRANSITIONS

from .helpers import V1, claim, my_code, rule, signed_up

pytestmark = pytest.mark.django_db

STAFF_GETS = ["staff/rules/", "staff/campaign-codes/", "staff/rewards/", "staff/stats/", "staff/strategies/"]


@pytest.mark.parametrize("path", STAFF_GETS)
def test_staff_only(path, referrer, guest, client_for):
    assert client_for(referrer).get(f"{V1}/{path}").status_code == 403
    assert client_for(guest).get(f"{V1}/{path}").status_code == 403
    assert client_for().get(f"{V1}/{path}").status_code in (401, 403)


class TestRules:
    def body(self, **extra):
        return {"name": "Friend bonus", "trigger": "signup", "beneficiary": "both", "strategy": "billing_credit",
                "referrer_params": {"credits": 100}, "referee_params": {"credits": 50}, **extra}

    def test_create_list_update(self, staff, client_for):
        c = client_for(staff)
        r = c.post(f"{V1}/staff/rules/", self.body(cap_per_referrer=5, cap_period_days=30), format="json")
        assert r.status_code == 201, r.content
        rule_id = r.json()["id"]
        assert r.json()["rewards"] == {"pending": 0, "granted": 0, "rejected": 0, "reversed": 0}
        listed = c.get(f"{V1}/staff/rules/").json()
        assert [x["id"] for x in listed] == [rule_id]
        r = c.patch(f"{V1}/staff/rules/{rule_id}/", {"active": False}, format="json")
        assert r.status_code == 200 and r.json()["active"] is False
        assert r.json()["cap_per_referrer"] == 5  # a partial update keeps the rest
        assert c.get(f"{V1}/staff/rules/{rule_id}/").json()["active"] is False

    def test_missing_rule(self, staff, client_for):
        r = client_for(staff).get(f"{V1}/staff/rules/999/")
        assert r.status_code == 404 and r.json()["localizable_error"] == "error.404.reward_rule_not_found"

    @pytest.mark.parametrize("extra,key", [
        ({"strategy": "nope"}, "error.400.reward_strategy_unknown"),
        ({"referrer_params": {}}, "error.400.reward_params_invalid"),
        ({"trigger": "first_order", "min_order_total": "10.00"}, "error.400.reward_rule_invalid"),
        ({"min_order_total": "10.00", "currency": "USD"}, "error.400.reward_rule_invalid"),
        ({"cap_period_days": 7}, "error.400.reward_rule_invalid"),
        ({"starts_at": "2026-02-01T00:00:00Z", "ends_at": "2026-01-01T00:00:00Z"}, "error.400.reward_rule_invalid"),
        ({"campaign_code": 999}, "error.400.reward_rule_invalid"),
    ])
    def test_invalid(self, staff, client_for, extra, key):
        r = client_for(staff).post(f"{V1}/staff/rules/", self.body(**extra), format="json")
        assert r.status_code == 400 and r.json()["localizable_error"] == key

    def test_campaign_code_must_be_a_campaign(self, staff, referrer, client_for):
        from stapel_referrals.models import ReferralCode

        my_code(referrer)
        personal = ReferralCode.objects.get().pk
        r = client_for(staff).post(f"{V1}/staff/rules/", self.body(campaign_code=personal), format="json")
        assert r.status_code == 400

    def test_campaign_bound_rule(self, staff, referrer, referee, client_for):
        c = client_for(staff)
        code = c.post(f"{V1}/staff/campaign-codes/", {"code": "fair", "owner_id": str(referrer.pk)},
                      format="json").json()
        r = c.post(f"{V1}/staff/rules/", self.body(strategy="noop", referrer_params={}, referee_params={},
                                                   campaign_code=code["id"]), format="json")
        assert r.status_code == 201
        other = client_for(referee).post(f"{V1}/claim/", {"code": my_code(staff)}, format="json")
        assert other.status_code == 201
        signed_up(referee)
        assert Reward.objects.count() == 0  # not through the campaign code


class TestCampaignCodes:
    def test_create_minted_and_chosen(self, staff, client_for, settings):
        settings.STAPEL_REFERRALS = {"SHARE_URL_TEMPLATE": "https://shop.example/r/{code}"}
        c = client_for(staff)
        minted = c.post(f"{V1}/staff/campaign-codes/", {"label": "Spring"}, format="json")
        assert minted.status_code == 201 and len(minted.json()["code"]) == 8
        assert minted.json()["owner_id"] == str(staff.pk)
        chosen = c.post(f"{V1}/staff/campaign-codes/", {"code": "spring-26", "max_attributions": 100},
                        format="json")
        assert chosen.json()["code"] == "SPRING-26"
        assert chosen.json()["link"] == "https://shop.example/r/SPRING-26"
        page = c.get(f"{V1}/staff/campaign-codes/").json()
        assert [x["code"] for x in page["items"]] == ["SPRING-26", minted.json()["code"]]
        dup = c.post(f"{V1}/staff/campaign-codes/", {"code": "SPRING-26"}, format="json")
        assert dup.status_code == 409

    def test_update_never_changes_the_code(self, staff, client_for):
        c = client_for(staff)
        made = c.post(f"{V1}/staff/campaign-codes/", {"code": "keep-me"}, format="json").json()
        r = c.patch(f"{V1}/staff/campaign-codes/{made['id']}/", {"code": "other", "active": False},
                    format="json")
        assert r.status_code == 200 and r.json()["code"] == "KEEP-ME" and r.json()["active"] is False

    def test_personal_code_is_not_a_campaign(self, staff, referrer, client_for):
        from stapel_referrals.models import ReferralCode

        my_code(referrer)
        r = client_for(staff).patch(f"{V1}/staff/campaign-codes/{ReferralCode.objects.get().pk}/",
                                    {"active": False}, format="json")
        assert r.status_code == 404


@pytest.fixture
def ledger(referrer, referee):
    rule(beneficiary="referrer", hold_days=5)
    claim(referee, my_code(referrer))
    signed_up(referee)
    return Reward.objects.get()


class TestLedger:
    def test_list_and_filter(self, staff, ledger, client_for):
        c = client_for(staff)
        page = c.get(f"{V1}/staff/rewards/").json()
        assert [x["id"] for x in page["items"]] == [ledger.pk]
        assert page["items"][0]["status"] == "pending" and page["items"][0]["strategy"] == "noop"
        assert c.get(f"{V1}/staff/rewards/", {"status": "granted"}).json()["items"] == []
        assert c.get(f"{V1}/staff/rewards/", {"rule": ledger.rule_id + 1}).json()["items"] == []
        assert c.get(f"{V1}/staff/rewards/", {"anchor": "garbage"}).status_code == 200

    def test_missing_reward(self, staff, client_for, db):
        r = client_for(staff).post(f"{V1}/staff/rewards/9/transition/", {"to": "rejected"}, format="json")
        assert r.status_code == 404

    def test_stats(self, staff, ledger, client_for):
        body = client_for(staff).get(f"{V1}/staff/stats/").json()
        assert body["attributions"] == 1 and body["personal_codes"] == 1 and body["active_rules"] == 1
        assert body["rewards"]["pending"] == 1
        assert body["top_referrers"] == [{"referrer_id": ledger.referrer_id, "referrals": 1}]


ALL = list(RewardStatus.values)
TARGETS = ("granted", "rejected", "reversed")


@pytest.mark.parametrize("frm,to", list(itertools.product(ALL, TARGETS)))
def test_fsm_table(frm, to, staff, ledger, client_for):
    """Every edge in TRANSITIONS answers 200; every other edge 409 (inversion)."""
    Reward.objects.filter(pk=ledger.pk).update(status=frm)
    r = client_for(staff).post(f"{V1}/staff/rewards/{ledger.pk}/transition/", {"to": to, "reason": "check"},
                               format="json")
    if (frm, to) in TRANSITIONS:
        assert r.status_code == 200, r.content
        assert r.json()["status"] == to
    else:
        assert r.status_code == 409
        assert r.json()["localizable_error"] == "error.409.reward_transition_invalid"
        assert Reward.objects.get(pk=ledger.pk).status == frm


def test_staff_grant_with_provider_down_is_503(staff, referrer, referee, client_for):
    rule(beneficiary="referrer", hold_days=5, strategy="billing_credit", referrer_params={"credits": 1})
    claim(referee, my_code(referrer))
    signed_up(referee)
    reward = Reward.objects.get()
    r = client_for(staff).post(f"{V1}/staff/rewards/{reward.pk}/transition/", {"to": "granted"}, format="json")
    assert r.status_code == 503
    assert r.json()["localizable_error"] == "error.503.reward_provider_unavailable"
    assert Reward.objects.get().status == "pending"


class TestStrategies:
    def test_builtins_with_their_params_schema(self, staff, client_for):
        r = client_for(staff).get(f"{V1}/staff/strategies/")
        assert r.status_code == 200, r.content
        items = {i["name"]: i for i in r.json()["items"]}
        assert set(items) == {"noop", "billing_credit", "promo_code"}
        assert all(i["builtin"] for i in items.values())
        assert (items["billing_credit"]["unit"], items["promo_code"]["unit"]) == ("credits", "code")
        assert items["billing_credit"]["params_schema"]["required"] == ["credits"]
        assert items["promo_code"]["params_schema"]["properties"]["promotion_key"]["maxLength"] == 58

    def test_project_registered_and_removed(self, staff, client_for, settings):
        settings.STAPEL_REFERRALS = {"REWARD_STRATEGIES": {
            "points": "stapel_referrals.tests.test_strategies.Custom", "promo_code": None}}
        items = {i["name"]: i for i in client_for(staff).get(f"{V1}/staff/strategies/").json()["items"]}
        assert set(items) == {"noop", "billing_credit", "points"}
        assert items["points"]["builtin"] is False and items["points"]["unit"] == "points"
        assert items["points"]["params_schema"] == {"type": "object"}

    @pytest.mark.parametrize("params, where", [
        ({"credits": 5, "colour": "red"}, "referrer_params"),
        ({"credits": "5"}, "referrer_params: credits"),
        ({}, "referrer_params"),
    ])
    def test_rule_writes_validate_against_the_schema(self, staff, client_for, params, where):
        body = {"name": "x", "trigger": "signup", "beneficiary": "referrer", "strategy": "billing_credit",
                "referrer_params": params}
        r = client_for(staff).post(f"{V1}/staff/rules/", body, format="json")
        assert r.status_code == 400
        assert r.json()["localizable_error"] == "error.400.reward_params_invalid"
        assert where in r.json()["params"]["problem"]

    def test_update_validates_too(self, staff, client_for):
        c = client_for(staff)
        created = c.post(f"{V1}/staff/rules/", {"name": "x", "trigger": "signup", "beneficiary": "referee",
                                                "strategy": "promo_code",
                                                "referee_params": {"promotion_key": "w"}}, format="json")
        assert created.status_code == 201, created.content
        r = c.patch(f"{V1}/staff/rules/{created.json()['id']}/",
                    {"referee_params": {"promotion_key": "x" * 59}}, format="json")
        assert r.status_code == 400 and "promotion_key" in r.json()["params"]["problem"]
