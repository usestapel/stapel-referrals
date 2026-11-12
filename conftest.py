def pytest_configure(config):
    from django.conf import settings

    if not settings.configured:
        from stapel_referrals._codegen_settings import settings_kwargs

        settings.configure(**settings_kwargs())
        import django

        django.setup()

        from stapel_core.comm.schemas import autoload_schemas

        autoload_schemas()


import pytest  # noqa: E402
from stapel_core.testing import emit_outside_atomic_gate  # noqa: E402,F401


@pytest.fixture
def api_client():
    from rest_framework.test import APIClient

    return APIClient()


def make_user(username, **extra):
    from django.contrib.auth import get_user_model

    extra.setdefault("email", f"{username}@example.com")
    return get_user_model().objects.create_user(username=username, password="x", **extra)


@pytest.fixture
def referrer(db):
    return make_user("alice")


@pytest.fixture
def referee(db):
    return make_user("bob")


@pytest.fixture
def guest(db):
    return make_user("guest-1", is_anonymous=True, email="")


@pytest.fixture
def staff(db):
    return make_user("staff", is_staff=True)


@pytest.fixture
def client_for():
    def _client(u=None):
        from rest_framework.test import APIClient

        c = APIClient()
        if u is not None:
            c.force_authenticate(user=u)
        return c

    return _client


@pytest.fixture
def providers():
    """Fake billing / promotions Functions, installed per test."""
    from stapel_referrals.tests.fakes import FakeProviders

    fake = FakeProviders()
    fake.install()
    try:
        yield fake
    finally:
        fake.uninstall()


@pytest.fixture
def captured_events():
    """Collect referrals emits delivered in-process."""
    from stapel_core.comm import action_registry, subscribe_action

    from stapel_referrals.services.events import EMITS

    collected = []

    def _handler(event):
        collected.append(event)

    for name in EMITS:
        subscribe_action(name, _handler)
    try:
        yield collected
    finally:
        for name in EMITS:
            handlers = action_registry._subscribers.get(name, [])
            if _handler in handlers:
                handlers.remove(_handler)
