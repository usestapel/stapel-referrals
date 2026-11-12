"""Contract triad + capabilities + llms.txt drift gate, and the schema names.

Regenerate after any change to a serializer/view/url/error key with
``make contract`` and commit docs/. Emission is pinned to Python 3.12
(drf-spectacular renders descriptions differently across minors).
"""
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

if sys.version_info[:2] != (3, 12):
    pytest.skip(
        "contract emission is pinned to Python 3.12 (the CI/monolith pin); "
        f"running {sys.version_info[0]}.{sys.version_info[1]}",
        allow_module_level=True,
    )

REPO = Path(__file__).resolve().parent.parent
DOCS = REPO / "docs"
ARTIFACTS = ("schema.json", "flows.json", "errors.json", "capabilities.json", "llms.txt")
#: Kept identical to the Makefile's LLMS_BUDGET.
LLMS_TXT_BUDGET = "6000"

#: The codegen contract the react pair generates from (docs/SPEC.md §6).
STABLE_COMPONENTS = {
    "BeneficiaryEnum", "CampaignCodePage", "CampaignCodeRequest", "CampaignCodeResponse", "ClaimRequest",
    "ClaimResponse", "CodeKindEnum", "CodeLookupResponse", "MyReferralsResponse", "NullEnum",
    "PatchedCampaignCodeRequest", "PatchedRewardRuleRequest", "PatchedVanityCodeRequest", "ReferralCounts", "ReversalOutcomeEnum",
    "ReferralItem", "ReferralPage", "ReferralStatsResponse", "RewardPage", "RewardResponse", "RewardRoleEnum",
    "RewardRuleRequest", "RewardRuleResponse", "RewardStatusEnum", "RewardTotal", "RewardTransitionRequest",
    "RewardTransitionTargetEnum", "RewardTriggerEnum", "RuleCodeKindEnum", "RuleRewardCounts",
    "RuleTriggerEnum", "StaffRewardPage", "StaffRewardResponse", "TopReferrer",
}


def _emit(out_dir: Path) -> None:
    for module in ("stapel_referrals._codegen", "stapel_referrals._capabilities"):
        subprocess.run(
            [sys.executable, "-m", module, "--out", str(out_dir)],
            cwd=str(REPO), check=True, capture_output=True,
        )
    subprocess.run(
        [sys.executable, "-m", "stapel_tools.llms_txt", ".", "--out", str(out_dir),
         "--budget", LLMS_TXT_BUDGET],
        cwd=str(REPO), check=True, capture_output=True,
    )


def _schema():
    return json.loads((DOCS / "schema.json").read_text())


def test_artifacts_committed():
    for name in ARTIFACTS + ("capabilities.meta.json", "readme.md"):
        assert (DOCS / name).is_file(), f"missing docs/{name} — run `make contract`"


def test_contract_has_no_drift(tmp_path):
    _emit(tmp_path)
    for name in ARTIFACTS:
        assert (DOCS / name).read_bytes() == (tmp_path / name).read_bytes(), (
            f"docs/{name} drifted — run `make contract` and commit it"
        )


def test_readme_is_generated_and_current():
    result = subprocess.run(
        [sys.executable, "-m", "stapel_tools.readme", ".", "--check"],
        cwd=str(REPO), capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_emission_is_deterministic(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    _emit(a)
    _emit(b)
    for name in ARTIFACTS:
        assert (a / name).read_bytes() == (b / name).read_bytes()


def test_paths_carry_canonical_prefix():
    paths = _schema()["paths"]
    assert paths
    assert all(p.startswith("/referrals/api/v1/") for p in paths)


def test_stable_component_names():
    assert set(_schema()["components"]["schemas"]) == STABLE_COMPONENTS


def test_schema_refs_are_self_contained():
    schema = _schema()
    comps = schema["components"]["schemas"]
    refs = set(re.findall(r'"#/components/schemas/([^"]+)"', json.dumps(schema)))
    assert refs <= set(comps)


def test_every_operation_carries_jwt_security():
    missing = [
        f"{method} {path}"
        for path, ops in _schema()["paths"].items()
        for method, op in ops.items()
        if not any("JWTCookieAuth" in s for s in op.get("security") or [])
    ]
    assert missing == []


@pytest.mark.parametrize("path", ["/referrals/api/v1/me/referrals/", "/referrals/api/v1/me/rewards/",
                                  "/referrals/api/v1/staff/rewards/", "/referrals/api/v1/staff/campaign-codes/"])
def test_lists_declare_anchor_params(path):
    names = {p["name"] for p in _schema()["paths"][path]["get"]["parameters"]}
    assert {"anchor", "limit", "direction"} <= names


def test_flows_document_the_programme():
    flows = {f["id"] for f in json.loads((DOCS / "flows.json").read_text())}
    assert {"referrals.invite", "referrals.claim", "referrals.staff"} <= flows


def test_every_error_key_is_in_the_catalog():
    from stapel_referrals.errors import STAPEL_REFERRALS_ERRORS

    keys = json.dumps(json.loads((DOCS / "errors.json").read_text()))
    assert all(k in keys for k in STAPEL_REFERRALS_ERRORS)
