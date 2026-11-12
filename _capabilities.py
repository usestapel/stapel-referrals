"""stapel-referrals capabilities.json emitter — thin shim over stapel_tools.capabilities."""
from pathlib import Path

from stapel_tools.capabilities import axis_group_rules, run_capabilities_cli


def main(argv=None):
    from stapel_referrals._codegen import _configure

    _configure()
    from stapel_referrals.conf import DEFAULTS
    from stapel_referrals.urls import GATE_REGISTRY

    # Four CTO-facing axes, all behavioural (no endpoint is unmounted):
    # VANITY_CODES (who may choose a code), CLAIM_REQUIRES_ACCOUNT (guest
    # claims), HOLD_DAYS (order rewards wait before the grant) and
    # REFEREE_DISPLAY (what a referrer sees of a referee). REWARD_STRATEGIES
    # and ATTRIBUTION_GUARDS are the extension seams.
    axes = {"VANITY_CODES", "CLAIM_REQUIRES_ACCOUNT", "HOLD_DAYS", "REFEREE_DISPLAY"}
    return run_capabilities_cli(
        argv,
        repo=Path(__file__).resolve().parent,
        canonical_prefix="/referrals/api/v1",
        defaults=DEFAULTS,
        registry=GATE_REGISTRY,
        is_axis=lambda k: k in axes,
        axis_group=axis_group_rules(
            exact={
                "VANITY_CODES": "referrals.codes",
                "CLAIM_REQUIRES_ACCOUNT": "referrals.attribution",
                "HOLD_DAYS": "referrals.rewards",
                "REFEREE_DISPLAY": "referrals.privacy",
            }
        ),
        prog="stapel-referrals-capabilities",
    )


if __name__ == "__main__":
    raise SystemExit(main())
