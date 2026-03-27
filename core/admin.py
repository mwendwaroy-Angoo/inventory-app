from django.contrib import admin
from .models import Store, Item, Transaction, Customer, BusinessType, County, SubCounty, Ward


@admin.register(Store)
class StoreAdmin(admin.ModelAdmin):
    list_display = ('name', 'business', 'item_count')
    list_filter = ('business',)
    search_fields = ('name', 'business__name')

    def item_count(self, obj):
        return obj.items.count()
    item_count.short_description = 'Items'


@admin.register(Item)
class ItemAdmin(admin.ModelAdmin):
    list_display = ('material_no', 'description', 'store', 'business',
                    'current_balance_display', 'selling_price', 'status_display')
    list_filter = ('store', 'business')
    search_fields = ('material_no', 'description')

    def current_balance_display(self, obj):
        return obj.current_balance()
    current_balance_display.short_description = 'Balance'

    def status_display(self, obj):
        if obj.current_balance() <= 0:
            return 'OUT OF STOCK'
        elif obj.needs_reorder():
            return 'REORDER'
        return 'AVAILABLE'
    status_display.short_description = 'Status'


@admin.register(Transaction)
class TransactionAdmin(admin.ModelAdmin):
    list_display = ('date', 'item', 'type', 'qty', 'recipient', 'invoice_no', 'business')
    list_filter = ('type', 'date', 'business')
    search_fields = ('item__description', 'invoice_no', 'recipient')


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ('name', 'phone', 'location', 'business')
    list_filter = ('business',)
    search_fields = ('name', 'phone')


@admin.register(BusinessType)
class BusinessTypeAdmin(admin.ModelAdmin):
    list_display = ('name',)
    search_fields = ('name',)


@admin.register(County)
class CountyAdmin(admin.ModelAdmin):
    list_display = ('name',)
    search_fields = ('name',)


@admin.register(SubCounty)
class SubCountyAdmin(admin.ModelAdmin):
    list_display = ('name', 'county')
    list_filter = ('county',)
    search_fields = ('name',)


@admin.register(Ward)
class WardAdmin(admin.ModelAdmin):
    list_display = ('name', 'sub_county')
    list_filter = ('sub_county__county',)
    search_fields = ('name',)