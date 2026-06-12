# Duka Mwecheche — Digital Receipts & Barcode Support
## Implementation Spec for Claude Code — Sprints 7 & 8

> **Sequencing rule:** Do NOT start until BAR_MODULE_SPEC.md Sprints 1–5 are merged.
> Both features modify Quick Sell, which those sprints are actively restructuring.
> All CLAUDE.md rules apply (theme, complete files, multi-tenancy, Resend-only email,
> float×Decimal casting, decorators directly above views).

---

## SPRINT 7 — Digital Receipts

### 7.0 Positioning (read before coding)
- These are BUSINESS receipts for accountability, NOT KRA tax invoices. Never label
  them "tax invoice" or "ETR receipt" anywhere in the UI. Footer text:
  "Risiti ya biashara — si ankara ya kodi (eTIMS)."
- However, design the numbering and data capture to be eTIMS-ready: KRA's OSCU/VSCU
  API integration is a planned future sprint, and all Kenyan businesses (VAT or not)
  are now required to issue electronic tax invoices. When we integrate, each Receipt
  gains KRA fields (CU invoice number, QR payload) — leave a JSONField for it now.

### 7.1 Models (migration 0045_receipts)

```python
class Business:  # new fields
    receipt_counter = models.PositiveIntegerField(default=0)   # gap-free sequence
    receipts_enabled = models.BooleanField(default=True)
    receipt_sms_enabled = models.BooleanField(default=False,
        help_text='SMS receipts cost ~KES 1 each via Africa\'s Talking. Off by default.')
    receipt_footer = models.CharField(max_length=160, blank=True,
        help_text='e.g. "Asante! Karibu tena." Shown at the bottom of every receipt.')

class Receipt(models.Model):
    business    = models.ForeignKey(Business, on_delete=models.CASCADE, related_name='receipts')
    store       = models.ForeignKey(Store, null=True, blank=True, on_delete=models.SET_NULL)
    receipt_no  = models.PositiveIntegerField()       # per-business sequential
    token       = models.CharField(max_length=22, unique=True, db_index=True)
                  # secrets.token_urlsafe(16) — public URL key, NOT the pk
    lines       = models.JSONField()
                  # SNAPSHOT at sale time: [{"desc","qty","unit","unit_price","amount"}]
                  # Snapshot, not FK joins — receipts must never change if an item
                  # is renamed or repriced later. That is the whole point of a receipt.
    total       = models.DecimalField(max_digits=12, decimal_places=2)
    payment_method = models.CharField(max_length=10)            # cash/mpesa/credit
    mpesa_ref   = models.CharField(max_length=20, blank=True)   # optional, typed by staff
    customer_name  = models.CharField(max_length=80, blank=True)
    customer_phone = models.CharField(max_length=20, blank=True)
    customer_email = models.EmailField(blank=True)
    sent_sms_at    = models.DateTimeField(null=True, blank=True)
    sent_email_at  = models.DateTimeField(null=True, blank=True)
    issued_by   = models.ForeignKey(User, null=True, on_delete=models.SET_NULL)
    shift       = models.ForeignKey('Shift', null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name='receipts')
    created_at  = models.DateTimeField(auto_now_add=True)
    voided_at   = models.DateTimeField(null=True, blank=True)
    void_reason = models.CharField(max_length=120, blank=True)
    etims_data  = models.JSONField(null=True, blank=True)   # reserved for KRA integration

    class Meta:
        unique_together = [('business', 'receipt_no')]
        ordering = ['-created_at']

class Transaction:  # new field
    receipt = models.ForeignKey('Receipt', null=True, blank=True,
                                on_delete=models.SET_NULL, related_name='transactions')
```

**Gap-free numbering (the accountability core):** allocate inside the sale's
atomic block:
```python
with db_transaction.atomic():
    biz = Business.objects.select_for_update().get(pk=business.pk)
    biz.receipt_counter += 1
    biz.save(update_fields=['receipt_counter'])
    receipt = Receipt.objects.create(receipt_no=biz.receipt_counter, ...)
```
Voided receipts keep their number (voided_at + reason) — gaps are what crooked
books look like; voids with reasons are what honest books look like.

### 7.2 Issuing flow
- One Receipt per CART (Quick Sell checkout) covering all its Transactions; one
  per single Add-Transaction Issue. Bar tab settlement: receipt generated at
  TICK time covering the entries just paid (description lines from BarTabEntry).
- Post-sale screen (and a "Risiti" button on each history row for reissue):
  - Big QR code + short URL of the public receipt page (FREE channel, default)
  - "Tuma SMS" (visible only if receipt_sms_enabled): phone input prefilled from
    linked Customer if any → normalize_ke_phone → Africa's Talking. Message:
    `RISITI #1042 {Business}: {n} items KES {total} via {method}.
    Tazama: {short_url} {footer}` — keep ≤160 chars, NO bundling window
    (receipts are transactional, send immediately; do not touch last_txn_sms_at)
  - "Tuma Email" → Resend API ONLY (never send_mail), HTML template
  - "Print" → opens print view
- Watch the AT `UserInBlacklist` Sender-ID issue — surface a friendly error and
  log; do not crash checkout if SMS fails. Receipt creation must never depend on
  delivery success.

### 7.3 Public receipt page (no login)
`/r/<token>/` — themed but light-background for printing/screenshots.
Shows: business name, location, receipt no, datetime, lines table, total,
payment method, served-by first name, footer, "Powered by Duka Mwecheche"
(free marketing on every receipt — this is your growth loop, Roy).
Rate-limit + 404 on bad token. No enumeration possible (token, not pk).

### 7.4 Print template (the "receipt printer" answer)
Same page with `@media print` CSS sized for 58mm and 80mm thermal rolls
(width toggle saved per business): monospace-ish stack, no colors, large total,
dashed separators, auto `window.print()` when opened via the Print button.
This works TODAY with any thermal printer paired to the phone/PC through the
browser/OS — zero device integration code. Direct Web-Bluetooth ESC/POS is
explicitly OUT of scope (flaky, Chrome-Android-only); revisit only on demand.

### 7.5 Business copy & accountability views
- `/receipts/` list: date/store/staff/payment filters, daily totals, Excel export
  (openpyxl, iterator(chunk_size=10))
- Shift reconciliation report (Sprint 4 output) gains one line:
  "Receipts issued: 41 / Sales recorded: 43 ⚠" — unreceipted sales per shift is
  exactly the accountability number the bar owner asked for.
- Owner notification rule (NotificationRouter): receipt voided → notify owner.

---

## SPRINT 8 — Barcode Support

### 8.0 Reality check (read before coding)
USB & Bluetooth barcode scanners are HID keyboards. There is NO pairing,
SDK, or driver work: the scanner "types" the code followed by Enter into
whatever input has focus. The entire feature is therefore:
(a) a barcode field on Item, (b) scan-aware inputs, (c) optional camera
scanning for owners with no hardware at all.

### 8.1 Model (migration 0046)
```python
class Item:  # new field
    barcode = models.CharField(max_length=64, blank=True, db_index=True)
    class Meta:
        constraints = [models.UniqueConstraint(
            fields=['business', 'barcode'], name='uniq_barcode_per_business',
            condition=~models.Q(barcode=''))]
```
Store EAN-13/UPC/Code128 as plain digits/text. Same product may have different
barcodes per business (repacked goods) — hence per-business uniqueness, never global.

### 8.2 Quick Sell integration
- Grid boards get a scan box: a visually-styled input pinned above the grid,
  auto-focused, with a keydown listener. Scanner behaviour = burst of chars +
  Enter in <100ms; on Enter: exact barcode lookup (business-scoped) →
  found: add to cart with the flash/beep cart animation, clear box;
  not found: gold toast "Bidhaa haijasajiliwa — sajili?" linking to item form
  with ?barcode= prefilled.
- Keyboard-wedge gotcha: ensure modals/other inputs don't steal focus; clicking
  anywhere non-input refocuses the scan box (document-level handler, but never
  steal focus FROM another input the user is typing in).
- 📷 button: camera scanning via html5-qrcode (CDN, ~free). Works on phones —
  the zero-hardware path for mama mboga with packaged goods. Decode → same
  lookup path. Feature-detect camera; hide button if unavailable.

### 8.3 Item form integration
- Barcode field with the same 📷 camera capture button and scanner-friendly focus.
- On blur/scan: uniqueness check via small JSON endpoint; if the code already
  belongs to another item, show its name (probable duplicate entry — offer to
  open that item instead).
- Catalog synergy (Sprint 5): catalog entries MAY carry known EAN-13s for
  branded goods (sodas, spirits, cigarettes) — when present, picking the
  catalog item prefills the barcode too. Populate the bar/liquor catalog codes
  opportunistically; leave blank where unknown (do NOT invent codes).

### 8.4 Out of scope (documented so Claude Code doesn't wander)
- Label PRINTING / generating barcodes for unbranded items — future sprint.
- Global product database lookup (Open Food Facts etc.) — future, offline-hostile.
- Inventory count / stocktake by scanning — natural follow-up, not now.

---

## Test checklist for both sprints
- Receipt numbers strictly sequential per business under concurrent checkouts
  (two simultaneous carts must not collide — select_for_update proves itself)
- Reissued receipt shows ORIGINAL data even after item renamed/repriced
- SMS failure does not block checkout; email only ever via Resend
- Public receipt URL works logged-out; pk-based guesses 404
- Scanner Enter-burst adds item in under one second on a mid-range Android
- Camera scan works on claude— on Chrome Android + Safari iOS
- Staff (Morrine) can issue receipts but cannot void; owner voids notify owner
- Non-bar, non-retail businesses: zero UI change unless receipts_enabled
