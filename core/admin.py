from django.contrib import admin
from django import forms
from django.urls import path
from django.template.response import TemplateResponse
from django.contrib import messages
import tempfile
import io
import threading

from .tasks import run_import_job

from .models import (
    Store, Item, Transaction, Customer, BusinessType, County, SubCounty, Ward,
    Order, OrderLine, Payment, RiderProfile, SupplierRelationship, Notification,
    ProcurementRequest, SupplierBid, SupplierApplication, Feedback, DeliveryRating,
    PendingTransactionPrompt,
)
from .models import PurchaseOrder, PurchaseOrderLine, SupplierBidLine, Category
from .models import ImportJob


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
    list_display = ('material_no', 'description', 'store', 'business', 'category',
                    'current_balance_display', 'selling_price', 'status_display')
    list_filter = ('store', 'business', 'category')
    search_fields = ('material_no', 'description')

    def get_urls(self):
        urls = super().get_urls()
        my_urls = [
            path('import-products/', self.admin_site.admin_view(self.import_products_view), name=f'{self.model._meta.app_label}_{self.model._meta.model_name}_import_products'),
        ]
        return my_urls + urls

    def import_products_view(self, request):
        if not self.has_change_permission(request):
            from django.http import HttpResponseForbidden
            return HttpResponseForbidden()

        class ProductImportForm(forms.Form):
            csv_file = forms.FileField(label='Products CSV')
            commit = forms.BooleanField(required=False, initial=False, label='Commit to database')
            store = forms.ModelChoiceField(queryset=Store.objects.all(), required=False)

        result = None
        if request.method == 'POST':
            form = ProductImportForm(request.POST, request.FILES)
            if form.is_valid():
                f = form.cleaned_data['csv_file']
                commit = form.cleaned_data['commit']
                store = form.cleaned_data['store']

                # Save uploaded file to a temp file
                tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.csv')
                for chunk in f.chunks():
                    tmp.write(chunk)
                tmp.flush()
                tmp.close()

                # Create ImportJob record and queue background worker
                from core.models import ImportJob
                job = ImportJob.objects.create(
                    job_type='products',
                    original_filename=getattr(f, 'name', 'upload.csv'),
                    file_path=tmp.name,
                    commit=bool(commit),
                    store=store,
                    created_by=request.user,
                    status='pending',
                )
                # Start background thread
                threading.Thread(target=run_import_job, args=(job.id,), daemon=True).start()
                messages.success(request, f'Import job queued (id={job.id}). View Import Jobs in admin for status/results.')
        else:
            form = ProductImportForm()

        context = dict(
            self.admin_site.each_context(request),
            form=form,
            result=result,
            opts=self.model._meta,
        )
        return TemplateResponse(request, 'admin/core/import_products.html', context)

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


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ('code', 'level1', 'level2', 'level3')
    search_fields = ('code', 'level1', 'level2', 'level3')
    list_filter = ('level1',)


@admin.register(ImportJob)
class ImportJobAdmin(admin.ModelAdmin):
    list_display = ('id', 'job_type', 'original_filename', 'status', 'created_by', 'created_at', 'started_at', 'finished_at')
    readonly_fields = ('result_text', 'file_path')
    search_fields = ('original_filename', 'file_path')
    list_filter = ('job_type', 'status', 'created_by')

    def get_urls(self):
        urls = super().get_urls()
        my_urls = [
            path('import-taxonomy/', self.admin_site.admin_view(self.import_taxonomy_view), name=f'{self.model._meta.app_label}_{self.model._meta.model_name}_import_taxonomy'),
        ]
        return my_urls + urls

    def import_taxonomy_view(self, request):
        if not self.has_change_permission(request):
            from django.http import HttpResponseForbidden
            return HttpResponseForbidden()

        class TaxonomyImportForm(forms.Form):
            csv_file = forms.FileField(label='Taxonomy CSV')
            commit = forms.BooleanField(required=False, initial=False, label='Commit to database')

        result = None
        if request.method == 'POST':
            form = TaxonomyImportForm(request.POST, request.FILES)
            if form.is_valid():
                f = form.cleaned_data['csv_file']
                commit = form.cleaned_data['commit']
                tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.csv')
                for chunk in f.chunks():
                    tmp.write(chunk)
                tmp.flush()
                tmp.close()

                out = io.StringIO()
                from core.models import ImportJob
                job = ImportJob.objects.create(
                    job_type='taxonomy',
                    original_filename=getattr(f, 'name', 'upload.csv'),
                    file_path=tmp.name,
                    commit=bool(commit),
                    created_by=request.user,
                    status='pending',
                )
                threading.Thread(target=run_import_job, args=(job.id,), daemon=True).start()
                messages.success(request, f'Taxonomy import job queued (id={job.id}). View Import Jobs in admin for status/results.')
        else:
            form = TaxonomyImportForm()

        context = dict(
            self.admin_site.each_context(request),
            form=form,
            result=result,
            opts=self.model._meta,
        )
        return TemplateResponse(request, 'admin/core/import_taxonomy.html', context)


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ('order_number', 'business', 'customer_name', 'status', 'total_amount', 'delivery_mode', 'created_at')
    list_filter = ('status', 'delivery_mode', 'business')
    search_fields = ('order_number', 'customer_name', 'customer_phone')


class OrderLineInline(admin.TabularInline):
    model = OrderLine
    extra = 0


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ('business', 'amount', 'method', 'status', 'mpesa_receipt', 'created_at')
    list_filter = ('status', 'method', 'business')
    search_fields = ('mpesa_receipt', 'phone')


@admin.register(RiderProfile)
class RiderProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'phone', 'county', 'vehicle_type', 'is_available')
    list_filter = ('vehicle_type', 'is_available', 'county')
    search_fields = ('user__username', 'user__first_name', 'phone')


@admin.register(SupplierRelationship)
class SupplierRelationshipAdmin(admin.ModelAdmin):
    list_display = ('business', 'supplier', 'created_at')
    list_filter = ('business',)
    search_fields = ('business__name', 'supplier__name')


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ('user', 'title', 'notification_type', 'is_read', 'created_at')
    list_filter = ('notification_type', 'is_read')
    search_fields = ('title', 'message', 'user__username')


@admin.register(ProcurementRequest)
class ProcurementRequestAdmin(admin.ModelAdmin):
    list_display = ('title', 'business', 'category', 'status', 'deadline', 'created_at')
    list_filter = ('status', 'category')
    search_fields = ('title', 'description', 'business__name')


class SupplierBidInline(admin.TabularInline):
    model = SupplierBid
    extra = 0
    readonly_fields = ('score',)


class SupplierBidLineInline(admin.TabularInline):
    model = SupplierBidLine
    extra = 0


@admin.register(SupplierBid)
class SupplierBidAdmin(admin.ModelAdmin):
    list_display = ('supplier', 'procurement', 'amount', 'score', 'status', 'created_at')
    list_filter = ('status',)
    search_fields = ('supplier__name', 'procurement__title')
    inlines = [SupplierBidLineInline]


@admin.register(SupplierApplication)
class SupplierApplicationAdmin(admin.ModelAdmin):
    list_display = ('applicant', 'target_business', 'status', 'created_at')
    list_filter = ('status',)
    search_fields = ('applicant__name', 'target_business__name')


@admin.register(Feedback)
class FeedbackAdmin(admin.ModelAdmin):
    list_display = ('feedback_type', 'from_business', 'to_business', 'customer_name', 'rating', 'created_at')
    list_filter = ('feedback_type', 'rating')
    search_fields = ('customer_name', 'comment')


@admin.register(DeliveryRating)
class DeliveryRatingAdmin(admin.ModelAdmin):
    list_display = ('rider', 'order', 'rating', 'on_time', 'item_condition', 'created_at')
    list_filter = ('on_time', 'rating')
    search_fields = ('rated_by', 'comment')


@admin.register(PendingTransactionPrompt)
class PendingTransactionPromptAdmin(admin.ModelAdmin):
    list_display = ('business', 'amount', 'phone', 'payment_channel', 'status', 'mpesa_receipt', 'created_at')
    list_filter = ('status', 'payment_channel')
    search_fields = ('phone', 'mpesa_receipt', 'business__name')


class PurchaseOrderLineInline(admin.TabularInline):
    model = PurchaseOrderLine
    extra = 0


@admin.register(PurchaseOrder)
class PurchaseOrderAdmin(admin.ModelAdmin):
    list_display = ('id', 'business', 'supplier', 'status', 'order_date', 'expected_delivery_date', 'created_at')
    list_filter = ('status', 'business')
    inlines = [PurchaseOrderLineInline]


@admin.register(PurchaseOrderLine)
class PurchaseOrderLineAdmin(admin.ModelAdmin):
    list_display = ('po', 'item', 'quantity_ordered', 'quantity_received', 'unit_price')