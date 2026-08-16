from django.core.management.base import BaseCommand

from accounts.models import Business
from core.models import BarTab, Receipt


class Command(BaseCommand):
    help = (
        "READ-ONLY. Diagnostic for the 2026-08-16 live report (Roy, Monsoon Inn) — "
        "a customer's live receipt showed an item they never ordered ('Chrome Gin'), "
        "already struck through as paid. Root cause under investigation: "
        "resolve_master_receipt() (core/tab_receipts.py) links a NEW tab into an "
        "EXISTING receipt by bare customer-name string match (Priority 3's fallback "
        "when tab.customer_id is None, and all of Priority 4) with no check that the "
        "two are actually the same physical person — two different real customers "
        "who happen to share a first name can be silently merged onto one shared "
        "receipt/PIN. This command dumps a receipt's real underlying data (its own "
        "static lines snapshot, every BarTab linked to it via meta.tab_id/"
        "linked_tab_ids, and each tab's own entries) so the actual cause can be "
        "confirmed from real facts instead of guessed at. Changes NOTHING."
    )

    def add_arguments(self, parser):
        parser.add_argument('--business', type=str, required=True, help='Business name substring.')
        parser.add_argument('--receipt', type=int, help='Receipt number (the #N shown on the receipt page).')
        parser.add_argument('--token', type=str, help='Receipt token (from the /r/<token>/ URL) instead of --receipt.')

    def handle(self, *args, **options):
        businesses = Business.objects.filter(name__icontains=options['business'])
        if not businesses.exists():
            self.stdout.write(self.style.ERROR('No matching business.'))
            return

        if not options['receipt'] and not options['token']:
            self.stdout.write(self.style.ERROR('Pass --receipt=N or --token=<token>.'))
            return

        for business in businesses:
            qs = Receipt.objects.filter(business=business)
            if options['token']:
                qs = qs.filter(token=options['token'])
            else:
                qs = qs.filter(receipt_number=options['receipt'])
            receipt = qs.first()
            if not receipt:
                self.stdout.write(f"[{business.name}] No matching receipt.")
                continue
            self._diagnose(business, receipt)

    def _diagnose(self, business, receipt):
        from core.receipt_views import _receipt_all_tab_ids

        self.stdout.write(self.style.WARNING(
            f"\n=== [{business.name}] Receipt #{receipt.receipt_number} "
            f"(token={receipt.token}) ==="
        ))
        self.stdout.write(
            f"customer_name={receipt.customer_name!r} payment_method={receipt.payment_method!r} "
            f"total=KES {receipt.total} source={receipt.source!r} created_at={receipt.created_at}"
        )
        self.stdout.write(f"meta: {receipt.meta}")

        self.stdout.write("\n-- Static lines snapshot (receipt.lines, taken at issue time) --")
        for line in (receipt.lines or []):
            self.stdout.write(f"  {line}")

        tab_ids = _receipt_all_tab_ids(receipt)
        self.stdout.write(f"\n-- Linked tab ids (meta.tab_id + meta.linked_tab_ids): {tab_ids} --")

        seen_customer_ids = set()
        seen_customer_names = set()
        for tid in tab_ids:
            tab = BarTab.objects.filter(id=tid, business=business).first()
            if not tab:
                self.stdout.write(self.style.ERROR(f"  tab#{tid} — NOT FOUND (deleted or wrong business)"))
                continue
            seen_customer_ids.add(tab.customer_id)
            seen_customer_names.add(tab.customer_name.strip().lower())
            self.stdout.write(
                f"\n  tab#{tab.id} name={tab.customer_name!r} customer_id={tab.customer_id} "
                f"status={tab.status} source={tab.source} pin={tab.tab_pin} "
                f"opened_at={tab.opened_at} settled_at={tab.settled_at} "
                f"served_by={tab.served_by.username if tab.served_by_id else tab.server_name!r}"
            )
            for e in tab.entries.all().select_related('transaction__item').order_by('id'):
                txn = e.transaction
                self.stdout.write(
                    f"    entry#{e.id} {e.description!r} KES {e.amount} is_paid={e.is_paid} "
                    f"paid_at={e.paid_at} payment_method={e.payment_method!r} "
                    f"| txn#{txn.id if txn else '?'} item={txn.item.description if txn and txn.item else '?'} "
                    f"txn.date={txn.date if txn else '?'} txn.created_at={txn.created_at if txn else '?'}"
                )

        if len(seen_customer_ids - {None}) > 1:
            self.stdout.write(self.style.ERROR(
                f"\n⚠️  LINKED TABS POINT TO DIFFERENT Customer records: {seen_customer_ids - {None}} "
                "— these are almost certainly two different real people who happen to "
                "share a name, wrongly merged onto one shared receipt."
            ))
        elif len(seen_customer_names) > 1:
            self.stdout.write(self.style.ERROR(
                f"\n⚠️  LINKED TABS HAVE DIFFERENT customer_name STRINGS: {seen_customer_names} "
                "— should never happen given the iexact match resolve_master_receipt() "
                "uses; investigate further."
            ))
        elif len(tab_ids) > 1:
            self.stdout.write(self.style.WARNING(
                f"\nMore than one tab is linked to this one receipt, all under the same "
                f"name ({seen_customer_names}). This is either genuinely one customer "
                "with tabs opened at different times/counters, or two different real "
                "customers who share that name and were merged by resolve_master_"
                "receipt()'s bare name-string match — cannot be told apart from this "
                "data alone. Check with staff who served each entry, or compare phone "
                "numbers if any were recorded."
            ))

        other_qs = BarTab.objects.filter(
            business=business,
        ).exclude(id__in=tab_ids)
        matching_name = [
            t for t in other_qs
            if t.customer_name.strip().lower() in seen_customer_names
        ]
        if matching_name:
            self.stdout.write(self.style.WARNING(
                f"\n-- {len(matching_name)} OTHER tab(s) in this business share the same "
                "name but are NOT linked to this receipt (for context) --"
            ))
            for t in matching_name:
                self.stdout.write(
                    f"  tab#{t.id} customer_id={t.customer_id} status={t.status} "
                    f"opened_at={t.opened_at}"
                )
