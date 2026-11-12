"""GDPR for stapel-referrals — the ledger rule: the person goes, the ledger stays.

Erasure of an ``account``:

- the person's codes are deactivated and their text replaced (a vanity code
  can carry a name); ``owner_id`` becomes the fleet pseudonym;
- attributions keep their dates and kinds; ``referee_id`` / ``referrer_id``
  become the pseudonym;
- rewards keep rule, status, value, unit and reference; ``beneficiary_id``
  / ``referrer_id`` become the pseudonym;
- signup markers and device sightings (they only name the person) go.

One operation, :func:`erase_subject`, reached by the in-process provider and
by ``register_gdpr_owner``'s ``gdpr.erasure.requested`` / ``user.deleted``
subscribers alike.
"""
from __future__ import annotations

from stapel_core.gdpr import GDPRProvider


def _reward_row(r) -> dict:
    return {
        "role": r.role,
        "trigger": r.trigger,
        "status": r.status,
        "value": f"{r.value:.2f}",
        "unit": r.unit,
        "grant_ref": r.grant_ref,
        "created_at": r.created_at.isoformat(),
        "granted_at": r.granted_at.isoformat() if r.granted_at else None,
    }


class ReferralsGDPRProvider(GDPRProvider):
    section = "referrals"

    def export(self, user_id) -> dict:
        from .models import Attribution, ReferralCode, Reward

        user_id = str(user_id)
        own = Attribution.objects.select_related("code").filter(referee_id=user_id).first()
        return {
            "codes": [
                {"code": c.code, "kind": c.kind, "primary": c.is_primary, "active": c.active,
                 "created_at": c.created_at.isoformat()}
                for c in ReferralCode.objects.filter(owner_id=user_id)
            ],
            "referred_by": None if own is None else {
                "code": own.code.code, "status": own.status, "attributed_at": own.attributed_at.isoformat(),
            },
            "referrals": [
                {"attributed_at": a.attributed_at.isoformat(), "status": a.status, "code_kind": a.code_kind}
                for a in Attribution.objects.filter(referrer_id=user_id)
            ],
            "rewards": [_reward_row(r) for r in Reward.objects.filter(beneficiary_id=user_id)],
        }

    def delete(self, user_id) -> None:
        erase_subject("account", user_id)

    #: After erasure the rows hold a pseudonym: that IS the anonymisation,
    #: so one operation answers both names.
    anonymize = delete


OWNER = ReferralsGDPRProvider.section
SUBJECT_TYPES = ("account",)


def erase_subject(subject_type, subject_key, workspace_id=None):
    """Erase one account's personal data. Idempotent; counts what it touched."""
    import secrets

    from django.db import transaction
    from stapel_core.gdpr.owners import pseudonymize

    from .models import Attribution, DeviceSighting, ReferralCode, Reward, SignupMarker

    if subject_type != "account" or not subject_key:
        return None
    user_id = str(subject_key)
    alias = pseudonymize(user_id)
    with transaction.atomic():
        codes = 0
        for code in ReferralCode.objects.select_for_update().filter(owner_id=user_id):
            code.code = f"X{secrets.token_hex(12).upper()}"
            code.owner_id = alias
            code.active = False
            code.is_primary = False
            code.label = ""
            code.save()
            codes += 1
        as_referee = Attribution.objects.filter(referee_id=user_id).update(referee_id=alias)
        as_referrer = Attribution.objects.filter(referrer_id=user_id).update(referrer_id=alias)
        rewards = Reward.objects.filter(beneficiary_id=user_id).update(beneficiary_id=alias)
        Reward.objects.filter(referrer_id=user_id).update(referrer_id=alias)
        markers, _ = SignupMarker.objects.filter(user_id=user_id).delete()
        devices, _ = DeviceSighting.objects.filter(user_id=user_id).delete()
    return {
        "codes": codes,
        "attribution_as_referee": as_referee,
        "attributions_as_referrer": as_referrer,
        "rewards": rewards,
        "signup_markers": markers,
        "device_sightings": devices,
    }
