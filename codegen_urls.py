"""Canonical-prefix URLconf for contract emission: the documented host mount."""
from django.urls import include, path

urlpatterns = [
    path("referrals/api/", include("stapel_referrals.urls")),
]
