"""Code text: minting, normalising, vanity validation, share links, masking.

Pure functions — no database. The service layer (``services/codes.py``)
owns the uniqueness retries.
"""
from __future__ import annotations

import logging
import re
import secrets

from .models import CODE_MAX_LENGTH

logger = logging.getLogger(__name__)

#: 31 symbols: digits and capitals without 0/O, 1/I/L (look-alikes on paper).
ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"
CODE_MIN_LENGTH = 6
VANITY_MIN_LENGTH = 4
VANITY_RE = re.compile(r"^[A-Z0-9][A-Z0-9_-]{%d,%d}$" % (VANITY_MIN_LENGTH - 1, CODE_MAX_LENGTH - 1))
#: What an input code may look like before lookup (anything else is unknown).
LOOKUP_RE = re.compile(r"^[A-Z0-9_-]{1,%d}$" % CODE_MAX_LENGTH)


def normalize(raw) -> str:
    """Input code -> stored form: trimmed, upper-case. Never raises."""
    return str(raw or "").strip().upper()


def is_lookup_shaped(code: str) -> bool:
    return bool(LOOKUP_RE.match(code))


def mint(length: int) -> str:
    length = max(CODE_MIN_LENGTH, min(int(length), CODE_MAX_LENGTH))
    return "".join(secrets.choice(ALPHABET) for _ in range(length))


def vanity_problem(code: str, reserved) -> str | None:
    """Why *code* (normalised) cannot be a vanity code, or None."""
    if not VANITY_RE.match(code):
        return "shape"
    if code in {normalize(r) for r in (reserved or ())}:
        return "reserved"
    return None


def share_link(code: str) -> str | None:
    """The share URL from ``SHARE_URL_TEMPLATE``; None when unset or broken."""
    from .conf import referrals_settings

    template = referrals_settings.SHARE_URL_TEMPLATE or ""
    if not template or not code:
        return None
    try:
        return template.format(code=code)
    except (KeyError, IndexError, ValueError):
        logger.warning("SHARE_URL_TEMPLATE %r cannot be formatted", template)
        return None


def mask(text: str) -> str:
    """``alice@example.com`` -> ``a***@e***.com``; ``bob`` -> ``b***``."""
    text = (text or "").strip()
    if not text:
        return ""
    if "@" in text:
        local, _, domain = text.partition("@")
        head, dot, tld = domain.rpartition(".")
        masked_domain = f"{head[:1]}***{dot}{tld}" if dot else f"{domain[:1]}***"
        return f"{local[:1]}***@{masked_domain}"
    return f"{text[:1]}***"
