import re

from django.core.management.base import BaseCommand

from accounts.models import Business
from core.models import BarTab, BarTabEntry, Notification


class Command(BaseCommand):
    help = (
        "READ-ONLY. Diagnostic for the 2026-08-25 'revert-to-tab landed in the "
        "wrong tabs drawer' bug (see Transaction.revert_direct_sale_to_tab_locked()'s "
        "own docstring for the full mechanism): before that fix, EVERY '↩️ Tengua->Tab' "
        "correction created its BarTab with `source` derived from the ITEM's own "
        "station (bar vs kitchen), regardless of which board's drawer the staffer was "
        "actually using — so a revert done from Quick Sell's own tabs drawer for a bar "
        "item landed as source='bar', invisible from Quick Sell's own drawer "
        "(?ctx=qs only ever shows source='qs') even though it's exactly the drawer the "
        "correcting staffer needs to see it in to eventually 'Geuza Deni' it.\n\n"
        "The fix (going forward) is not retroactive — any BarTab actually CREATED by "
        "this mechanism before the fix shipped still carries whatever source it was "
        "given at the time, permanently, until corrected by hand. There is no explicit "
        "marker column identifying 'this tab was created via a revert' — this command "
        "reconstructs candidates from the correction's own notification trail (the "
        "'↩️ {who} amerejesha ... kwenye tab ya {customer}' message every revert fires, "
        "see _notify_direct_correction's caller in revert_direct_sale_to_tab) and shows "
        "everything a human needs to decide whether — and to which station — each one "
        "should be corrected via fix_tab_station. Changes NOTHING."
    )

    NOTE_RE = re.compile(
        r'↩️ (?P<who>.+?) amerejesha "(?P<item>.+?)" \(iliyouzwa na (?P<seller>.+?), '
        r'tarehe (?P<sale_when>.+?)\) kwenye tab ya (?P<customer>.+?) — '
        r'KES (?P<owed>[\d,]+) bado inadaiwa'
    )

    def add_arguments(self, parser):
        parser.add_argument('--business', type=str, required=True, help='Business name substring.')

    def handle(self, *args, **options):
        businesses = Business.objects.filter(name__icontains=options['business'])
        if not businesses.exists():
            self.stdout.write(self.style.ERROR('No matching business.'))
            return

        for business in businesses:
            notes = (
                Notification.objects
                .filter(user__userprofile__business=business, message__contains='amerejesha')
                .filter(message__contains='kwenye tab ya')
                .order_by('created_at').distinct()
            )

            if not notes.exists():
                self.stdout.write(f"[{business.name}] No revert-to-tab corrections found — nothing to review.")
                continue

            self.stdout.write(self.style.WARNING(f"\n[{business.name}] {notes.count()} revert(s) found:"))
            seen_msgs = set()
            for note in notes:
                if note.message in seen_msgs:
                    continue  # same correction fanned out to several recipients
                seen_msgs.add(note.message)

                m = self.NOTE_RE.search(note.message)
                if not m:
                    self.stdout.write(f"  (unparsed notification #{note.id}, {note.created_at:%d %b %Y %H:%M}): {note.message}")
                    continue

                customer = m.group('customer').strip()
                item = m.group('item').strip()
                when = note.created_at.strftime('%d %b %Y %H:%M')

                candidate_tabs = BarTab.objects.filter(
                    business=business, customer_name__iexact=customer,
                )
                if not candidate_tabs.exists():
                    self.stdout.write(
                        f"  tarehe {when}: \"{item}\" -> tab ya \"{customer}\" — "
                        f"HAKUNA tab inayolingana na jina hilo sasa (labda tayari imeunganishwa/imefungwa)."
                    )
                    continue

                for tab in candidate_tabs:
                    matching_entry = BarTabEntry.objects.filter(
                        tab=tab, description=item,
                    ).select_related('transaction').first()
                    entry_note = f", entry: \"{matching_entry.description}\" KES {matching_entry.amount}" if matching_entry else " (hakuna entry inayolingana na kipengele hicho — angalia kwa makini)"
                    self.stdout.write(
                        f"  tarehe {when}: \"{item}\" na {m.group('seller')} (iliyouzwa) -> "
                        f"tab#{tab.id} \"{tab.customer_name}\" [source SASA HIVI = '{tab.source}', "
                        f"status={tab.status}]{entry_note}"
                    )
            self.stdout.write(
                "  -> Ikiwa `source` iliyoonyeshwa hapo juu si sahihi (mfano: kupelekwa "
                "kwenye Bar Board wakati marekebisho yalifanywa Quick Sell), tumia:\n"
                "     python manage.py fix_tab_station --business=\"...\" --tab-id=<ID> --station=qs"
            )
