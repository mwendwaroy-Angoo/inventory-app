from django.core.management.base import BaseCommand

from accounts.models import Business, UserProfile
from core.models import Transaction


class Command(BaseCommand):
    help = (
        "READ-ONLY. The other half of 'could the same thing happen for stock "
        "takes done before your update' (2026-08-25, Roy) — for the Rekebisha "
        "owner-notification fix specifically: nothing was ever lost or hidden "
        "here either. Every Rekebisha correction (invoice_no [ADJ]/[ADJ-NOLOSS]) "
        "has always been fully and correctly recorded in Transaction History — "
        "only the NOTIFICATION to the owner at the time was missing. This lists "
        "every historical correction made by a DELEGATED (non-owner/manager) "
        "staffer, exactly the population that fix now notifies going forward, so "
        "you can review what happened before the fix existed. Changes nothing."
    )

    def add_arguments(self, parser):
        parser.add_argument('--business', type=str, required=True, help='Business name substring.')

    def handle(self, *args, **options):
        businesses = Business.objects.filter(name__icontains=options['business'])
        if not businesses.exists():
            self.stdout.write(self.style.ERROR('No matching business.'))
            return

        for business in businesses:
            owner_manager_ids = set(
                UserProfile.objects.filter(business=business, role__in=['owner', 'manager'])
                .values_list('user_id', flat=True)
            )
            corrections = (
                Transaction.objects.filter(
                    business=business, invoice_no__in=['[ADJ]', '[ADJ-NOLOSS]'],
                )
                .exclude(recorded_by_id__in=owner_manager_ids)
                .select_related('item', 'recorded_by')
                .order_by('-created_at')
            )
            if not corrections.exists():
                self.stdout.write(f"[{business.name}] No delegated-staff Rekebisha corrections found.")
                continue

            self.stdout.write(self.style.WARNING(
                f"\n[{business.name}] {corrections.count()} delegated-staff Rekebisha "
                f"correction(s) never notified at the time:"
            ))
            for txn in corrections:
                who = (txn.recorded_by.get_full_name() or txn.recorded_by.username) if txn.recorded_by else '?'
                when = txn.created_at.strftime('%d %b %Y %H:%M')
                direction = 'Punguzo' if txn.type == 'Wastage' else 'Ongezeko'
                no_loss = ' [SIO HASARA HALISI]' if txn.invoice_no == '[ADJ-NOLOSS]' else ''
                note = f" — \"{txn.recipient}\"" if txn.recipient else ''
                self.stdout.write(
                    f"  {when}  {who:<20}  {txn.item.description if txn.item else '?'}: "
                    f"{direction} {abs(txn.qty):g} {txn.item.unit if txn.item else ''}{no_loss}{note}"
                )
