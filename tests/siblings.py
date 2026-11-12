"""Sibling modules this test suite reaches for, and the one rule about them.

1. Every sibling the suite touches is **declared** in the ``test`` extra of
   ``pyproject.toml`` (``pip install -e ".[test]"``). ``stapel-sibling-lint``
   (SIB001-003) fails the build when a declaration is missing.
2. Reaching for one goes through :func:`requires`, never a bare import at
   module scope, so a contributor without the extra gets a named skip instead
   of a collection error.
3. CI sets ``STAPEL_TEST_STRICT_SIBLINGS=1``. In strict mode a missing sibling
   **fails** instead of skipping — on CI the extra is installed, so a skip
   there means the install step did not do what the workflow says it does.
"""
from __future__ import annotations

import os
from importlib.util import find_spec

import pytest

#: CI sets this. See rule 3 above.
STRICT = os.environ.get("STAPEL_TEST_STRICT_SIBLINGS", "") == "1"


def installed(module: str) -> bool:
    """Is ``module`` importable here? Never raises, never imports it."""
    try:
        return find_spec(module) is not None
    except (ImportError, ValueError):  # pragma: no cover - defensive
        return False


def requires(*modules: str):
    """Decorator: this test needs every sibling module named."""
    missing = [m for m in modules if not installed(m)]
    if not missing:
        return lambda func: func

    names = ", ".join(m.replace("_", "-") for m in missing)
    message = (
        f"{names} not installed. Declared in this package's `test` extra — "
        f'install it with `pip install -e ".[test]"`.'
    )

    if not STRICT:
        return pytest.mark.skip(reason=message)

    def _decorator(func):
        # Deliberately NOT functools.wraps: pytest reads the wrapped signature
        # and would build fixtures that themselves need the missing module.
        def _missing_sibling(*_args, **_kwargs):
            pytest.fail(
                f"{message} STAPEL_TEST_STRICT_SIBLINGS=1 is set, so this is a "
                f"failure rather than a skip: on CI the extra is installed, "
                f"and a skip here would mean it silently was not."
            )

        _missing_sibling.__name__ = func.__name__
        _missing_sibling.__doc__ = func.__doc__
        return _missing_sibling

    return _decorator


__all__ = ["STRICT", "installed", "requires"]
