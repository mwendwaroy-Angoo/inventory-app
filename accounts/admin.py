from django.contrib import admin
from .models import Business, UserProfile

@admin.register(Business)
class BusinessAdmin(admin.ModelAdmin):
	list_display = ('name', 'created_at')

@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
	list_display = ('user', 'business')
