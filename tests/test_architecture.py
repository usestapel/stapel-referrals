"""Referrals never imports a sibling module — every neighbour is a comm name."""
import ast
from pathlib import Path

PACKAGE = Path(__file__).resolve().parent.parent
FORBIDDEN = ("stapel_auth", "stapel_billing", "stapel_orders", "stapel_promotions", "stapel_shop",
             "stapel_listings", "stapel_notifications", "stapel_analytics", "stapel_realtime",
             "stapel_classified", "stapel_currencies", "stapel_reservations")


def _imports(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            yield node.module


def _sources():
    for path in PACKAGE.rglob("*.py"):
        rel = path.relative_to(PACKAGE).parts
        if rel[0] in ("tests", "build", "dist", ".venv") or ".egg-info" in str(path):
            continue
        yield path


def test_no_sibling_import_anywhere():
    offenders = [
        (str(path.relative_to(PACKAGE)), name)
        for path in _sources()
        for name in _imports(path)
        if name.split(".")[0] in FORBIDDEN
    ]
    assert offenders == []


def test_the_scan_sees_the_package():
    names = {p.name for p in _sources()}
    assert {"models.py", "strategies.py", "views.py", "rewards.py", "attribution.py"} <= names


def test_the_rule_fires_on_a_sibling_import(tmp_path):
    bad = tmp_path / "bad.py"
    bad.write_text("from stapel_billing.services import credit\n")
    assert list(_imports(bad)) == ["stapel_billing.services"]


from .siblings import requires  # noqa: E402


@requires("stapel_tools")
def test_contract_tooling_is_installed():
    """tests/test_contract.py drives stapel_tools (codegen, llms.txt, README) as subprocesses."""
    from stapel_tools.capabilities import run_capabilities_cli

    assert callable(run_capabilities_cli)
