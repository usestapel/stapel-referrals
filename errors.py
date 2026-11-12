"""i18n error keys of stapel-referrals.

Only ``error.<status>.<slug>`` keys leave this package — human-readable
strings are translations, never literals in responses.
"""
from stapel_core.django.api.errors import register_service_errors

ERR_400_CODE_UNKNOWN = "error.400.code_unknown"
ERR_400_SELF_REFERRAL = "error.400.self_referral"
ERR_400_VANITY_INVALID = "error.400.vanity_code_invalid"
ERR_400_STRATEGY_UNKNOWN = "error.400.reward_strategy_unknown"
ERR_400_STRATEGY_PARAMS_INVALID = "error.400.reward_params_invalid"
ERR_400_RULE_INVALID = "error.400.reward_rule_invalid"
ERR_403_CLAIM_REQUIRES_ACCOUNT = "error.403.claim_requires_account"
ERR_403_VANITY_NOT_ALLOWED = "error.403.vanity_code_not_allowed"
ERR_404_RULE_NOT_FOUND = "error.404.reward_rule_not_found"
ERR_404_REWARD_NOT_FOUND = "error.404.reward_not_found"
ERR_404_CODE_NOT_FOUND = "error.404.referral_code_not_found"
ERR_409_ALREADY_ATTRIBUTED = "error.409.already_attributed"
ERR_409_CODE_EXPIRED = "error.409.code_expired"
ERR_409_CODE_EXHAUSTED = "error.409.code_exhausted"
ERR_409_CODE_TAKEN = "error.409.code_taken"
ERR_409_ATTRIBUTION_WINDOW_CLOSED = "error.409.attribution_window_closed"
ERR_409_CLAIM_WINDOW_CLOSED = "error.409.claim_window_closed"
ERR_409_CLAIM_REFUSED = "error.409.claim_refused"
ERR_409_VANITY_LIMIT = "error.409.vanity_change_limit"
ERR_409_REWARD_TRANSITION_INVALID = "error.409.reward_transition_invalid"
ERR_503_REWARD_PROVIDER_UNAVAILABLE = "error.503.reward_provider_unavailable"

STAPEL_REFERRALS_ERRORS = {
    ERR_400_CODE_UNKNOWN: "This referral code is not valid",
    ERR_400_SELF_REFERRAL: "You cannot use your own referral code",
    ERR_400_VANITY_INVALID: "A code is 4 to 32 letters, digits, '-' or '_'",
    ERR_400_STRATEGY_UNKNOWN: "Unknown reward strategy {strategy}",
    ERR_400_STRATEGY_PARAMS_INVALID: "Reward parameters are invalid: {problem}",
    ERR_400_RULE_INVALID: "The reward rule is invalid: {problem}",
    ERR_403_CLAIM_REQUIRES_ACCOUNT: "Sign in to use a referral code",
    ERR_403_VANITY_NOT_ALLOWED: "Choosing your own code is not available",
    ERR_404_RULE_NOT_FOUND: "Reward rule not found",
    ERR_404_REWARD_NOT_FOUND: "Reward not found",
    ERR_404_CODE_NOT_FOUND: "Referral code not found",
    ERR_409_ALREADY_ATTRIBUTED: "You have already used a referral code",
    ERR_409_CODE_EXPIRED: "This referral code is not active now",
    ERR_409_CODE_EXHAUSTED: "This referral code has been used up",
    ERR_409_CODE_TAKEN: "This code is taken; choose another",
    ERR_409_ATTRIBUTION_WINDOW_CLOSED: "This invitation has expired",
    ERR_409_CLAIM_WINDOW_CLOSED: "Referral codes are for new accounts",
    ERR_409_CLAIM_REFUSED: "This referral code cannot be used from this account",
    ERR_409_VANITY_LIMIT: "You cannot change your code again (at most {max} changes)",
    ERR_409_REWARD_TRANSITION_INVALID: "The reward cannot move from {from} to {to}",
    ERR_503_REWARD_PROVIDER_UNAVAILABLE: "The reward could not be issued now; it will be retried",
}

#: Explicit remediation where the status heuristic would mislead.
STAPEL_REFERRALS_REMEDIATION = {
    ERR_403_CLAIM_REQUIRES_ACCOUNT: "reauthenticate",
    ERR_503_REWARD_PROVIDER_UNAVAILABLE: "wait_and_retry",
}

register_service_errors(STAPEL_REFERRALS_ERRORS, remediation=STAPEL_REFERRALS_REMEDIATION)

__all__ = ["STAPEL_REFERRALS_ERRORS"] + [name for name in dir() if name.startswith("ERR_")]
