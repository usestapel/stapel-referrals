"""Fake billing and promotions Functions answering the contracts this module calls.

The payload contracts are owned by stapel-billing (``billing.credit`` /
``billing.debit``, >= 0.25.0) and stapel-promotions (``promotions.codes.*``,
0.1.0). This module ships no copy of their schemas, so the fakes assert the
key sets those schemas allow (``additionalProperties: false``) and answer the
documented shapes, including the parts that matter for retries: billing's
per-wallet idempotency keys (a replay answers the first transaction, the same
key on a different operation is ``idempotency_conflict``) and promotions'
issue key (a replay answers the same codes). The real neighbours are proved
in the seam run, not here.
"""
from __future__ import annotations

NAMES = ("billing.credit", "billing.debit", "promotions.codes.issue", "promotions.codes.revoke")
#: stapel-billing >= 0.25.0 billing.credit: required / allowed keys.
CREDIT_REQUIRED = {"user_id", "credits", "idempotency_key", "source"}
CREDIT_ALLOWED = CREDIT_REQUIRED | {"type", "description", "metadata", "expires_at", "identity"}
#: stapel-billing billing.debit: required / allowed keys.
DEBIT_REQUIRED = {"user_id", "credits", "idempotency_key"}
DEBIT_ALLOWED = DEBIT_REQUIRED | {"metadata", "description", "allow_partial", "type", "identity"}
IDENTITY_ALLOWED = {"is_anonymous", "auth_type", "username"}
#: stapel-promotions 0.1.0 promotions.codes.issue / .revoke: required / allowed keys.
ISSUE_REQUIRED = {"promotion_key"}
ISSUE_ALLOWED = ISSUE_REQUIRED | {"count", "assigned_user_id", "prefix", "usage_limit", "idempotency_key"}
REVOKE_ALLOWED = {"code", "code_id", "reason"}


class FakeProviders:
    def __init__(self):
        self.calls: dict[str, list[dict]] = {name: [] for name in NAMES}
        self.fail: set[str] = set()
        #: billing answers {"ok": false, "reason": ...} instead of crediting.
        self.refuse_credit: str = ""
        #: billing.debit answers this reason instead of debiting.
        self.refuse_debit: str = ""
        #: the answer of the next call to these Functions is lost (the work is done).
        self.lose_answer: set[str] = set()
        #: user_id -> credits (a wallet opens on first contact).
        self.wallets: dict[str, int] = {}
        #: users billing has not met: a call without ``identity`` answers user_unknown.
        self.unmet: set[str] = set()
        #: (user_id, idempotency_key) -> (operation, credits, transaction_id)
        self.keys: dict[tuple[str, str], tuple[str, int, str]] = {}
        #: promotions: idempotency_key -> (request, codes); code -> state
        self.issues: dict[str, tuple[dict, list[str]]] = {}
        self.codes: dict[str, dict] = {}
        self._seq = 0

    def _record(self, name, payload):
        if name == "billing.credit":
            assert CREDIT_REQUIRED <= set(payload) <= CREDIT_ALLOWED, payload
            assert payload["source"] in ("grant", "adjustment") and 1 <= len(payload["idempotency_key"]) <= 255
        elif name == "billing.debit":
            assert DEBIT_REQUIRED <= set(payload) <= DEBIT_ALLOWED, payload
        elif name == "promotions.codes.issue":
            assert ISSUE_REQUIRED <= set(payload) <= ISSUE_ALLOWED, payload
            assert len(payload["promotion_key"]) <= 58 and len(payload.get("assigned_user_id") or "") <= 64
            assert 1 <= len(payload.get("idempotency_key", "x")) <= 128
        elif name == "promotions.codes.revoke":
            assert set(payload) <= REVOKE_ALLOWED and "reason" in payload, payload
            assert ("code" in payload) != ("code_id" in payload) and 1 <= len(payload["reason"]) <= 64
        if name.startswith("billing."):
            assert set(payload.get("identity") or {}) <= IDENTITY_ALLOWED, payload
        self.calls[name].append(dict(payload))
        if name in self.fail:
            raise RuntimeError(f"{name} is down")

    def _answered(self, name, answer):
        if name in self.lose_answer:
            self.lose_answer.discard(name)
            raise TimeoutError(f"{name}: the answer was lost")
        return answer

    def _wallet(self, payload):
        user_id = payload["user_id"]
        if user_id in self.unmet and not payload.get("identity"):
            return None
        self.unmet.discard(user_id)
        self.wallets.setdefault(user_id, 0)
        return user_id

    def credit(self, payload):
        self._record("billing.credit", payload)
        if self.refuse_credit:
            return {"ok": False, "balance": None, "reason": self.refuse_credit}
        user_id = self._wallet(payload)
        if user_id is None:
            return {"ok": False, "balance": None, "reason": "user_unknown"}
        seen = self.keys.get((user_id, payload["idempotency_key"]))
        if seen is not None:
            if seen[:2] != ("credit", payload["credits"]):
                return {"ok": False, "balance": None, "reason": "idempotency_conflict"}
            return self._answered("billing.credit", {
                "ok": True, "balance": self.wallets[user_id], "reason": None, "transaction_id": seen[2]})
        self._seq += 1
        txn = f"txn-{self._seq}"
        self.keys[(user_id, payload["idempotency_key"])] = ("credit", payload["credits"], txn)
        self.wallets[user_id] += payload["credits"]
        return self._answered("billing.credit", {
            "ok": True, "balance": self.wallets[user_id], "reason": None, "transaction_id": txn})

    def debit(self, payload):
        self._record("billing.debit", payload)
        if self.refuse_debit:
            return {"ok": False, "balance": None, "reason": self.refuse_debit}
        user_id = self._wallet(payload)
        if user_id is None:
            return {"ok": False, "balance": None, "reason": "user_unknown"}
        seen = self.keys.get((user_id, payload["idempotency_key"]))
        if seen is not None:  # billing's debit replays whatever the key names
            return self._answered("billing.debit", {
                "ok": True, "balance": self.wallets[user_id], "reason": None, "transaction_id": seen[2]})
        if self.wallets[user_id] < payload["credits"] and not payload.get("allow_partial"):
            return {"ok": False, "balance": self.wallets[user_id], "reason": "insufficient_credits"}
        self._seq += 1
        txn = f"txn-{self._seq}"
        self.keys[(user_id, payload["idempotency_key"])] = ("debit", payload["credits"], txn)
        self.wallets[user_id] -= payload["credits"]
        return self._answered("billing.debit", {
            "ok": True, "balance": self.wallets[user_id], "reason": None, "transaction_id": txn})

    def issue(self, payload):
        self._record("promotions.codes.issue", payload)
        if payload["promotion_key"] == "missing":
            raise LookupError("promotion 'missing' not found")  # what promotions 0.1.0 raises
        key = payload.get("idempotency_key")
        request = {k: v for k, v in payload.items() if k != "idempotency_key"}
        if key and key in self.issues:
            if self.issues[key][0] != request:
                raise ValueError(f"idempotency_key {key!r} was used for a different issue request")
            return self._answered("promotions.codes.issue", {
                "promotion_key": payload["promotion_key"], "codes": list(self.issues[key][1])})
        self._seq += 1
        code = f"GIFT{self._seq:04d}"
        self.codes[code] = {"id": self._seq, "revoked": False, "redeemed": False}
        if key:
            self.issues[key] = (request, [code])
        return self._answered("promotions.codes.issue", {"promotion_key": payload["promotion_key"], "codes": [code]})

    def revoke(self, payload):
        self._record("promotions.codes.revoke", payload)
        state = self.codes.get(str(payload.get("code") or "").upper())
        if state is None:
            raise LookupError("promo code not found")
        already = state["revoked"]
        state["revoked"] = True
        return {"code": payload["code"], "code_id": state["id"], "revoked": True,
                "already_revoked": already, "redeemed": state["redeemed"],
                "revoked_at": "2026-11-12T00:00:00+00:00"}

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
