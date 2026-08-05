"""
UBA §7.2 (Sprint R1) — bulk-recompute MarketPriceIndex across every
GlobalProduct/county pair that has at least one contributing item. Not
wired into an automatic schedule this pass (documented deferral, same as
Maduka Yangu's daily digest in M3) — run manually or from a future Render
cron job, same convention as enrich_liquor_catalog.
"""
from django.core.management.base import BaseCommand

from core.market_price import recompute_market_price_index
from core.models import GlobalProduct, Item


class Command(BaseCommand):
    help = 'Recompute the MarketPriceIndex county-level benchmark for every GlobalProduct.'

    def handle(self, *args, **options):
        barcodes = set(
            Item.objects.filter(business__contribute_price_data=True)
            .exclude(barcode='').values_list('barcode', flat=True)
        )
        updated = 0
        for barcode in barcodes:
            gp = GlobalProduct.objects.filter(barcode=barcode).first()
            if not gp:
                continue
            counties = set(
                Item.objects.filter(barcode=barcode, business__contribute_price_data=True)
                .values_list('business__county_id', flat=True)
            )
            for county_id in counties | {None}:
                from core.models import County
                county = County.objects.filter(pk=county_id).first() if county_id else None
                result = recompute_market_price_index(gp, county=county)
                if result:
                    updated += 1
        self.stdout.write(self.style.SUCCESS(f'Recomputed {updated} MarketPriceIndex row(s).'))
