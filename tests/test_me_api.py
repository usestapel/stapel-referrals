"""The referrer's and referee's own views."""
import pytest

from stapel_referrals.models import Reward

from .helpers import V1, claim, my_code, rule, signed_up

pytestmark = pytest.mark.django_db


def test_me_mints_and_counts(referrer, referee, client_for):
    rule(beneficiary="both")
    body = client_for(referrer).get(f"{V1}/me/").json()
    assert body["counts"] == {"referrals": 0, "rewarded_referrals": 0, "pending_rewards": 0, "granted_rewards": 0}
    assert body["vanity_allowed"] is False and body["referred"] is False and body["rewards_total"] == []
    claim(referee, body["code"])
    signed_up(referee)
    body = client_for(referrer).get(f"{V1}/me/").json()
    assert body["counts"] == {"referrals": 1, "rewarded_referrals": 1, "pending_rewards": 0, "granted_rewards": 1}
    assert body["rewards_total"] == [{"unit": "none", "value": "10.00"}]
    assert client_for(referee).get(f"{V1}/me/").json()["referred"] is True


def test_guest_gets_a_code(guest, client_for):
    r = client_for(guest).get(f"{V1}/me/")
    assert r.status_code == 200 and r.json()["code"]


def test_anonymous_request_is_refused(client_for, db):
    assert client_for().get(f"{V1}/me/").status_code in (401, 403)


class TestMyReferrals:
    def _setup(self, referrer, n):
        from conftest import make_user

        rule(beneficiary="referrer")
        friends = []
        for i in range(n):
            friend = make_user(f"friend{i}", email=f"friend{i}@mail.example")
            claim(friend, my_code(referrer))
            friends.append(friend)
        signed_up(friends[0])
        return friends

    def test_masked_by_default(self, referrer, client_for):
        self._setup(referrer, 2)
        page = client_for(referrer).get(f"{V1}/me/referrals/").json()
        assert [x["referee_label"] for x in page["items"]] == ["f***@m***.example"] * 2
        assert [x["rewards_granted"] for x in page["items"]] == [0, 1]
        assert "friend1@mail.example" not in str(page)

    def test_hidden(self, referrer, client_for, settings):
        settings.STAPEL_REFERRALS = {"REFEREE_DISPLAY": "hidden"}
        self._setup(referrer, 1)
        page = client_for(referrer).get(f"{V1}/me/referrals/").json()
        assert page["items"][0]["referee_label"] is None

    def test_anchor_pages(self, referrer, client_for):
        self._setup(referrer, 3)
        c = client_for(referrer)
        first = c.get(f"{V1}/me/referrals/", {"limit": 2}).json()
        assert len(first["items"]) == 2 and first["has_next"] and isinstance(first["next_anchor"], str)
        second = c.get(f"{V1}/me/referrals/", {"limit": 2, "anchor": first["next_anchor"]}).json()
        assert len(second["items"]) == 1 and not second["has_next"]

    def test_only_mine_and_refused_hidden(self, referrer, referee, client_for):
        from stapel_referrals.models import Attribution

        self._setup(referrer, 1)
        Attribution.objects.update(status="refused")
        assert client_for(referrer).get(f"{V1}/me/referrals/").json()["items"] == []
        assert client_for(referee).get(f"{V1}/me/referrals/").json()["items"] == []


def test_my_rewards(referrer, referee, client_for, providers):
    rule(strategy="promo_code", referrer_params={"promotion_key": "friend"}, referee_params={"promotion_key": "hello"})
    claim(referee, my_code(referrer))
    signed_up(referee)
    mine = client_for(referee).get(f"{V1}/me/rewards/").json()["items"]
    assert len(mine) == 1 and mine[0]["role"] == "referee" and mine[0]["grant_ref"].startswith("GIFT")
    assert mine[0]["rule_name"] == "Invite bonus"
    theirs = client_for(referrer).get(f"{V1}/me/rewards/").json()["items"]
    assert [x["role"] for x in theirs] == ["referrer"]
    assert Reward.objects.count() == 2
