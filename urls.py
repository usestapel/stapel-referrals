"""Root URLconf for stapel-referrals — v1 canon mount.

The host mounts the module under ``referrals/api/``; this module contributes
the ``v1/`` segment:

    path("referrals/api/", include("stapel_referrals.urls"))   # -> /referrals/api/v1/...
"""
from django.urls import include, path

from stapel_referrals.urls_v1 import GATE_REGISTRY  # noqa: F401  (re-export)

urlpatterns = [
    path("v1/", include("stapel_referrals.urls_v1")),
]
