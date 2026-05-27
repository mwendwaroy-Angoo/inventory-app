# Duka Mwecheche — Claude Project Context

## Project Overview
Multi-tenant Django inventory and business management web application for Kenyan SMEs.
Live at: https://www.dukamwecheche.co.ke
GitHub: https://github.com/mwendwaroy-Angoo/inventory-app
Deployed on: Render (web service) with PostgreSQL database

## Developer
- Name: Collins (goes by Roy), based in Nairobi, Kenya
- Business account username on live app: RoyMwendwa
- Learning Django through building — explain concepts when introducing new patterns

---

## Tech Stack
- Python 3.13+
- Django (latest)
- Bootstrap 5 via django-bootstrap5
- Chart.js (dashboards and analytics)
- WhiteNoise (static files)
- dj-database-url (database config)
- openpyxl (Excel exports)
- africastalking (SMS)
- Twilio (WhatsApp)
- Gmail SMTP (email)
- Select2 (searchable dropdowns)
- Leaflet.js (maps in business settings)
- PostgreSQL (production), SQLite (local dev)

---

## Django Apps
1. `core` — items, transactions, stores, customers, notifications, compliance
2. `accounts` — business registration, user profiles, staff management

---

## URL Structure
- `accounts/` — Django built-in auth URLs
- `business/` — custom accounts app URLs
- `business/ajax/subcounties/` — AJAX endpoint for county dropdowns
- `business/ajax/wards/` — AJAX endpoint for ward dropdowns
- Standard paths for core functionality

---

## Key Models

### accounts.Business
```python
# Key fields:
name, role (owner/supplier/rider), business_type, phone, email, address
county, sub_county, ward  # FK to seeded Kenya geography models
latitude, longitude
opening_time, closing_time, is_open_override
offers_delivery, delivery_radius_km, delivery_fee, delivery_fee_per_km
min_order_amount, min_order_per_km
mpesa_till, mpesa_paybill, mpesa_paybill_account, mpesa_pochi, mpesa_phone
preferred_payment_channel
business_start_date, pre_app_cumulative_profit
```

### accounts.UserProfile
```python
# Key fields:
user (FK), business (FK to Business), role (owner/staff/rider/supplier)
phone
has_seen_tutorial
# Properties: is_owner, is_staff_member, is_rider, is_supplier
```

### core.Item
```python
# Key fields:
business (FK), store (FK), description, material_number
unit, current_balance, reorder_level
selling_price, cost_price
```

### core.Transaction
```python
# Key fields:
business (FK), item (FK), transaction_type (receipt/issue)
quantity, unit_price, total_value
recipient, invoice_no
created_by (FK User), created_at
```

### core.Store
```python
business (FK), name, description
# Note: Store.__str__ must handle null business gracefully
```

### core.Customer
```python
business (FK), name, phone, email, address
```

### core.Notification
```python
business (FK), user (FK), message, is_read, created_at
related_name='app_notifications'  # IMPORTANT — use this related_name
```

### core.BusinessTypeRequirement
```python
# Compliance system — Phase 1 complete (182+ requirements, 60+ business types)
business_type (FK), name, description, category, tier (micro/semi/formal)
is_mandatory, document_required, validity_period_days
issuing_authority, approximate_cost, reference_url
```

### core.BusinessCompliance
```python
business (FK), requirement (FK to BusinessTypeRequirement)
is_declared, notes, declared_at
unique_together: (business, requirement)
```

### core.SupplierRequirement (NEW — just built, not yet deployed)
```python
# Mirrors BusinessTypeRequirement but for supplier-role businesses
name, description, category, supplier_category
is_mandatory, document_required, validity_period_days
issuing_authority, reference_url
```

### core.SupplierCompliance (NEW — just built, not yet deployed)
```python
supplier (FK to Business, role=supplier), requirement (FK to SupplierRequirement)
status (pending/compliant/non_compliant/waived/expired)
document, notes, submitted_at
verified_by (FK User), verified_at, expires_at
unique_together: (supplier, requirement)
# Auto-sets expires_at on save when status=compliant and validity_period_days set
```

---

## Geography
- Database seeded with all 47 Kenya counties, sub-counties, and wards via data migrations
- Dynamic dropdowns use AJAX: `/business/ajax/subcounties/` and `/business/ajax/wards/`

---

## Management Commands
- `reset_superuser` — runs on every Render deploy to manage admin credentials

---

## UI Theme — "Duka Mwecheche Dark Luxury"
```css
:root {
    --onyx: #1a1a1a;          /* page background */
    --onyx-light: #242424;
    --onyx-card: #2a2a2a;     /* card backgrounds */
    --gold: #c9a84c;          /* primary accent */
    --gold-light: #e2c36e;
    --pearl: #f0ece4;         /* primary text */
    --raspberry: #c0395a;     /* headers, buttons */
    --raspberry-dark: #8b1a35;
    --muted: #888888;         /* NOTE: often too dark — use #b0b0b0 for hint text */
    --success: #2ecc71;
    --warning: #f39c12;
    --danger: #e74c3c;
}
```
- Fonts: Playfair Display (headings), DM Sans (body)
- Card headers: raspberry gradient
- Buttons: `btn-gold` (primary actions), `btn-secondary` (cancel/back)

### CRITICAL THEME RULES — Never Violate
1. NEVER use `class="text-muted"` — use `style="color: #b0b0b0"` instead
2. NEVER use `color: var(--muted)` for hint text — `--muted` (#888888) is invisible on dark bg
3. NEVER use Bootstrap bg classes on cards: `bg-light`, `bg-primary`, `bg-success`, `bg-dark`, `bg-white`
4. NEVER use `{% bootstrap_field %}` and expect help_text to be visible — base.html has `.form-text { color: #b0b0b0 !important }` to fix this globally
5. `{% trans 'string' %}` tags must NEVER be line-wrapped by formatters — always one line
6. Save Changes / primary action buttons use `btn-gold`, never `btn-primary`
7. `card bg-dark border-secondary` → use `style="background: rgba(255,255,255,0.03); border: 1px solid #3a3a3a;"`

---

## Coding Preferences (Roy's Requirements)
- **Always output complete files** — never use `...`, `# unchanged`, `# rest of code` placeholders
- **One file at a time** — show result, state what changed, then move to next
- **Never truncate** — complete every file fully before stopping
- **No Django template formatters** — Prettier breaks `{% trans %}` tags across lines
- Explain new Django concepts when introducing them

---

## Features Built (Complete)

### Core Inventory
- Stock list with filters (store, status)
- Add transaction (receipt/issue) with invoice numbers
- Transaction history with Excel export
- Quick Sell POS (cart-based, M-Pesa/cash)

### Analytics & Reporting
- Sales & P&L dashboard with Chart.js (daily bar chart, top items)
- Analytics page with ETS/Holt-Winters demand forecasting
- Break-even analysis (supports pre-app history)
- Capital investments tracker

### Business Management
- Multi-store support
- Staff management (add, edit, remove, password reset)
- Role-based access (owner/staff/rider/supplier)
- Business settings (Edit Business form with Leaflet map, delivery tiers)

### Compliance System (Phase 1 — Complete)
- 182+ requirements across 60+ business types
- BusinessTypeRequirement / BusinessCompliance models
- Compliance checklist with score ring, progress bar
- Tier system: micro / semi-formal / formal

### Supply Chain
- Supplier portal (supplier_dashboard, browse_businesses)
- Rider portal (rider_dashboard, delivery management)
- Procurement system (POs, bids, bid scoring engine)
- Supplier applications and approval workflow

### Payments & M-Pesa
- Payment settings (Till, Paybill, Pochi la Biashara, Personal M-Pesa)
- Payment prompts (confirm/dismiss incoming M-Pesa payments)
- STK Push integration

### Customer & Orders
- Customer management
- Order management and fulfillment
- Delivery assignment to riders

### Other
- Notification system (bell icon, polling every 30s)
- Multi-language support (i18n, language switcher modal)
- PWA (manifest, service worker, install banner)
- Onboarding tutorial overlay (role-specific, 4 role variants)
- Feedback/reviews system
- County/Sub-county/Ward seeded geography

---

## Features Built — NOT YET DEPLOYED (files ready, need git push)

### Supplier Prerequisites (all files in repo, pending push)
Files to add/integrate:
- `core/models.py` — add SupplierRequirement + SupplierCompliance models at bottom
- `core/views_supplier_compliance.py` — new views file (complete)
- `core/urls.py` — add supplier compliance URL patterns
- `core/admin.py` — register SupplierRequirement + SupplierCompliance
- `templates/supplier_compliance/requirements_list.html`
- `templates/supplier_compliance/requirement_form.html`
- `templates/supplier_compliance/compliance_dashboard.html`
- `templates/supplier_compliance/compliance_detail.html`
- `templates/supplier_compliance/compliance_update.html`
- Run: `python manage.py makemigrations core --name supplier_prerequisites && python manage.py migrate`
- Optional seed: `python manage.py shell < 6_seed_supplier_requirements.py`

---

## Pending Features (Not Yet Built — Priority Order)

### 1. Receipt of Goods — Variable Pricing (NEXT)
When supplier delivers goods, actual price on delivery note often differs from PO price.
Requirements:
- Accept actual delivered price per line item (not just PO price)
- Record variance (delivered price vs PO price) — visible to owner
- Option to update item `cost_price` when owner accepts delivery
- Handle partial deliveries (some items delivered, others pending)
- Affects: Transaction model or new GoodsReceipt model, PO views, receipt template

### 2. Yield-Based Items
For businesses with waste/yield factors:
- Butchery: animal carcass → yield of sellable cuts (e.g. 100kg cow → 65kg cuts)
- Keg bar reconciliation: keg volume → expected vs actual pints sold
- Requires: yield_factor field on Item, yield tracking on transactions

### 3. County-Level Sales Heatmap
Visual map showing sales by county.
BLOCKER: Requires adding `county` FK to Customer and Order models first.
Steps:
1. Add `county` FK to Customer model
2. Add `county` FK to Order model (or derive from customer)
3. Migration
4. Build heatmap view using Leaflet.js + Kenya GeoJSON

---

## Environment & Deployment
- Platform: Render (web service + PostgreSQL)
- Static files: WhiteNoise
- Database URL: via `dj-database-url` from environment variable
- `reset_superuser` management command runs on every deploy
- GitHub repo: https://github.com/mwendwaroy-Angoo/inventory-app

---

## Important Patterns

### Multi-tenancy
Every queryset must be scoped to `request.user.userprofile.business`.
Never query without business filter in views. Example:
```python
items = Item.objects.filter(business=request.user.userprofile.business)
```

### AJAX Dropdown Pattern
County → Sub-county → Ward cascade uses:
```javascript
fetch(`/business/ajax/subcounties/?county_id=${countyId}`)
fetch(`/business/ajax/wards/?sub_county_id=${scId}`)
```

### Notification Pattern
```python
Notification.objects.create(
    business=business,
    user=user,
    message="...",
)
# QuerySet: user.app_notifications.filter(is_read=False)
```

### Template Structure
All templates extend `base.html`.
Block structure:
```html
{% extends "base.html" %}
{% block title %}Page Title{% endblock %}
{% block extra_css %}<style>...</style>{% endblock %}
{% block content %}...{% endblock %}
{% block extra_js %}<script>...</script>{% endblock %}
```

---

## Known Issues / Watch Points
- `Store.__str__` must handle null business (causes crashes if not guarded)
- SQLite → PostgreSQL migration completed — no SQLite-specific queries
- `Notification` model uses `related_name='app_notifications'` — use this, not default
- `UnboundLocalError` risk if imports are scattered — keep all imports at top of views files
- Bootstrap `.form-text` is invisible without the global override in base.html
- `{% trans %}` tags break if any formatter (Prettier etc.) wraps them across lines

---

## Session History Summary
Built together from scratch over multiple sessions covering:
- Full inventory system, POS, analytics
- Compliance system (182+ Kenya-specific requirements)
- Supply chain, procurement, bid scoring
- Supplier and rider portals
- M-Pesa payments integration
- Complete dark luxury theme with visibility fixes
- Supplier prerequisites feature (ready to deploy)
- Multiple template visibility bug fixes (Bootstrap class conflicts on dark theme)

Next session should start with: **Receipt of Goods — Variable Pricing**