from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import User
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect
from django.urls import path, reverse
from django.template.response import TemplateResponse
from django import forms
from django.contrib import messages
from django.utils import timezone
import csv
import io

from .models import Business, UserProfile, DeliveryTier, AccountDeletionLog
from core.models import Transaction, Shift, BarTab


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


class RecentTransactionsInline(admin.TabularInline):
    """Read-only — instant 'data ownership' visibility: open any business,
    see its most recent activity right there, no need to jump to the
    Transaction admin and filter by business yourself."""
    model = Transaction
    fk_name = 'business'
    extra = 0
    max_num = 0
    can_delete = False
    fields = ('date', 'item', 'type', 'qty', 'payment_method', 'recipient')
    readonly_fields = fields
    verbose_name_plural = 'Recent Transactions (latest 15)'

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def get_queryset(self, request):
        qs = super().get_queryset(request).select_related('item').order_by('-created_at')
        # A sliced queryset can't be filtered further by the admin formset
        # machinery downstream (Django raises on .filter() after a slice) —
        # materialize the latest 15 ids first, then return a normal,
        # unsliced queryset filtered to just those.
        latest_ids = list(qs.values_list('id', flat=True)[:15])
        return qs.filter(id__in=latest_ids)


class OpenShiftsInline(admin.TabularInline):
    """Read-only — which shifts are live on this business right now."""
    model = Shift
    fk_name = 'business'
    extra = 0
    max_num = 0
    can_delete = False
    fields = ('staff', 'store', 'started_at', 'opening_float')
    readonly_fields = fields
    verbose_name_plural = 'Currently Open Shifts'

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def get_queryset(self, request):
        return super().get_queryset(request).filter(status='OPEN').select_related('staff', 'store')


class ActiveStaffInline(admin.TabularInline):
    """Read-only — who currently has a login on this business."""
    model = UserProfile
    fk_name = 'business'
    extra = 0
    max_num = 0
    can_delete = False
    fields = ('user', 'role', 'phone')
    readonly_fields = fields
    verbose_name_plural = 'Active Staff'

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def get_queryset(self, request):
        return super().get_queryset(request).filter(user__is_active=True).select_related('user')


class OpenNowFilter(admin.SimpleListFilter):
    title = 'open now'
    parameter_name = 'open_now'

    def lookups(self, request, model_admin):
        return (('yes', 'Open now'), ('no', 'Closed now'))

    def queryset(self, request, queryset):
        value = self.value()
        if value not in ('yes', 'no'):
            return queryset
        want_open = value == 'yes'
        matching_ids = [b.id for b in queryset if b.is_open() == want_open]
        return queryset.filter(id__in=matching_ids)


def _delete_business_safely(business, performed_by_username):
    """Detach every OTHER staff member's UserProfile first (so their own
    logins survive with no business attached — UserProfile.business is
    CASCADE, so skipping this step would destructively wipe out every other
    staff member's login the instant the business is deleted), then delete
    the business, then delete the owner's own User login too. Mirrors
    accounts.views.delete_account's atomic role='owner' sequence — this is
    the same action, just triggered by a platform admin instead of the
    owner themselves, per Roy's explicit choice that a business deletion
    from admin removes the owner's login along with it (2026-08-01)."""
    owner_user = business.owner
    with transaction.atomic():
        AccountDeletionLog.objects.create(
            username=owner_user.username if owner_user else '',
            email=(owner_user.email or '') if owner_user else '',
            role='owner',
            business_name=business.name,
            reason='admin_removed',
            details=f'Deleted via Django admin by {performed_by_username}',
        )
        UserProfile.objects.filter(business=business).exclude(user=owner_user).update(business=None)
        business.delete()
        if owner_user:
            owner_user.delete()


@admin.register(Business)
class BusinessAdmin(admin.ModelAdmin):
    list_display = (
        'name', 'get_owner_username', 'get_business_type', 'get_staff_count',
        'get_is_open_badge', 'get_today_activity', 'get_county', 'phone', 'created_at',
    )
    search_fields = ('name', 'owner__username', 'phone')
    list_filter = ('business_type', 'county', 'created_at', OpenNowFilter)
    date_hierarchy = 'created_at'
    readonly_fields = ('created_at', 'daraja_consumer_secret', 'daraja_passkey')
    filter_horizontal = ('categories',)
    inlines = [ActiveStaffInline, OpenShiftsInline, RecentTransactionsInline, DeliveryTierInline]

    fieldsets = (
        ('Identity / Ownership', {
            'fields': ('owner', 'name', 'business_type', 'categories'),
        }),
        ('Location', {
            'fields': ('county', 'sub_county', 'ward', 'address'),
        }),
        ('Contact', {
            'fields': ('phone', 'email'),
        }),
        ('Operating Hours', {
            'fields': ('opening_time', 'closing_time', 'is_open_override'),
        }),
        ('Payment Receiving', {
            'fields': (
                'preferred_payment_channel', 'mpesa_till', 'mpesa_paybill',
                'mpesa_paybill_account', 'mpesa_pochi', 'mpesa_phone',
            ),
        }),
        ('Delivery Settings', {
            'fields': (
                'offers_delivery', 'delivery_radius_km', 'delivery_fee',
                'delivery_fee_per_km', 'min_order_amount', 'min_order_per_km',
            ),
            'classes': ('collapse',),
        }),
        ('GPS Coordinates', {
            'fields': ('latitude', 'longitude'),
            'classes': ('collapse',),
        }),
        ('Daraja API Credentials (sensitive)', {
            'fields': (
                'daraja_consumer_key', 'daraja_consumer_secret', 'daraja_passkey',
                'daraja_c2b_registered', 'daraja_environment',
            ),
            'classes': ('collapse',),
        }),
        ('Pre-App Business History', {
            'fields': ('pre_app_cumulative_profit', 'business_start_date'),
            'classes': ('collapse',),
        }),
        ('Credit Policy', {
            'fields': (
                'credit_policy_enabled', 'credit_window_days', 'debt_cycle',
                'debt_cutoff_days_before_month_end', 'block_if_overdue',
                'overdue_grace_days', 'late_repayment_strikes', 'late_threshold_days',
                'defaulter_permanent', 'cooldown_days', 'default_credit_limit',
                'debt_erase_requires_approval',
            ),
            'classes': ('collapse',),
        }),
        ('SMS Bundling', {
            'fields': ('last_txn_sms_at', 'last_daily_summary_sent_at'),
            'classes': ('collapse',),
        }),
        ('Keg / Bar Settings', {
            'fields': (
                'weighs_kegs', 'block_sales_past_target', 'keg_variance_tolerance_pct',
                'keg_default_gross_kg', 'keg_default_tare_kg', 'keg_revenue_multiplier',
                'keg_alerts_enabled', 'keg_alert_min_litres', 'keg_loss_baseline_pct',
                'keg_loss_baseline_sample', 'cups_per_pint', 'cups_per_jug',
                'cup_low_notified_at',
            ),
            'classes': ('collapse',),
        }),
        ('KRA / eTIMS', {
            'fields': ('kra_pin',),
            'classes': ('collapse',),
        }),
        ('Kitchen Module', {
            'fields': ('has_kitchen',),
            'classes': ('collapse',),
        }),
        ('Haki (Staff Fairness) Module', {
            'fields': ('haki_enabled',),
            'classes': ('collapse',),
        }),
        ('Recurring Expenses', {
            'fields': ('last_expense_review_date',),
            'classes': ('collapse',),
        }),
        ('DJ / MC Performer Module', {
            'fields': ('event_sms_enabled', 'performer_approval_threshold'),
            'classes': ('collapse',),
        }),
        ('Metadata', {
            'fields': ('created_at',),
        }),
    )

    def get_staff_count(self, obj):
        return obj.users.count()
    get_staff_count.short_description = 'Staff'

    def get_is_open_badge(self, obj):
        try:
            return '🟢 Open' if obj.is_open() else '🔴 Closed'
        except Exception:
            return '-'
    get_is_open_badge.short_description = 'Open Now'

    def get_today_activity(self, obj):
        today = timezone.localdate()
        txns = Transaction.objects.filter(business=obj, type='Issue', date=today)
        count = txns.count()
        if count == 0:
            return '—'
        total_rev = sum((t.revenue() or 0) for t in txns)
        return f"{count} sales / KES {total_rev:,.0f}"
    get_today_activity.short_description = "Today's Activity"

    def get_urls(self):
        urls = super().get_urls()
        my_urls = [
            path('bulk-sync-categories/', self.admin_site.admin_view(self.bulk_sync_categories_view), name='accounts_business_bulk_sync_categories'),
            path('<int:business_id>/delete-business/', self.admin_site.admin_view(self.delete_business_view), name='accounts_business_delete_business'),
        ]
        return my_urls + urls

    # ── Safe, guided business deletion (replaces Django's default delete) ──
    def has_delete_permission(self, request, obj=None):
        # Deletion must go through delete_business_view's guided,
        # impact-preview + typed-confirmation flow — never Django's default
        # single/bulk delete, which would either destructively CASCADE-delete
        # every other staff member's UserProfile (UserProfile.business is
        # CASCADE) or silently leave the owner's own User login behind.
        return False

    def get_actions(self, request):
        actions = super().get_actions(request)
        actions.pop('delete_selected', None)
        return actions

    def delete_business_view(self, request, business_id):
        if not self.has_change_permission(request):
            from django.http import HttpResponseForbidden
            return HttpResponseForbidden()
        business = get_object_or_404(Business, id=business_id)

        if request.method == 'POST':
            confirm_text = request.POST.get('confirm_text', '').strip()
            if confirm_text != business.name:
                messages.error(request, f'Please type the business name exactly ("{business.name}") to confirm.')
                return redirect(reverse('admin:accounts_business_delete_business', args=[business.id]))
            owner_username = business.owner.username if business.owner else '(no owner)'
            business_name = business.name
            _delete_business_safely(business, request.user.username)
            messages.success(
                request,
                f'"{business_name}" and owner login "{owner_username}" have been permanently deleted.',
            )
            return redirect(reverse('admin:accounts_business_changelist'))

        from core.models import Item, Store, Order
        context = dict(
            self.admin_site.each_context(request),
            business=business,
            opts=self.model._meta,
            item_count=Item.objects.filter(business=business).count(),
            transaction_count=Transaction.objects.filter(business=business).count(),
            store_count=Store.objects.filter(business=business).count(),
            staff_count=UserProfile.objects.filter(business=business).exclude(user=business.owner).count(),
            order_count=Order.objects.filter(business=business).count(),
        )
        return TemplateResponse(request, 'admin/accounts/delete_business.html', context)

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
