from django.contrib import admin
from .models import Business, UserProfile


@admin.register(Business)
class BusinessAdmin(admin.ModelAdmin):
    list_display = ('name', 'owner', 'business_type', 'county', 'sub_location', 'created_at')
    search_fields = ('name', 'owner__username')
    list_filter = ('business_type', 'county', 'created_at')
    readonly_fields = ('created_at',)


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'business')          # ← fixed: 'client' → 'business'
    search_fields = ('user__username', 'business__name')
    list_filter = ('business',)                  # ← fixed: 'client' → 'business'