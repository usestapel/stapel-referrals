"""Migrations: from zero to head, and no model change without a migration."""
import pytest
from django.core.management import call_command


@pytest.mark.django_db(transaction=True)
def test_from_zero_and_back():
    call_command("migrate", "referrals", "zero", verbosity=0)
    call_command("migrate", "referrals", verbosity=0)


@pytest.mark.django_db
def test_no_missing_migration():
    call_command("makemigrations", "referrals", "--check", "--dry-run", verbosity=0)
