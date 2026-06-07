from django import forms
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
import json
from .models import Item, Store, PurchaseOrder, PurchaseOrderLine, Category, BusinessExpense, CapitalInvestment, GoodsReceipt
from django.forms import inlineformset_factory, formset_factory


class ItemForm(forms.ModelForm):
    tags = forms.CharField(required=False, label=_('Tags'),
                           help_text=_('Comma-separated tags (e.g. organic, halal)'),
                           widget=forms.TextInput(attrs={'placeholder': _('e.g. organic, halal')}))
    category_level1 = forms.ChoiceField(required=False, label=_('Category Level 1'))
    category_level2 = forms.ChoiceField(required=False, label=_('Category Level 2'))
    category_level3 = forms.ChoiceField(required=False, label=_('Category Level 3'))

    class Meta:
        model = Item
        fields = [
            'description',
            'material_no',
            'unit',
            'store',
            'selling_price',
            'cost_price',
            'opening_bin_balance',
            'opening_physical',
            'reorder_level',
            'reorder_quantity',
            'lead_time_days',
            'safety_days',
            'category',
            'tags',
            'is_yield_item',
            'yield_factor',
        ]
        widgets = {
            'description': forms.TextInput(attrs={'placeholder': _('e.g. Cement 50kg')}),
            'material_no': forms.TextInput(attrs={'placeholder': _('e.g. MAT-001')}),
            'unit': forms.TextInput(attrs={'placeholder': _('e.g. Bags, Litres, Pcs')}),
            'selling_price': forms.NumberInput(attrs={'placeholder': '0.00'}),
            'cost_price': forms.NumberInput(attrs={'placeholder': '0.00'}),
            'opening_bin_balance': forms.NumberInput(attrs={'placeholder': '0'}),
            'opening_physical': forms.NumberInput(attrs={'placeholder': '0'}),
            'reorder_level': forms.NumberInput(attrs={'placeholder': '0'}),
            'reorder_quantity': forms.NumberInput(attrs={'placeholder': '0'}),
            'lead_time_days': forms.NumberInput(attrs={'placeholder': '7'}),
            'safety_days': forms.NumberInput(attrs={'placeholder': '2'}),
            'yield_factor': forms.NumberInput(attrs={
                'placeholder': '0.65',
                'step': '0.0001',
                'min': '0.01',
                'max': '0.9999',
            }),
        }

    def __init__(self, *args, business=None, show_cost_price=False, **kwargs):
        super().__init__(*args, **kwargs)

        self.fields['description'].label = _('Description')
        self.fields['material_no'].label = _('Material No')
        self.fields['unit'].label = _('Unit')
        self.fields['store'].label = _('Store')
        self.fields['selling_price'].label = _('Selling Price')
        self.fields['cost_price'].label = _('Cost Price')
        self.fields['opening_bin_balance'].label = _('Opening Bin Balance')
        self.fields['opening_physical'].label = _('Opening Physical')
        self.fields['reorder_level'].label = _('Reorder Level')
        self.fields['reorder_quantity'].label = _('Reorder Quantity')
        self.fields['lead_time_days'].label = _('Lead Time (days)')
        self.fields['safety_days'].label = _('Safety Days')
        self.fields['is_yield_item'].label = _('This item has a yield factor (loses weight/volume during processing)')
        self.fields['yield_factor'].label = _('Yield Factor (0–1, e.g. 0.65 = 65%)')

        if business:
            self.fields['store'].queryset = Store.objects.filter(business=business)
            # If business has curated categories, limit category choices to those
            try:
                curated_qs = business.categories.all()
                if curated_qs.exists():
                    self.fields['category'].queryset = curated_qs
                else:
                    self.fields['category'].queryset = Category.objects.all()
            except Exception:
                self.fields['category'].queryset = Category.objects.all()
        else:
            try:
                self.fields['category'].queryset = Category.objects.all()
            except Exception:
                pass
        # hide the raw category select; we'll control it via the hierarchical picker
        try:
            self.fields['category'].widget = forms.HiddenInput()
        except Exception:
            pass

        # Required fields
        self.fields['description'].required = True
        self.fields['store'].required = True
        self.fields['selling_price'].required = False

        # Optional fields
        self.fields['material_no'].required = False
        self.fields['unit'].required = False
        self.fields['cost_price'].required = False
        self.fields['opening_bin_balance'].required = False
        self.fields['opening_physical'].required = False
        self.fields['reorder_level'].required = False
        self.fields['reorder_quantity'].required = False
        self.fields['lead_time_days'].required = False
        self.fields['safety_days'].required = False
        self.fields['is_yield_item'].required = False
        self.fields['yield_factor'].required = False

        # Hide cost_price from staff
        if not show_cost_price:
            del self.fields['cost_price']
        # Initialize tags field for editing (comma separated)
        if self.instance and getattr(self.instance, 'tags', None):
            try:
                self.fields['tags'].initial = ', '.join(self.instance.tags)
            except Exception:
                self.fields['tags'].initial = self.instance.tags

        # Build category hierarchy JSON for client-side picker
        try:
            qs = self.fields['category'].queryset
            cats = list(qs.values('id', 'level1', 'level2', 'level3'))
            # normalize None to empty string for JS
            for c in cats:
                c['level2'] = c.get('level2') or ''
                c['level3'] = c.get('level3') or ''
            self.categories_json = json.dumps(cats)
        except Exception:
            self.categories_json = json.dumps([])

        # Recommended tags based on business type
        self.recommended_tags = []
        try:
            bt = business.business_type.name if business and business.business_type else ''
            recommendations = {
                'Supermarket': ['organic', 'fresh', 'frozen', 'dairy-free', 'gluten-free', 'halal', 'on-sale', 'imported', 'beverages'],
                'Pharmacy': ['otc', 'prescription', 'health', 'vitamins', 'medical'],
                'Restaurant': ['hot-food', 'takeaway', 'vegan', 'vegetarian', 'spicy'],
                'Butchery': ['fresh', 'beef', 'poultry', 'goat'],
                'Bakery': ['fresh', 'bread', 'pastry', 'gluten-free'],
                'Hardware': ['tools', 'building', 'paint', 'electrical'],
            }
            self.recommended_tags = recommendations.get(bt, ['organic', 'local', 'imported'])
        except Exception:
            self.recommended_tags = []

        for field in self.fields.values():
            field.widget.attrs['class'] = 'form-control'

    def clean_tags(self):
        val = self.cleaned_data.get('tags', '')
        if isinstance(val, str):
            tags = [t.strip().lower() for t in val.replace(';', ',').split(',') if t.strip()]
            return tags
        if isinstance(val, list):
            return val
        return []

    def save(self, commit=True):
        # Ensure category FK is set from hidden/category field (form has category field)
        obj = super().save(commit=False)
        # tags are cleaned already
        obj.tags = self.cleaned_data.get('tags') or []
        # category is a ModelChoiceField so it will be in cleaned_data if provided
        cat = self.cleaned_data.get('category')
        if cat:
            obj.category = cat
        if commit:
            obj.save()
            self.save_m2m()
        return obj


class PurchaseOrderForm(forms.ModelForm):
    class Meta:
        model = PurchaseOrder
        fields = [
            'supplier',
            'status',
            'expected_delivery_date',
            'notes',
        ]


class PurchaseOrderLineForm(forms.ModelForm):
    class Meta:
        model = PurchaseOrderLine
        fields = [
            'item',
            'quantity_ordered',
            'unit_price',
        ]


# Inline formset for PurchaseOrder lines
PurchaseOrderLineFormSet = inlineformset_factory(
    PurchaseOrder,
    PurchaseOrderLine,
    form=PurchaseOrderLineForm,
    extra=1,
    can_delete=True,
)


class BusinessExpenseForm(forms.ModelForm):
    """Form for recording business expenses (labor, rent, electricity, etc.)."""

    class Meta:
        model = BusinessExpense
        fields = ['description', 'amount', 'category', 'date', 'notes']
        widgets = {
            'description': forms.TextInput(attrs={'placeholder': _('e.g. Staff salaries for March')}),
            'amount': forms.NumberInput(attrs={'placeholder': '0.00', 'step': '0.01'}),
            'date': forms.DateInput(attrs={'type': 'date'}),
            'notes': forms.Textarea(attrs={'rows': 3, 'placeholder': _('Optional notes...')}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs['class'] = 'form-control'
        self.fields['description'].label = _('Description')
        self.fields['amount'].label = _('Amount (KES)')
        self.fields['category'].label = _('Category')
        self.fields['date'].label = _('Date')
        self.fields['notes'].label = _('Notes')
        self.fields['date'].initial = timezone.now().date()


class CapitalInvestmentForm(forms.ModelForm):
    class Meta:
        model  = CapitalInvestment
        fields = ['description', 'amount', 'category', 'date_acquired', 'notes']
        widgets = {
            'description': forms.TextInput(attrs={
                'placeholder': 'e.g. 3 Pool Tables, Borehole Rig, Matatu KBX 123Z'
            }),
            'date_acquired': forms.DateInput(attrs={'type': 'date'}),
            'notes': forms.Textarea(attrs={'rows': 2,
                'placeholder': 'Optional — supplier, loan details, etc.'}),
        }


# ──────────────────────────────────────────────────────────────
# GOODS RECEIPT FORMS
# ──────────────────────────────────────────────────────────────

class GoodsReceiptForm(forms.ModelForm):
    """Header details for a goods receipt (delivery note, date, notes)."""

    class Meta:
        model = GoodsReceipt
        fields = ['received_date', 'delivery_note_no', 'notes']
        widgets = {
            'received_date': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
            'delivery_note_no': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': _('e.g. DN-2025-001'),
            }),
            'notes': forms.Textarea(attrs={
                'class': 'form-control',
                'rows': 2,
                'placeholder': _('Optional — condition notes, driver name, etc.'),
            }),
        }
        labels = {
            'received_date': _('Date Received'),
            'delivery_note_no': _('Delivery Note No.'),
            'notes': _('Notes'),
        }


class GoodsReceiptLineForm(forms.Form):
    """
    One row in the receipt form — one per outstanding PO line.
    Uses a plain Form (not ModelForm) so we can pre-populate from PO lines.

    The po_line_id hidden field ties each submitted row back to its PurchaseOrderLine.
    """
    po_line_id = forms.IntegerField(widget=forms.HiddenInput)
    quantity_received = forms.IntegerField(
        min_value=0,
        widget=forms.NumberInput(attrs={'class': 'form-control qty-input', 'min': '0'}),
        label=_('Qty Received'),
    )
    actual_unit_price = forms.DecimalField(
        max_digits=12,
        decimal_places=2,
        widget=forms.NumberInput(attrs={'class': 'form-control price-input', 'step': '0.01', 'min': '0'}),
        label=_('Actual Price (KES)'),
    )
    update_cost_price = forms.BooleanField(
        required=False,
        label=_('Update cost price'),
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}),
    )
    notes = forms.CharField(
        required=False,
        max_length=200,
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': _('Optional note…')}),
        label=_('Notes'),
    )


# Formset — extra=0 so only the forms we explicitly provide via `initial` are rendered.
GoodsReceiptLineFormSet = formset_factory(GoodsReceiptLineForm, extra=0)
