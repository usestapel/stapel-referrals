"""Django system checks for stapel-referrals.

Every check is static (settings only, no database, no neighbour): the
module boots green with or without billing, orders, promotions or auth
installed — a missing neighbour is a rule that waits, not a broken boot.

- E001  a ``REWARD_STRATEGIES`` entry does not import as a strategy.
- E002  an ``ATTRIBUTION_GUARDS`` entry does not import.
- E003  ``SHARE_URL_TEMPLATE`` is set but has no ``{code}`` or will not format.
- E004  ``VANITY_CODES`` / ``REFEREE_DISPLAY`` is not one of its values.
- E005  a window or length setting is out of range.
"""
from django.core import checks


@checks.register(checks.Tags.compatibility)
def check_strategies(app_configs, **kwargs):
    from .conf import referrals_settings
    from .strategies import get_strategy, strategy_paths

    raw = referrals_settings.REWARD_STRATEGIES
    if raw is not None and not isinstance(raw, dict):
        return [checks.Error(
            "STAPEL_REFERRALS['REWARD_STRATEGIES'] must be a dict of {name: 'dotted.path' | None}.",
            id="stapel_referrals.E001",
        )]
    findings = []
    for name in sorted(strategy_paths()):
        try:
            get_strategy(name)
        except LookupError:
            findings.append(checks.Error(
                f"Reward strategy {name!r} ({strategy_paths()[name]!r}) does not import as a "
                "strategy (clean_params/value/grant/reverse).",
                id="stapel_referrals.E001",
            ))
    return findings


@checks.register(checks.Tags.compatibility)
def check_guards(app_configs, **kwargs):
    from .guards import configured_guards

    try:
        configured_guards()
    except (ImportError, TypeError, ValueError) as exc:
        return [checks.Error(
            f"STAPEL_REFERRALS['ATTRIBUTION_GUARDS'] has an entry that does not import: {exc}",
            hint="Use 'same_device', 'duplicate_contact' or a dotted path to a guard function.",
            id="stapel_referrals.E002",
        )]
    return []


@checks.register(checks.Tags.compatibility)
def check_share_template(app_configs, **kwargs):
    from .conf import referrals_settings

    template = referrals_settings.SHARE_URL_TEMPLATE or ""
    if not template:
        return []
    try:
        ok = "{code}" in template and template.format(code="ABCD2345")
    except (KeyError, IndexError, ValueError):
        ok = False
    if ok:
        return []
    return [checks.Error(
        f"STAPEL_REFERRALS['SHARE_URL_TEMPLATE'] {template!r} must contain {{code}} and no other field.",
        id="stapel_referrals.E003",
    )]


@checks.register(checks.Tags.compatibility)
def check_enums(app_configs, **kwargs):
    from .conf import REFEREE_DISPLAYS, VANITY_POLICIES, referrals_settings

    findings = []
    for key, allowed in (("VANITY_CODES", VANITY_POLICIES), ("REFEREE_DISPLAY", REFEREE_DISPLAYS)):
        value = getattr(referrals_settings, key)
        if value not in allowed:
            findings.append(checks.Error(
                f"STAPEL_REFERRALS['{key}'] is {value!r}; expected one of {', '.join(allowed)}.",
                id="stapel_referrals.E004",
            ))
    return findings


@checks.register(checks.Tags.compatibility)
def check_ranges(app_configs, **kwargs):
    from .codes import CODE_MIN_LENGTH
    from .conf import referrals_settings
    from .models import CODE_MAX_LENGTH

    findings = []
    bounds = {
        "CODE_LENGTH": (CODE_MIN_LENGTH, CODE_MAX_LENGTH),
        "ATTRIBUTION_WINDOW_DAYS": (1, 3660),
        "QUALIFYING_WINDOW_DAYS": (1, 3660),
        "CLAIM_MAX_ACCOUNT_AGE_HOURS": (0, 87600),
        "HOLD_DAYS": (0, 365),
        "REVERSAL_WINDOW_DAYS": (0, 3660),
        "VANITY_MAX_CHANGES": (0, 100),
    }
    for key, (low, high) in bounds.items():
        value = getattr(referrals_settings, key)
        if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
            findings.append(checks.Error(
                f"STAPEL_REFERRALS['{key}'] is {value!r}; expected an integer from {low} to {high}.",
                id="stapel_referrals.E005",
            ))
    return findings
