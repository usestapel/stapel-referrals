"""Business flows of stapel-referrals (stapel_core.flows).

The Flow objects live here; HTTP steps are attached in ``views.py`` with
``@flow_step``. Literals are the canonical English source texts; i18n keys
are derived (``flow.<id>.title`` / ``.step.<order>.note``).
"""
from stapel_core.flows import Flow

INVITE = Flow(
    "referrals.invite",
    title="Invite a friend",
    description=(
        "A member opens their referral page, gets a personal code minted on "
        "first use and a share link, optionally picks a vanity code, and later "
        "follows the friends who joined and the rewards they earned."
    ),
    actors=["Member (the referrer)"],
)
INVITE.human(order=0, note="The member opens 'Invite friends'")
INVITE.action("referrals.reward.granted", order=5, note="A granted reward is announced for notifications")

CLAIM = Flow(
    "referrals.claim",
    title="Arrive through an invitation",
    description=(
        "A visitor lands on a share link; the client stores the code (first "
        "touch) and, right after sign-up, claims it. The account is attributed "
        "to the referrer once; reward rules fire on sign-up and on qualifying "
        "orders."
    ),
    actors=["Visitor, then the new account (the referee)"],
)
CLAIM.human(order=0, note="The visitor opens a share link; the client keeps the code")
CLAIM.action("user.signed_up", order=3, note="Sign-up fires the signup rules")
CLAIM.action("orders.order.placed", order=4, note="A qualifying order fires the order rules")
CLAIM.action("referrals.attribution.created", order=5, note="The attribution is announced")

STAFF = Flow(
    "referrals.staff",
    title="Run the referral programme",
    description=(
        "Staff define reward rules and campaign codes, watch the ledger and "
        "the statistics, and grant, reject or reverse a reward by hand."
    ),
    actors=["Staff"],
)
STAFF.action("referrals.reward.reversed", order=6, note="A reversal is announced")
