"""Every response body the committed schema declares is the body the views send.

The operation list is DERIVED from ``docs/schema.json``: an operation with a
declared JSON 2xx body and no recipe here fails loudly. Every body is
validated against its declared schema (OAS ``nullable`` honoured), and each
read is driven in a populated and an empty state.
"""
import copy
import json
from pathlib import Path

import jsonschema
import pytest

from conftest import make_user

from .helpers import claim, my_code, rule, signed_up

REPO = Path(__file__).resolve().parent.parent
SCHEMA = json.loads((REPO / "docs" / "schema.json").read_text())
V1 = "/referrals/api/v1"

pytestmark = pytest.mark.django_db


def _json_schema(node):
    if isinstance(node, list):
        return [_json_schema(item) for item in node]
    if not isinstance(node, dict):
        return node
    rebuilt = {k: _json_schema(v) for k, v in node.items() if k != "nullable"}
    if node.get("nullable"):
        return {"anyOf": [rebuilt, {"type": "null"}]}
    return rebuilt


def _validator(body_schema):
    root = copy.deepcopy(body_schema)
    root["components"] = copy.deepcopy(SCHEMA["components"])
    return jsonschema.Draft202012Validator(_json_schema(root))


def _operations():
    ops = []
    for path, methods in SCHEMA["paths"].items():
        for method, op in methods.items():
            for code, response in op.get("responses", {}).items():
                body = response.get("content", {}).get("application/json", {}).get("schema")
                if body is not None and code.startswith("2"):
                    ops.append((method.upper(), path, int(code), body))
    return sorted(ops, key=lambda o: (o[1], o[0], o[2]))


OPERATIONS = _operations()


def _url(path, **params):
    for name, value in params.items():
        path = path.replace("{%s}" % name, str(value))
    assert "{" not in path, f"unfilled path parameter in {path}"
    return path


def _friends(env, n):
    rule(beneficiary="both", hold_days=0)
    for i in range(n):
        friend = make_user(f"wire-{i}", email=f"wire{i}@mail.example")
        claim(friend, my_code(env.user))
        signed_up(friend)


def r_me(env):
    empty = env.client.get(V1 + "/me/")
    _friends(env, 1)
    return [env.client.get(V1 + "/me/")], [empty]


def r_me_code(env):
    from django.test import override_settings

    _friends(env, 1)
    with override_settings(STAPEL_REFERRALS={"VANITY_CODES": "users"}):
        return [env.client.patch(V1 + "/me/code/", {"code": "wire-code"}, format="json")], []


def r_referrals(env):
    empty = env.client.get(V1 + "/me/referrals/")
    _friends(env, 3)
    return [env.client.get(V1 + "/me/referrals/?limit=2")], [empty]


def r_rewards(env):
    empty = env.client.get(V1 + "/me/rewards/")
    _friends(env, 2)
    return [env.client.get(V1 + "/me/rewards/")], [empty]


def r_claim(code):
    def recipe(env):
        friend = env.client_for(make_user("claimer"))
        first = friend.post(V1 + "/claim/", {"code": my_code(env.user)}, format="json")
        if code == 201:
            return [first], []
        return [friend.post(V1 + "/claim/", {"code": my_code(env.user)}, format="json")], []
    return recipe


def r_lookup(env):
    return [env.client_for().get(_url(V1 + "/codes/{code}/", code=my_code(env.user)))], []


def r_rules_list(env):
    empty = env.staff.get(V1 + "/staff/rules/")
    rule(min_order_total="10.00", currency="USD", trigger="first_order")
    return [env.staff.get(V1 + "/staff/rules/")], [empty]


RULE_BODY = {"name": "Wire", "trigger": "signup", "strategy": "noop", "referrer_params": {"value": "1"},
             "referee_params": {}}


def r_rules_create(env):
    return [env.staff.post(V1 + "/staff/rules/", RULE_BODY, format="json")], []


def r_rule_get(env):
    made = rule()
    return [env.staff.get(_url(V1 + "/staff/rules/{rule_id}/", rule_id=made.pk))], []


def r_rule_patch(env):
    made = rule()
    return [env.staff.patch(_url(V1 + "/staff/rules/{rule_id}/", rule_id=made.pk), {"active": False},
                            format="json")], []


def r_campaigns_list(env):
    empty = env.staff.get(V1 + "/staff/campaign-codes/")
    env.staff.post(V1 + "/staff/campaign-codes/", {"code": "wire-1", "ends_at": "2030-01-01T00:00:00Z"},
                   format="json")
    return [env.staff.get(V1 + "/staff/campaign-codes/")], [empty]


def r_campaigns_create(env):
    return [env.staff.post(V1 + "/staff/campaign-codes/", {"label": "Wire", "max_attributions": 5},
                           format="json")], []


def r_campaign_patch(env):
    made = env.staff.post(V1 + "/staff/campaign-codes/", {"label": "Wire"}, format="json").json()
    return [env.staff.patch(_url(V1 + "/staff/campaign-codes/{code_id}/", code_id=made["id"]),
                            {"active": False}, format="json")], []


def r_staff_rewards(env):
    empty = env.staff.get(V1 + "/staff/rewards/")
    _friends(env, 1)
    return [env.staff.get(V1 + "/staff/rewards/")], [empty]


def r_transition(env):
    from stapel_referrals.models import Reward

    _friends(env, 1)
    reward = Reward.objects.first()
    return [env.staff.post(_url(V1 + "/staff/rewards/{reward_id}/transition/", reward_id=reward.pk),
                           {"to": "reversed", "reason": "wire"}, format="json")], []


def r_stats(env):
    empty = env.staff.get(V1 + "/staff/stats/")
    _friends(env, 1)
    return [env.staff.get(V1 + "/staff/stats/")], [empty]


RECIPES = {
    ("GET", V1 + "/me/", 200): r_me,
    ("PATCH", V1 + "/me/code/", 200): r_me_code,
    ("GET", V1 + "/me/referrals/", 200): r_referrals,
    ("GET", V1 + "/me/rewards/", 200): r_rewards,
    ("POST", V1 + "/claim/", 201): r_claim(201),
    ("POST", V1 + "/claim/", 200): r_claim(200),
    ("GET", V1 + "/codes/{code}/", 200): r_lookup,
    ("GET", V1 + "/staff/rules/", 200): r_rules_list,
    ("POST", V1 + "/staff/rules/", 201): r_rules_create,
    ("GET", V1 + "/staff/rules/{rule_id}/", 200): r_rule_get,
    ("PATCH", V1 + "/staff/rules/{rule_id}/", 200): r_rule_patch,
    ("GET", V1 + "/staff/campaign-codes/", 200): r_campaigns_list,
    ("POST", V1 + "/staff/campaign-codes/", 201): r_campaigns_create,
    ("PATCH", V1 + "/staff/campaign-codes/{code_id}/", 200): r_campaign_patch,
    ("GET", V1 + "/staff/rewards/", 200): r_staff_rewards,
    ("POST", V1 + "/staff/rewards/{reward_id}/transition/", 200): r_transition,
    ("GET", V1 + "/staff/stats/", 200): r_stats,
}


class Env:
    def __init__(self, client, client_for, user, staff):
        self.client, self.client_for, self.user = client, client_for, user
        self.staff = client_for(staff)


def test_every_declared_operation_has_a_recipe():
    declared = {(m, p, c) for m, p, c, _ in OPERATIONS}
    assert declared == set(RECIPES), {
        "missing": sorted(declared - set(RECIPES)),
        "stale": sorted(set(RECIPES) - declared),
    }


@pytest.mark.parametrize("method,path,code,body_schema", OPERATIONS,
                         ids=[f"{m} {p} {c}" for m, p, c, _ in OPERATIONS])
def test_wire_matches_the_declared_body(method, path, code, body_schema, client_for, referrer, staff):
    env = Env(client_for(referrer), client_for, referrer, staff)
    populated, empty = RECIPES[(method, path, code)](env)
    validator = _validator(body_schema)
    for resp in populated + empty:
        assert resp.status_code == code, (method, path, resp.status_code, resp.content)
        errors = sorted(validator.iter_errors(resp.json()), key=str)
        assert not errors, [e.message for e in errors[:5]]
    for resp in populated:
        data = resp.json()
        if isinstance(data, list):
            assert data, f"{method} {path}: populated list came back empty"
        for key in ("items", "rewards_total", "top_referrers", "granted_value"):
            if isinstance(data, dict) and key in data:
                assert data[key], f"{method} {path}: populated '{key}' came back empty"
    for resp in empty:
        data = resp.json()
        if isinstance(data, list):
            assert not data
        for key in ("items", "rewards_total", "top_referrers"):
            if isinstance(data, dict) and key in data:
                assert not data[key], f"{method} {path}: the empty state is not empty"


def test_the_gate_is_not_blind(client_for, referrer):
    """Inversion: a body validated against a wrong schema must fail."""
    body = client_for(referrer).get(f"{V1}/me/").json()
    schema = SCHEMA["paths"][V1 + "/me/"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
    assert not list(_validator(schema).iter_errors(body))
    assert list(_validator(schema).iter_errors(dict(body, counts={"referrals": "many"})))
