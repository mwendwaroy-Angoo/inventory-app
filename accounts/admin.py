from django.contrib import admin
from .models import Business, UserProfile


@admin.register(Business)
class BusinessAdmin(admin.ModelAdmin):
    list_display = ('name', 'get_owner_username', 'get_business_type', 'get_county', 'get_sub_location', 'created_at')
    search_fields = ('name', 'owner__username')
    list_filter = ('business_type', 'county', 'created_at')
    readonly_fields = ('created_at',)

    def get_owner_username(self, obj):
        return obj.owner.username if obj.owner else '-'
    get_owner_username.short_description = 'Owner'
    get_owner_username.admin_order_field = 'owner__username'

    def get_business_type(self, obj):
        return obj.business_type.name if obj.business_type else '-'
    get_business_type.short_description = 'Business Type'

    def get_county(self, obj):
        return obj.county.name if obj.county else '-'
    get_county.short_description = 'County'

    def get_sub_location(self, obj):
        return obj.sub_location.name if obj.sub_location else '-'
    get_sub_location.short_description = 'Sub Location'


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'business')
    search_fields = ('user__username', 'business__name')
    list_filter = ('business',)