from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import User
from .models import Business, UserProfile, DeliveryTier, AccountDeletionLog
from django.urls import path
from django.template.response import TemplateResponse
from django import forms
from django.contrib import messages
import csv
import io


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
    list_display = ('name', 'get_owner_username', 'get_business_type', 'get_categories', 'get_county', 'get_sub_county', 'phone', 'created_at')
    search_fields = ('name', 'owner__username', 'phone')
    list_filter = ('business_type', 'county', 'created_at')
    readonly_fields = ('created_at',)
    inlines = [DeliveryTierInline]
    filter_horizontal = ('categories',)

    def get_categories(self, obj):
        try:
            return ', '.join([str(c) for c in obj.categories.all()]) if obj.categories.exists() else '-'
        except Exception:
            return '-'
    get_categories.short_description = 'Categories'

    def get_urls(self):
        urls = super().get_urls()
        my_urls = [
            path('bulk-sync-categories/', self.admin_site.admin_view(self.bulk_sync_categories_view), name='accounts_business_bulk_sync_categories'),
        ]
        return my_urls + urls

    def bulk_sync_categories_view(self, request):
        if not self.has_change_permission(request):
            from django.http import HttpResponseForbidden
            return HttpResponseForbidden()

        class BulkSyncForm(forms.Form):
            csv_file = forms.FileField(label='Business -> Categories CSV')
            commit = forms.BooleanField(required=False, initial=False, label='Commit changes')

        result = None
        errors = []
        processed = 0
        if request.method == 'POST':
            form = BulkSyncForm(request.POST, request.FILES)
            if form.is_valid():
                f = form.cleaned_data['csv_file']
                commit = form.cleaned_data['commit']
                try:
                    data = f.read().decode('utf-8')
                except Exception:
                    data = f.read()
                    try:
                        data = data.decode('utf-8')
                    except Exception:
                        data = data.decode('latin-1')

                reader = csv.DictReader(io.StringIO(data))
                required = {'business', 'categories'}
                if not set(reader.fieldnames or []) >= required:
                    messages.error(request, f"CSV must contain columns: {', '.join(required)}")
                else:
                    processed = 0
                    updated = 0
                    missing_business = []
                    missing_categories = []
                    for r in reader:
                        processed += 1
                        b_key = (r.get('business') or '').strip()
                        cats_raw = (r.get('categories') or '').strip()
                        if not b_key:
                            missing_business.append((processed, 'blank business'))
                            continue
                        # Try by id first, then by name
                        business = None
                        if b_key.isdigit():
                            business = Business.objects.filter(id=int(b_key)).first()
                        if not business:
                            business = Business.objects.filter(name__iexact=b_key).first()
                        if not business:
                            missing_business.append((processed, b_key))
                            continue

                        codes = [c.strip() for c in cats_raw.replace(';', ',').split(',') if c.strip()]
                        cat_objs = []
                        for code in codes:
                            cat = None
                            # if code looks numeric, ignore — Category.code is string; match by code
                            cat = __import__('core.models', fromlist=['Category']).Category.objects.filter(code=code).first()
                            if not cat:
                                # allow matching by level names
                                cat = __import__('core.models', fromlist=['Category']).Category.objects.filter(level1__iexact=code).first()
                            if cat:
                                cat_objs.append(cat)
                            else:
                                missing_categories.append((processed, code))

                        if commit:
                            # replace curated categories for this business
                            business.categories.set(cat_objs)
                            business.save()
                            updated += 1

                    result = {
                        'processed': processed,
                        'updated': updated if commit else 0,
                        'missing_business': missing_business,
                        'missing_categories': missing_categories,
                    }
                    if commit:
                        messages.success(request, f"Bulk sync applied: {updated} businesses updated.")
                    else:
                        messages.info(request, f"Preview: {processed} rows parsed. Set 'Commit' to apply changes.")
        else:
            form = BulkSyncForm()

        context = dict(
            self.admin_site.each_context(request),
            form=form,
            result=result,
            opts=self.model._meta,
        )
        return TemplateResponse(request, 'admin/accounts/bulk_sync_categories.html', context)

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


@admin.register(AccountDeletionLog)
class AccountDeletionLogAdmin(admin.ModelAdmin):
    list_display = ('username', 'role', 'business_name', 'reason', 'deleted_at')
    list_filter = ('reason', 'role', 'deleted_at')
    search_fields = ('username', 'email', 'business_name', 'details')
    readonly_fields = ('username', 'email', 'role', 'business_name', 'reason', 'details', 'deleted_at')