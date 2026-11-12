"""Fake billing and promotions Functions answering the contracts this module calls.

``billing.credit`` (not shipped yet) is validated against the proposal in
``schemas/asks/``. ``billing.debit`` and ``promotions.codes.issue`` are owned
by their bricks; this module ships no copy of their schemas, so the fakes
assert the key sets those schemas allow (``additionalProperties: false``)
and answer the documented response shapes.
"""
from __future__ import annotations

import json
from pathlib import Path

import jsonschema

ASKS = Path(__file__).resolve().parent.parent / "schemas" / "asks"
NAMES = ("billing.credit", "billing.debit", "promotions.codes.issue", "promotions.codes.revoke")
#: The fields stapel-billing's own billing.debit schema requires (it owns that file).
DEBIT_REQUIRED = {"user_id", "credits", "idempotency_key"}
DEBIT_ALLOWED = DEBIT_REQUIRED | {"metadata", "description", "allow_partial", "type", "identity"}
#: stapel-promotions 0.1.0 promotions.codes.issue: required / allowed keys.
ISSUE_REQUIRED = {"promotion_key"}
ISSUE_ALLOWED = ISSUE_REQUIRED | {"count", "assigned_user_id", "prefix", "usage_limit"}


def _ask(name):
    return json.loads((ASKS / f"{name}.json").read_text())


class FakeProviders:
    def __init__(self):
        self.calls: dict[str, list[dict]] = {name: [] for name in NAMES}
        self.fail: set[str] = set()
        #: billing answers {"ok": false, "reason": ...} instead of crediting.
        self.refuse_credit: str = ""
        self._seq = 0

    def _record(self, name, payload):
        if name == "billing.debit":
            assert DEBIT_REQUIRED <= set(payload) <= DEBIT_ALLOWED, payload
        elif name == "promotions.codes.issue":
            assert ISSUE_REQUIRED <= set(payload) <= ISSUE_ALLOWED, payload
            assert len(payload["promotion_key"]) <= 58 and len(payload.get("assigned_user_id") or "") <= 64
        else:
            jsonschema.validate(payload, _ask(name))
        self.calls[name].append(dict(payload))
        if name in self.fail:
            raise RuntimeError(f"{name} is down")

    def credit(self, payload):
        self._record("billing.credit", payload)
        if self.refuse_credit:
            return {"ok": False, "reason": self.refuse_credit}
        self._seq += 1
        return {"ok": True, "transaction_id": f"txn-{self._seq}", "balance": payload["credits"]}

    def debit(self, payload):
        self._record("billing.debit", payload)
        return {"ok": True, "balance": 0, "reason": None}

    def issue(self, payload):
        self._record("promotions.codes.issue", payload)
        if payload["promotion_key"] == "missing":
            raise LookupError("promotion 'missing' not found")  # what promotions 0.1.0 raises
        self._seq += 1
        return {"promotion_key": payload["promotion_key"], "codes": [f"GIFT{self._seq:04d}"]}

    def revoke(self, payload):
        self._record("promotions.codes.revoke", payload)
        return {"revoked": True, "redeemed": False}

    def install(self):
        from stapel_core.comm.registry import function_registry

        for name, fn in zip(NAMES, (self.credit, self.debit, self.issue, self.revoke)):
            function_registry.register(name, fn)

    def uninstall(self):
        from stapel_core.comm.registry import function_registry

        with function_registry._lock:
            for name in NAMES:
                function_registry._providers.pop(name, None)
                function_registry._schemas.pop(name, None)
