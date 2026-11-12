"""Run the beat passes once: grant due rewards, retry reversals, purge markers."""
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Grant due referral rewards, retry reversals and purge expired markers."

    def handle(self, *args, **options):
        from stapel_referrals import beat

        granted = beat.grant_due_rewards()
        reversed_ = beat.retry_reversals()
        purged = beat.purge_markers()
        self.stdout.write(f"granted={granted} reversed={reversed_} purged={purged}")
