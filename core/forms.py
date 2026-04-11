from django import forms
from django.utils.translation import gettext_lazy as _
from .models import Item, Store


class ItemForm(forms.ModelForm):
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

        if business:
            self.fields['store'].queryset = Store.objects.filter(business=business)

        # Required fields
        self.fields['description'].required = True
        self.fields['store'].required = True
        self.fields['selling_price'].required = True

        # Optional fields
        self.fields['material_no'].required = False
        self.fields['unit'].required = False
        self.fields['cost_price'].required = False
        self.fields['opening_bin_balance'].required = False
        self.fields['opening_physical'].required = False
        self.fields['reorder_level'].required = False
        self.fields['reorder_quantity'].required = False

        # Hide cost_price from staff
        if not show_cost_price:
            del self.fields['cost_price']

        for field in self.fields.values():
            field.widget.attrs['class'] = 'form-control'