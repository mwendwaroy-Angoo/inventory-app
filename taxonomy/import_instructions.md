# Taxonomy & Product Import Instructions — Supermarket

Files produced:
- [taxonomy/supermarket_taxonomy.csv](taxonomy/supermarket_taxonomy.csv)
- [data/supermarket_sample_import.csv](data/supermarket_sample_import.csv)

Summary
- Purpose: import a hierarchical taxonomy and product SKU data into the Inventory App.
- Two CSVs: taxonomy (categories) and products (SKUs). Import taxonomy first, then map products to categories.

Prerequisites
- Backup your database before running any import.
  - SQLite (local dev): copy the file `db.sqlite3`.
    ```powershell
    copy .\db.sqlite3 .\db.sqlite3.bak
    ```
  - Postgres example:
    ```bash
    pg_dump -U <user> -h <host> -Fc <dbname> > ~/db_backup.dump
    ```
- Files must be UTF-8 encoded, comma-separated, with a header row.

Taxonomy CSV specification (`taxonomy/supermarket_taxonomy.csv`)
- Required columns: `Level1`, `Level2`, `Level3`, `SuggestedCode`, `Description`.
- Rules:
  - `Level1` is mandatory. `Level2`/`Level3` may be empty when not applicable.
  - `SuggestedCode` must be unique across the file (recommended format: UPPER-ALPHA segments with hyphens, e.g., `PROD-FRT-BR`).
  - `Description` is optional free text.

Product import CSV specification (`data/supermarket_sample_import.csv`)
- Expected columns: `product_id`, `sku`, `name`, `level1`, `level2`, `level3`, `suggested_code`, `tags`, `price`, `cost`, `supplier`.
- Rules:
  - `sku`: required, unique per product, max length 50. If a SKU exists in DB, decide whether to update or skip.
  - `name`: required, max length 255.
  - `price`: required, positive decimal (two decimal places recommended).
  - `cost`: required, >= 0.
  - `suggested_code`: should match `SuggestedCode` in taxonomy when present; used first for mapping.
  - `tags`: semicolon- or comma-separated; normalized to lower-case (e.g., `organic;local`).
  - `product_id`: optional; can be used for internal mapping if relevant.

Validation rules (minimal set)
- Header presence: fail if required columns missing.
- Unique SKU: duplicates inside CSV => error; duplicates against DB => either update or flagged (configurable).
- Category mapping: try in this order:
  1. Match by `suggested_code` to taxonomy `SuggestedCode`.
  2. Fallback to exact match of `level1`+`level2`+`level3` strings.
  3. If no match, mark as `UNMAPPED` and report; optionally create a placeholder category for manual review.
- Numeric checks: `price` > 0, `cost` >= 0.
- Age-restricted: if `level1`/`level2` indicates alcohol or `tags` contains `age_restricted`, ensure the product is flagged accordingly in system.

Error handling and reporting
- Run import in `preview` mode first: validate entire file and generate `errors.csv` with rows and error messages.
- On successful preview, run `commit` mode to persist changes.
- `errors.csv` columns suggestion: `row_number`, `sku`, `error_details`.

Recommended import workflow
1. Backup DB.
2. Validate taxonomy file locally (header + SuggestedCode uniqueness).
3. Import taxonomy into `Category` table (see model suggestion below).
4. Validate product CSV in preview mode.
5. Resolve `errors.csv` issues and remap unmapped items.
6. Commit product import.

Category model suggestion (Django)
```python
class Category(models.Model):
    code = models.CharField(max_length=50, unique=True)   # SuggestedCode
    level1 = models.CharField(max_length=100)
    level2 = models.CharField(max_length=100, blank=True, null=True)
    level3 = models.CharField(max_length=100, blank=True, null=True)
    parent = models.ForeignKey('self', null=True, blank=True, on_delete=models.CASCADE)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        indexes = [models.Index(fields=['code']), models.Index(fields=['level1'])]
```

Product mapping notes
- Add/ensure `category = ForeignKey(Category)` on your product/inventory model.
- Keep `tags` as a JSON/text field or a separate `Tag` model; normalize tags on import.

Category mapping algorithm (pseudocode)
1. If `suggested_code` provided and exists in Category.code => map.
2. Else if exact Level1/Level2/Level3 match to Category fields => map.
3. Else add to `unmapped` list for manual review.

Preview vs commit modes
- Preview mode: read CSV, validate all rows, produce `errors.csv` and `unmapped.csv`. No DB changes.
- Commit mode: perform insert/update within a transaction; on failure roll back and log error details.

Quick validation checklist
- Required headers present.
- No duplicate SKUs in CSV.
- SuggestedCode references existing category codes.
- Prices and costs numeric and in-range.
- Tags normalized and limited to allowed list (optional enforcement).

Post-import QA
- Confirm category counts match taxonomy.
- Sample product spot-check (20 products across departments).
- Run category sales/stock report to verify rollups.

Next steps I can take for you
- Implement a Django management command to import taxonomy and products (preview + commit modes).
- Create `Category` model migration and add `category` FK to product model.
- Add admin bulk-import UI for taxonomy and product CSVs.

---
If you want, I can implement the management commands and model changes next — shall I proceed to add `Category` model and import commands? 
