from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import User
from .models import Business, UserProfile, DeliveryTier


# ── Inline UserProfile on the User admin page ──
class UserProfileInline(admin.StackedInline):
    model = UserProfile
    can_delete = True
    verbose_name_plural = 'Profile'
    fk_name = 'user'
    fields = ('business', 'role', 'phone', 'has_seen_tutorial')


# Extend the default User admin to show profile inline
class UserAdmin(BaseUserAdmin):
    inlines = [UserProfileInline]
    list_display = ('username', 'email', 'first_name', 'last_name', 'get_role', 'get_business', 'is_active')
    list_filter = BaseUserAdmin.list_filter + ('userprofile__role',)

    def get_role(self, obj):
        try:
            return obj.userprofile.get_role_display()
        except UserProfile.DoesNotExist:
            return '-'
    get_role.short_description = 'Role'

    def get_business(self, obj):
        try:
            return obj.userprofile.business.name if obj.userprofile.business else '-'
        except UserProfile.DoesNotExist:
            return '-'
    get_business.short_description = 'Business'


# Unregister the default User admin and register our enhanced one
admin.site.unregister(User)
admin.site.register(User, UserAdmin)


class DeliveryTierInline(admin.TabularInline):
    model = DeliveryTier
    extra = 0


@admin.register(Business)
class BusinessAdmin(admin.ModelAdmin):
    list_display = ('name', 'get_owner_username', 'get_business_type', 'get_county', 'get_sub_county', 'phone', 'created_at')
    search_fields = ('name', 'owner__username', 'phone')
    list_filter = ('business_type', 'county', 'created_at')
    readonly_fields = ('created_at',)
    inlines = [DeliveryTierInline]

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

    def get_sub_county(self, obj):
        return obj.sub_county.name if obj.sub_county else '-'
    get_sub_county.short_description = 'Sub County'


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'get_email', 'business', 'role', 'phone', 'has_seen_tutorial')
    list_editable = ('role',)
    search_fields = ('user__username', 'user__email', 'business__name', 'phone')
    list_filter = ('role', 'business')
    raw_id_fields = ('user', 'business')

    def get_email(self, obj):
        return obj.user.email
    get_email.short_description = 'Email'
    get_email.admin_order_field = 'user__email'