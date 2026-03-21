from django.contrib import admin
from .models import Business, UserProfile  # Adjust model names if different

@admin.register(Business)
class BusinessAdmin(admin.ModelAdmin):
    list_display = ('name', 'owner', 'created_at')  # Customize fields
    search_fields = ('name', 'owner__username')
    list_filter = ('created_at',)

@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'client')  # or 'business' if you renamed it
    search_fields = ('user__username', 'client__name')
    list_filter = ('client',)