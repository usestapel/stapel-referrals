"""Admin for stapel-referrals.

The ledger is read-only; a reward moves only through the service (actions
below), never a raw field edit, so every move is checked by the FSM and
announced on the bus. Rules are validated by the same service the staff API
uses.
"""
from django import forms
from django.contrib import admin, messages

from .models import Attribution, ReferralCode, Reward, RewardRule, RewardStatus


def _transition_action(to_status: str):
    def action(modeladmin, request, queryset):
        from .services import ReferralsError
        from .services.rewards import staff_transition

        moved = refused = 0
        for reward in queryset:
            try:
                staff_transition(reward.pk, to_status, "admin")
                moved += 1
            except ReferralsError:
                refused += 1
        if moved:
            modeladmin.message_user(request, f"{moved} reward(s) moved to {to_status}.")
        if refused:
            modeladmin.message_user(request, f"{refused} reward(s) cannot move to {to_status}.",
                                    messages.WARNING)

    action.__name__ = f"mark_{to_status}"
    action.short_description = f"Mark selected rewards {to_status}"
    return action


@admin.register(ReferralCode)
class ReferralCodeAdmin(admin.ModelAdmin):
    list_display = ("code", "kind", "owner_id", "is_primary", "active", "attribution_count", "created_at")
    list_filter = ("kind", "active", "vanity")
    search_fields = ("code", "owner_id", "label")
    readonly_fields = ("code", "kind", "owner_id", "is_primary", "vanity", "attribution_count",
                       "created_by", "created_at", "updated_at")


@admin.register(Attribution)
class AttributionAdmin(admin.ModelAdmin):
    list_display = ("referee_id", "referrer_id", "code", "code_kind", "status", "attributed_at")
    list_filter = ("status", "code_kind", "source")
    search_fields = ("referee_id", "referrer_id")
    readonly_fields = [f.name for f in Attribution._meta.fields]

    def has_add_permission(self, request):
        return False


class RewardRuleForm(forms.ModelForm):
    class Meta:
        model = RewardRule
        fields = "__all__"

    def clean(self):
        from .services import ReferralsError
        from .services.rules import clean_values

        cleaned = super().clean()
        try:
            cleaned.update(clean_values(dict(cleaned)))
        except ReferralsError as exc:
            raise forms.ValidationError(f"{exc.key} {exc.params}") from None
        return cleaned


@admin.register(RewardRule)
class RewardRuleAdmin(admin.ModelAdmin):
    form = RewardRuleForm
    list_display = ("name", "active", "trigger", "beneficiary", "strategy", "cap_per_referrer", "budget_count")
    list_filter = ("active", "trigger", "beneficiary")
    search_fields = ("name",)


@admin.register(Reward)
class RewardAdmin(admin.ModelAdmin):
    list_display = ("id", "rule", "role", "beneficiary_id", "status", "value", "unit", "created_at")
    list_filter = ("status", "role", "trigger", "unit")
    search_fields = ("beneficiary_id", "referrer_id", "trigger_id", "grant_ref")
    readonly_fields = [f.name for f in Reward._meta.fields]
    actions = [
        _transition_action(RewardStatus.GRANTED),
        _transition_action(RewardStatus.REJECTED),
        _transition_action(RewardStatus.REVERSED),
    ]

    def has_add_permission(self, request):
        return False
