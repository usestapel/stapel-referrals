"""The comm Functions this module provides, called through comm with schemas on."""
import pytest
from stapel_core.comm import call

from .helpers import claim, my_code, rule, signed_up

pytestmark = pytest.mark.django_db


def test_attribution_get(referrer, referee):
    assert call("referrals.attribution.get", {"user_id": str(referee.pk)})["referred"] is False
    claim(referee, my_code(referrer))
    got = call("referrals.attribution.get", {"user_id": str(referee.pk)})
    assert got["referred"] is True and got["referrer_id"] == str(referrer.pk) and got["code_kind"] == "personal"


def test_code_resolve(referrer):
    code = my_code(referrer)
    assert call("referrals.code.resolve", {"code": code.lower()}) == {
        "found": True, "claimable": True, "kind": "personal", "owner_id": str(referrer.pk)}
    assert call("referrals.code.resolve", {"code": "NOPE2345"})["found"] is False


def test_export_pages(referrer, db):
    from conftest import make_user

    rule()
    for i in range(3):
        friend = make_user(f"f{i}")
        claim(friend, my_code(referrer))
        signed_up(friend)
    first = call("referrals.export", {"limit": 4})
    assert first["total"] == 6 and len(first["rows"]) == 4 and first["cursor"]
    rest = call("referrals.export", {"cursor": first["cursor"], "limit": 4})
    assert len(rest["rows"]) == 2 and rest["cursor"] is None
    assert rest["rows"][0]["value"] in ("10.00", "5.00")


def test_payload_schema_is_enforced(db):
    from stapel_core.comm.exceptions import SchemaValidationError

    with pytest.raises(SchemaValidationError):
        call("referrals.attribution.get", {"user": "x"})
