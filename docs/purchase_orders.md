Purchase Orders & Reorder Recommendations

Overview

This project adds basic purchase order (PO) support and demand-driven reorder recommendations to help manage supply shortages and surplus.

Key concepts

- `lead_time_days` (Item): expected supplier lead time in days. Default 7.
- `safety_days` (Item): safety stock expressed as days of cover. Default 2.
- Average daily demand: computed from `Issue` transactions over the last 30 days by default.
- Lead-time demand: `avg_daily * lead_time_days`.
- Safety stock: `avg_daily * safety_days`.
- Reorder point (ROP): `lead_time_demand + safety_stock`.
- Target stock: `ROP + reorder_quantity`.
- `on_order`: quantity currently on open POs (statuses: draft, ordered, part_received).
- Recommended order qty: `max(reorder_quantity, target_stock - (on_hand + on_order))`.

How to use

- Owners can set `lead_time_days` and `safety_days` per item in the Item edit form.
- From an Item detail page, owners can click "Create PO" to make a draft PO prefilled with the recommended quantity.
- Use the Purchase Orders section to view, edit, and receive POs.
- A management command `generate_reorder_recommendations` prints recommendations and can optionally create draft POs.

Commands

```bash
# Show recommendations for all businesses
python manage.py generate_reorder_recommendations

# Create draft POs for recommendations (use with caution)
python manage.py generate_reorder_recommendations --create-draft
```

Notes & next steps

- The current safety-stock is a simple heuristic (days of cover). You can improve it later using demand variability (standard deviation) and service-level z-scores.
- Consider adding supplier-specific lead times, minimum order quantities (MOQs), and price breaks for better procurement decisions.
