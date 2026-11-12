"""Single-module Django settings shared by the test suite and contract emission.

One copy, so the harness that emits ``docs/schema.json`` and the tests that
prove it can never drift in INSTALLED_APPS or comm config. The production
``REST_FRAMEWORK`` (exception handler, schema class) is used by both: the
error envelope the tests assert is the one a host serves.
"""
from __future__ import annotations

import os
from urllib.parse import urlparse

#: Run the suite against a real database (the row-lock proofs need one):
#: ``STAPEL_TEST_DATABASE_URL=postgres://user:pass@127.0.0.1:55481/db``.
TEST_DATABASE_ENV = "STAPEL_TEST_DATABASE_URL"


def _database() -> dict:
    url = os.environ.get(TEST_DATABASE_ENV, "")
    if not url:
        return {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}
    parts = urlparse(url)
    return {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": parts.path.lstrip("/"),
        "USER": parts.username or "",
        "PASSWORD": parts.password or "",
        "HOST": parts.hostname or "127.0.0.1",
        "PORT": str(parts.port or 5432),
    }


def settings_kwargs(
    *,
    root_urlconf: str = "stapel_referrals.tests.urls",
    contract: bool = False,
) -> dict:
    rest_framework = {
        "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
        "DEFAULT_SCHEMA_CLASS": "stapel_core.django.openapi.schemas.PermissionAwareAutoSchema",
        "EXCEPTION_HANDLER": "stapel_core.django.api.errors.stapel_exception_handler",
    }
    if contract:
        rest_framework.update({
            "DEFAULT_AUTHENTICATION_CLASSES": [
                "stapel_core.django.jwt.authentication.JWTCookieAuthentication",
            ],
            "DEFAULT_PERMISSION_CLASSES": [
                "stapel_core.django.api.permissions.IsServiceRequest",
                "stapel_core.django.api.permissions.IsSuperUser",
            ],
            "DEFAULT_RENDERER_CLASSES": [
                "rest_framework.renderers.JSONRenderer",
                "rest_framework.renderers.BrowsableAPIRenderer",
            ],
        })
    return dict(
        SECRET_KEY="test-secret-key-not-for-production",
        INSTALLED_APPS=[
            "django.contrib.contenttypes",
            "django.contrib.auth",
            "django.contrib.sessions",
            "django.contrib.admin",
            "django.contrib.messages",
            "stapel_core.django.apps.CommonDjangoConfig",
            "stapel_core.django.users",
            "rest_framework",
            "drf_spectacular",
            "stapel_referrals",
        ],
        AUTH_USER_MODEL="users.User",
        DATABASES={"default": _database() if not contract else {
            "ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}},
        DEFAULT_AUTO_FIELD="django.db.models.BigAutoField",
        USE_TZ=True,
        ROOT_URLCONF=root_urlconf,
        CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}},
        STAPEL_BUS_BACKEND="stapel_core.bus.backends.memory.MemoryBus",
        STAPEL_COMM={
            "OUTBOX_ENABLED": False,
            "ACTION_TRANSPORT": "inprocess",
            "VALIDATE_SCHEMAS": True,
        },
        STAPEL_REFERRALS={},
        TEMPLATES=[{
            "BACKEND": "django.template.backends.django.DjangoTemplates",
            "APP_DIRS": True,
            "OPTIONS": {"context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ]},
        }],
        MIDDLEWARE=[
            "django.contrib.sessions.middleware.SessionMiddleware",
            "django.contrib.auth.middleware.AuthenticationMiddleware",
            "django.contrib.messages.middleware.MessageMiddleware",
        ],
        MIGRATION_MODULES={} if os.environ.get(TEST_DATABASE_ENV) else {"users": None},
        REST_FRAMEWORK=rest_framework,
    )


#: Forced on drf-spectacular's singleton so a single-module instance derives
#: the same operation ids as a multi-module aggregate.
CODEGEN_SCHEMA_PATH_PREFIX = "/"


def enum_name_overrides() -> dict:
    """Stable enum component names (stapel-schema-lint SCH004). Called after setup."""
    from stapel_referrals.models import (
        Beneficiary,
        CodeKind,
        ReversalOutcome,
        RewardStatus,
        Role,
        RuleCodeKind,
        Trigger,
        TriggerKind,
    )

    return {
        "CodeKindEnum": CodeKind,
        "RewardStatusEnum": RewardStatus,
        "ReversalOutcomeEnum": ReversalOutcome,
        "RewardRoleEnum": Role,
        "RewardTriggerEnum": TriggerKind,
        "RuleTriggerEnum": Trigger,
        "BeneficiaryEnum": Beneficiary,
        "RuleCodeKindEnum": RuleCodeKind,
        "RewardTransitionTargetEnum": [(s, s) for s in ("granted", "rejected", "reversed")],
    }
