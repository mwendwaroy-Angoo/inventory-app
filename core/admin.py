from django.contrib import admin
from .models import Store, Item, Transaction

class ItemAdmin(admin.ModelAdmin):
    list_display = ['material_no', 'description', 'store', 'current_balance_def', 'reorder_status']
    list_filter = ['store']
    search_fields = ['material_no', 'description']
    list_editable = ['store']  # Quick edit store directly in list

    actions = ['assign_to_sf', 'assign_to_computer', 'assign_to_maintenance']

    def current_balance_def(self, obj):
        return obj.current_balance()
    current_balance_def.short_description = 'Current Balance'

    def reorder_status(self, obj):
        if obj.current_balance() <= 0:
            return 'OUT OF STOCK'
        elif obj.needs_reorder():
            return 'REORDER'
        return 'AVAILABLE'
    reorder_status.short_description = 'Status'

    def assign_to_sf(self, request, queryset):
        store = Store.objects.get(name='SF STORE')
        updated = queryset.update(store=store)
        self.message_user(request, f"{updated} items assigned to SF STORE.")
    assign_to_sf.short_description = "Assign selected to SF STORE"

    def assign_to_computer(self, request, queryset):
        store = Store.objects.get(name='COMPUTER STORE')
        updated = queryset.update(store=store)
        self.message_user(request, f"{updated} items assigned to COMPUTER STORE.")
    assign_to_computer.short_description = "Assign selected to COMPUTER STORE"

    def assign_to_maintenance(self, request, queryset):
        store = Store.objects.get(name='MAINTENANCE STORE')
        updated = queryset.update(store=store)
        self.message_user(request, f"{updated} items assigned to MAINTENANCE STORE.")
    assign_to_maintenance.short_description = "Assign selected to MAINTENANCE STORE"

admin.site.register(Item, ItemAdmin)
admin.site.register(Store)
admin.site.register(Transaction)