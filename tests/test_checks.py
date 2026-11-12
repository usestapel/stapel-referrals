"""System checks: each with the case that fires and the one that must not —
and the module boots clean with and without billing / orders / promotions."""
import pytest
from django.core import checks
from django.core.management import call_command

from stapel_referrals import checks as rf_checks


def ids(findings):
    return [f.id for f in findings]


def ours():
    return [f for f in checks.run_checks() if f.id.startswith("stapel_referrals")]


class TestNeighbours:
    def test_clean_without_any_neighbour(self):
        from stapel_core.comm import function_unreachable_reason

        assert function_unreachable_reason("billing.credit")
        assert function_unreachable_reason("promotions.codes.issue")
        assert ours() == []

    def test_clean_with_billing_and_promotions(self, providers):
        assert ours() == []

    def test_clean_with_a_rule_naming_an_absent_neighbour(self, db):
        from .helpers import rule

        rule(strategy="billing_credit", referrer_params={"credits": 1}, referee_params={"credits": 1})
        assert ours() == []

    def test_manage_check_fail_level_error(self):
        call_command("check", "--fail-level", "ERROR", "--tag", "compatibility")


class TestStrategies:
    def test_bad_entry_is_e001(self, settings):
        settings.STAPEL_REFERRALS = {"REWARD_STRATEGIES": {"x": "no.such.Strategy"}}
        assert ids(rf_checks.check_strategies(None)) == ["stapel_referrals.E001"]

    def test_not_a_dict_is_e001(self, settings):
        settings.STAPEL_REFERRALS = {"REWARD_STRATEGIES": ["x"]}
        assert ids(rf_checks.check_strategies(None)) == ["stapel_referrals.E001"]

    def test_removal_is_fine(self, settings):
        settings.STAPEL_REFERRALS = {"REWARD_STRATEGIES": {"promo_code": None}}
        assert rf_checks.check_strategies(None) == []


class TestGuards:
    def test_bad_guard_is_e002(self, settings):
        settings.STAPEL_REFERRALS = {"ATTRIBUTION_GUARDS": ["nope.guard"]}
        assert ids(rf_checks.check_guards(None)) == ["stapel_referrals.E002"]

    def test_builtins_resolve(self, settings):
        settings.STAPEL_REFERRALS = {"ATTRIBUTION_GUARDS": ["same_device", "duplicate_contact"]}
        assert rf_checks.check_guards(None) == []


@pytest.mark.parametrize("template,fires", [("", False), ("https://x/r/{code}", False),
                                            ("https://x/r/", True), ("https://x/{code}/{other}", True)])
def test_share_template(settings, template, fires):
    settings.STAPEL_REFERRALS = {"SHARE_URL_TEMPLATE": template}
    assert ids(rf_checks.check_share_template(None)) == (["stapel_referrals.E003"] if fires else [])


@pytest.mark.parametrize("key,value", [("VANITY_CODES", "everyone"), ("REFEREE_DISPLAY", "full")])
def test_enums(settings, key, value):
    settings.STAPEL_REFERRALS = {key: value}
    assert ids(rf_checks.check_enums(None)) == ["stapel_referrals.E004"]


@pytest.mark.parametrize("key,value", [("CODE_LENGTH", 4), ("CODE_LENGTH", 40), ("HOLD_DAYS", -1),
                                       ("ATTRIBUTION_WINDOW_DAYS", 0), ("VANITY_MAX_CHANGES", True)])
def test_ranges(settings, key, value):
    settings.STAPEL_REFERRALS = {key: value}
    assert ids(rf_checks.check_ranges(None)) == ["stapel_referrals.E005"]


def test_defaults_are_silent():
    for check in (rf_checks.check_strategies, rf_checks.check_guards, rf_checks.check_share_template,
                  rf_checks.check_enums, rf_checks.check_ranges):
        assert check(None) == []
