import os
import django
import json

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'stockapp.settings')
django.setup()

from accounts.models import BusinessType, Business
from core.models import Category, Store, Item
from core.forms import ItemForm

bt, _ = BusinessType.objects.get_or_create(name='Supermarket')
b, created = Business.objects.get_or_create(name='Test Supermarket', defaults={'business_type': bt})

if not Category.objects.exists():
    c = Category.objects.create(code='SUP-FRUIT', level1='Produce', level2='Fruit', level3='Citrus')
else:
    c = Category.objects.first()

b.categories.add(c)
store, _ = Store.objects.get_or_create(business=b, name='Main')

f = ItemForm(business=b)
print('categories_json_len=', len(f.categories_json))
print('recommended_tags=', f.recommended_tags)

# simulate POST
data = {
    'description': 'Test Item',
    'selling_price': '50',
    'store': str(store.id),
    'material_no': 'MAT-XYZ',
    'unit': 'pcs',
    'category': str(c.id),
    'tags': 'organic, fresh'
}
form = ItemForm(data, business=b)
print('is_valid', form.is_valid())
print('errors', form.errors)
if form.is_valid():
    item = form.save()
    print('saved_item_id=', item.id)
    print('item_category=', item.category)
    print('item_tags=', item.tags)
else:
    print('form invalid, not saved')
