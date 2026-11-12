"""Service layer of stapel-referrals: codes, attribution, rewards, stats.

Views, comm handlers and the beat call these; nothing here knows about
HTTP. A refusal is a :class:`ReferralsError` carrying the status, the error
key and its params — the view layer renders it as the fleet error envelope.
"""
from __future__ import annotations


class ReferralsError(Exception):
    """A domain refusal with its HTTP status and i18n error key."""

    def __init__(self, status: int, key: str, params: dict | None = None):
        self.status = status
        self.key = key
        self.params = params or {}
        super().__init__(key)


__all__ = ["ReferralsError"]
