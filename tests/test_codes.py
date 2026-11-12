"""Personal codes: lazy, unique, collision-proof; vanity by policy; share links."""
import pytest
from django.db import IntegrityError

from stapel_referrals import codes
from stapel_referrals.models import ReferralCode
from stapel_referrals.services import codes as code_service

from .helpers import V1, claim, my_code

pytestmark = pytest.mark.django_db


class TestLazyPersonalCode:
    def test_minted_on_first_read_and_stable(self, referrer):
        assert ReferralCode.objects.count() == 0
        first = my_code(referrer)
        assert my_code(referrer) == first
        assert ReferralCode.objects.count() == 1
        assert len(first) == 8 and set(first) <= set(codes.ALPHABET)

    def test_alphabet_has_no_look_alikes(self):
        assert not set("01OIL") & set(codes.ALPHABET)
        assert len(codes.ALPHABET) == 31

    def test_code_length_setting(self, referrer, settings):
        settings.STAPEL_REFERRALS = {"CODE_LENGTH": 12}
        assert len(my_code(referrer)) == 12

    def test_collision_retries_on_the_constraint(self, referrer, referee, monkeypatch):
        taken = my_code(referee)
        minted = iter([taken, taken, "ZZZZ2345"])
        monkeypatch.setattr(codes, "mint", lambda length: next(minted))
        assert my_code(referrer) == "ZZZZ2345"

    def test_concurrent_first_reads_end_on_one_row(self, referrer, monkeypatch):
        """The rival mints between our read and our insert: we re-read its row."""
        real_primary = code_service._primary
        state = {"raced": False}

        def stale_first_read(owner_id):
            if not state["raced"]:
                state["raced"] = True
                ReferralCode.objects.create(code="RIVAL234", owner_id=owner_id, kind="personal", is_primary=True)
                return None
            return real_primary(owner_id)

        monkeypatch.setattr(code_service, "_primary", stale_first_read)
        assert my_code(referrer) == "RIVAL234"
        assert ReferralCode.objects.filter(owner_id=str(referrer.pk)).count() == 1

    def test_inversion_the_constraint_is_what_holds(self, referrer):
        my_code(referrer)
        with pytest.raises(IntegrityError):
            ReferralCode.objects.create(code="SECOND23", owner_id=str(referrer.pk), kind="personal",
                                        is_primary=True)


class TestVanity:
    def _patch(self, client_for, user, code):
        return client_for(user).patch(f"{V1}/me/code/", {"code": code}, format="json")

    def test_off_by_default(self, referrer, client_for):
        r = self._patch(client_for, referrer, "alice-2026")
        assert r.status_code == 403
        assert r.json()["localizable_error"] == "error.403.vanity_code_not_allowed"

    def test_users_policy(self, referrer, referee, client_for, settings):
        settings.STAPEL_REFERRALS = {"VANITY_CODES": "users"}
        old = my_code(referrer)
        r = self._patch(client_for, referrer, "alice-2026")
        assert r.status_code == 200, r.content
        assert r.json()["code"] == "ALICE-2026"
        # The old code is retired but still names its owner.
        assert claim(referee, old).referrer_id == str(referrer.pk)

    def test_staff_policy(self, referrer, staff, client_for, settings):
        settings.STAPEL_REFERRALS = {"VANITY_CODES": "staff"}
        assert self._patch(client_for, referrer, "alice-x").status_code == 403
        assert self._patch(client_for, staff, "team-x").status_code == 200

    def test_guest_never(self, guest, client_for, settings):
        settings.STAPEL_REFERRALS = {"VANITY_CODES": "users"}
        assert self._patch(client_for, guest, "guest-x").status_code == 403

    @pytest.mark.parametrize("bad,problem", [("ab", None), ("a b c d", "shape"), ("admin", "reserved"),
                                             ("-lead", "shape")])
    def test_invalid(self, referrer, client_for, settings, bad, problem):
        settings.STAPEL_REFERRALS = {"VANITY_CODES": "users"}
        r = self._patch(client_for, referrer, bad)
        assert r.status_code == 400
        if problem:
            assert r.json()["localizable_error"] == "error.400.vanity_code_invalid"

    def test_taken(self, referrer, referee, client_for, settings):
        settings.STAPEL_REFERRALS = {"VANITY_CODES": "users"}
        assert self._patch(client_for, referee, "SAME-CODE").status_code == 200
        r = self._patch(client_for, referrer, "same-code")
        assert r.status_code == 409 and r.json()["localizable_error"] == "error.409.code_taken"
        # The refused change left the caller's primary code in place.
        assert ReferralCode.objects.filter(owner_id=str(referrer.pk), is_primary=True).count() == 1

    def test_change_limit(self, referrer, client_for, settings):
        settings.STAPEL_REFERRALS = {"VANITY_CODES": "users", "VANITY_MAX_CHANGES": 1}
        assert self._patch(client_for, referrer, "first-1").status_code == 200
        r = self._patch(client_for, referrer, "second-2")
        assert r.status_code == 409 and r.json()["params"]["max"] == 1

    def test_same_code_is_a_no_op(self, referrer, client_for, settings):
        settings.STAPEL_REFERRALS = {"VANITY_CODES": "users", "VANITY_MAX_CHANGES": 1}
        assert self._patch(client_for, referrer, "mine-1").status_code == 200
        assert self._patch(client_for, referrer, "MINE-1").status_code == 200


class TestShareLink:
    def test_unset_is_null(self, referrer, client_for):
        assert client_for(referrer).get(f"{V1}/me/").json()["link"] is None

    def test_template(self, referrer, client_for, settings):
        settings.STAPEL_REFERRALS = {"SHARE_URL_TEMPLATE": "https://shop.example/r/{code}"}
        body = client_for(referrer).get(f"{V1}/me/").json()
        assert body["link"] == f"https://shop.example/r/{body['code']}"

    def test_broken_template_is_null_not_500(self, referrer, settings):
        settings.STAPEL_REFERRALS = {"SHARE_URL_TEMPLATE": "https://x/{nope}"}
        assert codes.share_link("ABC") is None


class TestMask:
    @pytest.mark.parametrize("raw,masked", [("alice@example.com", "a***@e***.com"), ("bob", "b***"),
                                            ("", ""), ("x@localhost", "x***@l***")])
    def test_mask(self, raw, masked):
        assert codes.mask(raw) == masked


class TestLookup:
    def test_public_lookup(self, referrer, client_for):
        code = my_code(referrer)
        r = client_for().get(f"{V1}/codes/{code.lower()}/")
        assert r.status_code == 200
        assert r.json() == {"code": code, "valid": True, "kind": "personal", "referrer_label": "a***@e***.com"}

    def test_unknown_is_200_invalid(self, client_for, db):
        body = client_for().get(f"{V1}/codes/NOPE2345/").json()
        assert body == {"code": "NOPE2345", "valid": False, "kind": None, "referrer_label": None}

    def test_campaign_outside_window_is_invalid(self, staff, client_for):
        from django.utils import timezone

        code = code_service.create_campaign(staff, {"code": "SUMMER", "label": "Summer",
                                                    "ends_at": timezone.now()})
        assert client_for().get(f"{V1}/codes/{code.code}/").json()["valid"] is False
