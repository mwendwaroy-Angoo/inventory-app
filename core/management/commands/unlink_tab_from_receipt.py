from django.core.management.base import BaseCommand
from django.db import transaction as db_transaction

from accounts.models import Business
from core.models import BarTab, Receipt


class Command(BaseCommand):
    help = (
        "Correction tool for the 2026-08-16 shared-receipt name-merge bug "
        "(see resolve_master_receipt/_sync_master_receipt_customer_name's own "
        "docstrings) — that fix stops FUTURE merges but does nothing for a "
        "receipt already wrongly linked, like Receipt #705 (Monsoon Inn), "
        "confirmed via diagnose_receipt to be tied to two different Customer "
        "records.\n\n"
        "Detaches one BarTab from a receipt's meta.linked_tab_ids and gives "
        "it a brand-new, standalone receipt of its own — built from that "
        "tab's real entries — so its existing PIN/wall-QR lookup keeps "
        "working correctly (it resolves the tab by its own tab_pin, then "
        "finds whichever receipt currently claims it — see "
        "_resolve_tab_public_url). The tab it stays linked to (the "
        "receipt's own meta.tab_id) and every OTHER still-linked tab are "
        "completely untouched — this only detaches the one tab you name.\n\n"
        "Refuses to touch a receipt's own primary tab (meta.tab_id) — that's "
        "the tab the receipt was genuinely issued for; only an ADDED tab "
        "(one appearing in linked_tab_ids) can be a wrongly-merged addition, "
        "and only that kind is supported here.\n\n"
        "Defaults to a DRY RUN (prints what would happen, changes nothing). "
        "Pass no --dry-run flag to actually apply it."
    )

    def add_arguments(self, parser):
        parser.add_argument('--business', type=str, required=True, help='Business name substring (must match exactly one).')
        parser.add_argument('--receipt', type=int, help='Receipt number (the #N shown on the receipt page).')
        parser.add_argument('--token', type=str, help='Receipt token (from the /r/<token>/ URL) instead of --receipt.')
        parser.add_argument('--tab', type=int, required=True, help='BarTab id to detach (must be in the receipt\'s linked_tab_ids).')
        parser.add_argument('--dry-run', action='store_true', help='Preview only — writes nothing. Omit this flag to actually apply.')

    def handle(self, *args, **options):
        businesses = Business.objects.filter(name__icontains=options['business'])
        count = businesses.count()
        if count == 0:
            self.stdout.write(self.style.ERROR('No matching business.'))
            return
        if count > 1:
            self.stdout.write(self.style.ERROR(
                f"Ambiguous — {count} businesses match {options['business']!r}: "
                + ', '.join(b.name for b in businesses) + '. Be more specific.'
            ))
            return
        business = businesses.first()

        if not options['receipt'] and not options['token']:
            self.stdout.write(self.style.ERROR('Pass --receipt=N or --token=<token>.'))
            return

        qs = Receipt.objects.filter(business=business)
        qs = qs.filter(token=options['token']) if options['token'] else qs.filter(receipt_number=options['receipt'])
        receipt = qs.first()
        if not receipt:
            self.stdout.write(self.style.ERROR('No matching receipt.'))
            return

        tab_id = options['tab']
        tab = BarTab.objects.filter(id=tab_id, business=business).first()
        if not tab:
            self.stdout.write(self.style.ERROR(f'No BarTab #{tab_id} in this business.'))
            return

        linked = list(receipt.meta.get('linked_tab_ids') or [])
        primary_tab_id = receipt.meta.get('tab_id')

        if tab_id == primary_tab_id:
            self.stdout.write(self.style.ERROR(
                f"tab#{tab_id} is this receipt's OWN primary tab (meta.tab_id) "
                "— not a wrongly-merged addition. Unlinking it would orphan "
                "the receipt itself; not supported by this tool."
            ))
            return

        if tab_id not in linked:
            self.stdout.write(self.style.ERROR(
                f"tab#{tab_id} is not in this receipt's linked_tab_ids "
                f"({linked}) — nothing to unlink."
            ))
            return

        entries = list(tab.entries.all().select_related('transaction__item'))
        lines = [
            {'name': e.description, 'qty': 1, 'subtotal': float(e.amount)}
            for e in entries
        ]
        if tab.status == 'OPEN':
            new_payment_method = 'tab'
        else:
            paid_methods = [e.payment_method for e in entries if e.payment_method]
            new_payment_method = max(set(paid_methods), key=paid_methods.count) if paid_methods else 'cash'

        self.stdout.write(self.style.WARNING(
            f"\n=== [{business.name}] Unlink tab#{tab_id} ({tab.customer_name!r}) "
            f"from Receipt #{receipt.receipt_number} (token={receipt.token}) ==="
        ))
        self.stdout.write(f"{len(entries)} entr(y/ies) will move to a NEW standalone receipt:")
        for line in lines:
            self.stdout.write(f"  {line['name']} — KES {line['subtotal']}")
        self.stdout.write(
            f"New receipt: payment_method={new_payment_method!r} "
            f"customer_name={tab.customer_name!r} source={tab.source!r}"
        )
        self.stdout.write(
            f"tab#{tab_id}'s PIN ({tab.tab_pin or '—'}) keeps working — it will "
            "resolve to the new receipt automatically."
        )

        if options['dry_run']:
            self.stdout.write(self.style.WARNING('\n[DRY RUN] Nothing written. Re-run without --dry-run to apply.'))
            return

        with db_transaction.atomic():
            linked.remove(tab_id)
            receipt.meta['linked_tab_ids'] = linked
            receipt.save(update_fields=['meta'])

            new_receipt = Receipt.issue(
                business=business,
                lines=lines,
                payment_method=new_payment_method,
                customer_name=tab.customer_name,
                source=('kitchen' if tab.source == 'kitchen' else ''),
                meta={'tab_id': tab.id},
            )

        self.stdout.write(self.style.SUCCESS(
            f"\n✓ tab#{tab_id} unlinked from Receipt #{receipt.receipt_number}. "
            f"New standalone Receipt #{new_receipt.receipt_number} "
            f"(token={new_receipt.token}) created for it."
        ))
