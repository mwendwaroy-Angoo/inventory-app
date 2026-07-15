# Bar Module Sprint Test & Verification Guide

**Scope:** F1 → F6 (plus B0). Use this to verify the full bar sprint sequence on the live app after deployment.

---

## Automated Tests

Run with:
```
python manage.py test
```

Expected: **117 tests, 0 failures** (84 original + K3/K4/SG/K5/K6/DJ1/DJ3 additions; pre-existing 301 trailing-slash failures in K2a–K6 tests are known and non-blocking).

### Full test list (`core/tests.py`)

| Class | Test | Sprint |
|---|---|---|
| `MpesaUrlRoutingTest` | `test_sandbox_url_contains_sandbox_domain` | F1/B0 |
| `MpesaUrlRoutingTest` | `test_production_url_contains_api_domain` | F1/B0 |
| `MpesaUrlRoutingTest` | `test_none_falls_back_to_global_mpesa_env` | F1/B0 |
| `MpesaUrlRoutingTest` | `test_sandbox_and_production_urls_are_distinct` | F1/B0 |
| `MpesaUrlRoutingTest` | `test_stk_push_hits_sandbox_cluster_when_env_sandbox` | F1/B0 |
| `MpesaUrlRoutingTest` | `test_stk_push_hits_production_cluster_when_env_production` | F1/B0 |
| `MpesaUrlRoutingTest` | `test_query_stk_status_hits_correct_cluster` | F1/B0 |
| `MpesaUrlRoutingTest` | `test_oauth_token_fetched_from_correct_cluster` | F1/B0 |
| `ReceiptNumberingTest` | `test_receipts_are_numbered_from_one` | F1 |
| `ReceiptNumberingTest` | `test_receipts_are_sequential` | F1 |
| `ReceiptNumberingTest` | `test_receipt_numbers_are_per_business` | F1 |
| `ReceiptNumberingTest` | `test_receipt_tokens_are_unique` | F1 |
| `TabStkSettlementClearsDebtTest` | `test_stk_settlement_clears_debt` | F1 |
| `VoidTabClearsDebtTest` | `test_void_tab_clears_debt_and_not_revenue` | F1 |
| `ConvertTabToDebtWithDuplicateCustomersTest` | `test_duplicate_customers_do_not_raise` | F1 |
| `ConcurrentKegSalesDoNotLoseUpdatesTest` | `test_sequential_locked_sales_accumulate_correctly` | F1 |
| `LeaderboardLossAggregatedInKesTest` | `test_loss_is_sum_not_average` | F2 |
| `CoveragePctCorrectTest` | `test_coverage_pct_with_one_measured_window` | F2 |
| `DangerShiftCloseCreatesNotificationTest` | `test_danger_close_triggers_alert` | F2 |
| `TinyVolumeSpotDoesNotAlertTest` | `test_small_spot_no_notification` | F2 |
| `HandoverMismatchCreatesNotificationTest` | `test_overnight_loss_creates_notification` | F2 |
| `AlertsMutedWhenDisabledTest` | `test_muted_business_gets_no_notification` | F2 |
| `BaselineNotLearnedBelowMinSampleTest` | `test_returns_default_when_too_few_samples` | F3 |
| `BaselineLearnedAtMinSampleTest` | `test_baseline_pct_is_mean_of_loss_pcts` | F3 |
| `BaselineCachedOnDepletedTest` | `test_close_depleted_updates_business_cache` | F3 |
| `BaselineExcludesUnderTargetBarrels` | `test_low_revenue_barrel_not_counted` | F3 |
| `ZReportDrawerMathTest` | `test_expected_cash_formula` | F4 |
| `ZReportDrawerMathTest` | `test_variance_when_counted` | F4 |
| `ZReportOpenTabsTest` | `test_open_tabs_appear_in_context` | F4 |
| `BottleExpectedRevenueTest` | `test_expected_revenue_per_unit_uses_avg_preset` | F5 |
| `BottleExpectedRevenueTest` | `test_expected_revenue_falls_back_to_selling_price_when_no_presets` | F5 |
| `BottleShrinkageLeaderboardTest` | `test_bottle_loss_included_in_leaderboard` | F5 |
| `BottleShrinkageLeaderboardTest` | `test_surplus_count_does_not_add_to_bottle_loss` | F5 |

*(9 additional tests exist for M-Pesa URL routing edge cases — all in `MpesaUrlRoutingTest`.)*

**K1 — Source-scoped debt (3 tests)**

| Class | Test |
|---|---|
| `DebtPaymentSourceFieldTest` | `test_source_defaults_to_bar` |
| `DebtPaymentSourceFieldTest` | `test_source_accepts_kitchen` |
| `DebtPaymentSourceFieldTest` | `test_filter_by_source_partitions_ledger` |
| `DebtScopeHelperTest` | `test_owner_gets_all_scope` |
| `DebtScopeHelperTest` | `test_kitchen_staff_gets_kitchen_scope` |
| `DebtScopeHelperTest` | `test_no_kitchen_business_gets_all_scope` |

**K2a — Per-counter M-Pesa resolver (6 tests)**

| Class | Test |
|---|---|
| `ResolveMpesaConfigTest` | `test_no_override_returns_business_config` |
| `ResolveMpesaConfigTest` | `test_store_override_returns_store_config` |
| `ResolveMpesaConfigTest` | `test_no_store_returns_business_config` |
| `ResolveAccountByShortcodeTest` | `test_finds_store_shortcode_first` |
| `ResolveAccountByShortcodeTest` | `test_falls_back_to_business_shortcode` |
| `ResolveAccountByShortcodeTest` | `test_unknown_shortcode_returns_none` |

**H1-H4 — Haki module (6 tests)**

| Class | Test |
|---|---|
| `SalaryPaymentModelTest` | `test_salary_payment_created_and_unique` |
| `SalaryPaymentModelTest` | `test_days_overdue_is_positive_when_past_due` |
| `SalaryPaymentModelTest` | `test_days_overdue_is_zero_when_paid` |
| `HakiRecognitionNudgeTest` | `test_milestone_creates_notification` |
| `HakiRecognitionNudgeTest` | `test_duplicate_milestone_not_re_notified` |
| `HakiRecognitionNudgeTest` | `test_no_milestones_no_notification` |

**K3 — Credit Discipline Gate (10 tests)**

| Class | Test |
|---|---|
| `CreditGatePolicyOffTest` | `test_policy_off_allows_any_customer` |
| `CreditGateApprovalTest` | `test_unapproved_customer_is_blocked` |
| `CreditGateApprovalTest` | `test_approved_customer_with_no_history_is_ok` |
| `CreditGateDefaulterTest` | `test_defaulter_permanently_blocked` |
| `CreditGateDefaulterTest` | `test_non_defaulter_not_blocked_by_flag` |
| `CreditGateMonthlyMidMonthTest` | `test_rolling_biz_ignores_monthly_cutoff` |
| `CreditGateMonthlyMidMonthTest` | `test_monthly_biz_blocks_at_month_end` |
| `CreditGateMonthlyMidMonthTest` | `test_monthly_biz_allows_mid_month` |
| `CreditGateCreditLimitTest` | `test_at_limit_is_blocked` |
| `CreditGateCreditLimitTest` | `test_below_limit_is_allowed` |

**K4 — Customer-Facing Accountability Receipts (11 tests)**

| Class | Test |
|---|---|
| `ReceiptMetaFieldTest` | `test_issue_with_no_meta_creates_empty_dict` |
| `ReceiptMetaFieldTest` | `test_issue_stores_meta_dict` |
| `ReceiptMetaFieldTest` | `test_cash_receipt_has_no_credit_score` |
| `BuildCreditReceiptMetaTest` | `test_no_debt_returns_new_score` |
| `BuildCreditReceiptMetaTest` | `test_credit_sale_outstanding_reflects_db_state` |
| `BuildCreditReceiptMetaTest` | `test_scope_bar_excludes_kitchen_debt` |
| `CreditReceiptWarnTierTest` | `test_near_limit_triggers_warn` |
| `CreditReceiptWarnTierTest` | `test_well_within_limit_no_warn` |
| `CustomerDebtStatementViewTest` | `test_statement_creates_receipt_with_meta` |
| `CustomerDebtStatementViewTest` | `test_statement_is_scope_correct` |
| `CustomerDebtStatementViewTest` | `test_no_statement_when_no_outstanding` |

---

## Manual Smoke Tests on the Live App

Log in as **RoyMwendwa** (owner) at https://www.dukamwecheche.co.ke. Staff test account: **Morrine**.

---

### Sprint F1 — Money & data-integrity fixes

**F1-1: STK settlement clears debt**
1. Bar Board → Open a tab for "TestKamau" with a drink.
2. Tabs drawer → click "📲 STK Push" on that tab → enter phone → submit.
3. Wait for STK prompt on the test phone → pay KES 1 (use a real test).
4. After settlement: go to Debt Tracker → search "TestKamau".
- ✅ Correct: balance = 0 (or the tab amount exactly, no phantom open credit).
- ❌ Bug if: debt shows the full tab amount as unpaid even after STK settled.

**F1-2: Void tab — no ghost debt, no revenue**
1. Bar Board → open a tab for "TestOtieno" with a drink.
2. Tabs drawer → Void the tab.
3. Check Debt Tracker for "TestOtieno" → should not exist or show 0.
4. Check Today's revenue on home dashboard → should not include voided amount.
- ✅ Correct: zero debt, zero revenue bump.
- ❌ Bug if: Debt Tracker shows the voided tab as an open credit.

**F1-3: Concurrent keg sales — no lost updates**
*(Automated test covers this — manual verification is optional.)*
1. Open the bar board on two browser tabs simultaneously.
2. Ring up pint sales rapidly from both.
3. Check KegBarrel.revenue_collected in Django admin → should equal the sum of all sales.

---

### Sprint B0 — keg_metrics.py centralised math

**B0-1: Reconciliation numbers unchanged**
1. Go to `/bar/reconciliation/` → pick any closed barrel.
2. Note the Wastage %, Wastage KES, and Book vs Scale figures.
3. Open Barrel Detail → check per-shift variance.
- ✅ Correct: numbers are identical to what they were before B0 (no rounding difference).

---

### Sprint F2 — Shrinkage leaderboard + push alerts

**F2-1: Danger variance fires alert**
1. Open a shift. Weigh a barrel mid-shift (bar board → weigh barrel).
2. Set the weight low enough to create > tolerance% variance on ≥ 5 L dispensed.
3. Check: the owner's in-app notification bell → should show a "Keg Variance Alert".
4. If SMS is live, check the owner's phone for the alert.
- ✅ Correct: alert message includes barrel name, KES gap, and "kagua mara moja".
- ❌ Bug if: no notification despite danger flag.

**F2-2: Tiny-volume spot does NOT fire alert**
1. Tap a new barrel. Record a SPOT weigh on a barrel with < 5 L dispensed.
2. Even if variance % is high, no alert should appear.
- ✅ Correct: no notification.

**F2-3: Leaderboard shows KES loss, not % average**
1. Go to `/bar/shrinkage/` (owner only).
2. Confirm "Keg Loss" column is in KES, not a per-barrel average percentage.
3. Any staff with coverage < 60% should show "partially measured" badge.
- ✅ Correct: loss is in KES; low-coverage rows are visually marked.

**F2-4: Muting suppresses alerts**
1. In Django admin → Business → set `keg_alerts_enabled = False`.
2. Trigger a danger variance → no notification should appear.
3. Re-enable.

---

### Sprint F3 — Learned foam/spillage baseline

**F3-1: Still-learning state**
1. Go to `/bar/reconciliation/`.
2. If fewer than 3 depleted barrels exist, the card header should read "Baseline: 10% (still learning — N/3)".
- ✅ Correct: learning state visible, not silently showing 10% as if learned.

**F3-2: Learned baseline after 3 depleted barrels**
1. Mark at least 3 barrels as DEPLETED (each with revenue ≥ 95% of target and at least one weigh-in).
2. Reload `/bar/reconciliation/` → header should read "Baseline: X% (learned from N barrels)".
3. On the Waste % column, check the inline chip: ▲ (over baseline), ✓ (at or below).
- ✅ Correct: baseline is the mean loss% of the honest depleted barrels, not a flat 10%.

**F3-3: Barrel detail shows vs-baseline row**
1. Open any barrel detail page.
2. Spillage card should include a "vs Learned baseline" row.
- ✅ Correct: row appears with signed deviation.

---

### Sprint F4 — End-of-night Z-report

**F4-1: Drawer math**
1. Open a shift with float of KES 2,000.
2. Ring up KES 500 cash + KES 300 M-Pesa.
3. Close the shift (count KES 2,400 in drawer).
4. Go to `/bar/z-report/`.
- ✅ Correct: Expected drawer = 2000 + 500 = 2500; Variance = 2400 − 2500 = −100 (shown in raspberry).

**F4-2: Open tabs highlighted**
1. Leave a tab open (do not settle before checking Z-report).
2. Go to `/bar/z-report/` → Open Tabs KES tile should be amber and non-zero.

**F4-3: Date navigation**
1. Use the ← Prev / Next → / Today links → confirm the page reloads for the correct date.

**F4-4: Share SMS**
1. As owner, click "📱 Share SMS" → a toast confirmation should appear.
2. Check owner's phone for the summary SMS.
- ✅ Correct: "Z-Report YYYY-MM-DD — [Biz name]\nJumla: KES X..." received.

---

### Sprint F5 — Bottle & spirits revenue envelope

**F5-1: Enable a spirit as bottle envelope**
1. Stock list → edit "Whiskey 750ml" → Spirits Accountability section → tick "Track per-bottle shrinkage".
2. Set Vol = 750 ml, Tot = 25 ml → Tots per bottle should auto-fill to 30.
3. Save.
- ✅ Correct: item saved with bottle_envelope=True, tots_per_unit=30.

**F5-2: Shift close stock count — variance in KES**
1. Open a shift. During the shift, simulate selling from the bottle.
2. Close shift → stock take modal → for Whiskey, enter an actual count lower than the book.
3. The result should show a `variance_kes` for the whiskey line.
- ✅ Correct: missing bottles × (30 tots × avg preset price) = KES shown.

**F5-3: Leaderboard includes bottle loss**
1. After a stock count with a shortfall, go to `/bar/shrinkage/`.
2. The "Bottle/Spirits Loss" column should show the KES variance for the responsible staff.
- ✅ Correct: separate column from Keg Loss; total_loss_kes = keg + bottle.

**F5-4: Z-report shows bottle variance**
1. After a shortfall stock count, go to `/bar/z-report/`.
2. The 🍾 Bottle Variance tile should appear (only when > 0).

---

### Sprint F6 — M-Pesa cross-check + eTIMS-ready receipts

**F6-1: M-Pesa cross-check on Z-report**
1. Ensure at least one STK Push payment (`Payment`, method=mpesa, status=completed) exists for today.
2. Also record at least one manual M-Pesa sale (staff ticks M-Pesa at Quick Sell or Bar Board).
3. Go to `/bar/z-report/`.
4. If STK payments exist, the "M-Pesa Cross-Check" card should appear.
- ✅ Correct: "Recorded M-Pesa" vs "STK Push completions" with signed gap.
- ❌ Bug if: card doesn't appear even when STK payments exist.

**F6-2: eTIMS fields render when populated**
1. In Django admin → find a Receipt → set `etims_receipt_no = "TG001234"`.
2. Open `/r/<token>/` for that receipt.
- ✅ Correct: "eTIMS Receipt: TG001234" appears at the bottom of the receipt.
- ❌ Bug if: existing receipts without eTIMS fields show any eTIMS section.

**F6-3: KRA PIN saves from Business Settings**
1. Go to Business Settings (`/accounts/edit/`).
2. Enter `P051234567M` in the KRA PIN field → Save.
3. Go to `/bar/z-report/` → a "KRA PIN: P051234567M" card should appear at the bottom.
- ✅ Correct: PIN saved, card visible on Z-report.

---

### Sprint K1 — Source-scoped debt sub-ledgers

**K1-1: Kitchen staff only sees kitchen debt**
1. Log in as a kitchen staff account.
2. Go to `/debt/` (Debt Tracker).
3. Only debts from kitchen sales (items in `is_kitchen=True` store) should appear.
- ✅ Correct: bar debts are hidden from kitchen staff.
- ❌ Bug if: bar customer debts are listed.

**K1-2: Owner sees dual sub-ledger on customer profile**
1. Log in as RoyMwendwa (owner).
2. Go to Debt Tracker → open any customer who has both bar and kitchen credit sales.
3. The profile should show two separate cards — "🍺 Bar" and "🍗 Kitchen" with separate outstanding balances.
- ✅ Correct: two ledger cards, independent balances.

**K1-3: Payment settles the correct sub-ledger**
1. From a customer profile, click "Lipa" on the Bar sub-ledger card → pay.
2. The Bar outstanding should decrease; Kitchen balance unchanged.
- ✅ Correct: source-scoped settlement.

---

### Sprint K2a — Per-counter M-Pesa resolver

**K2a-1: Kitchen store M-Pesa override routes STK Push correctly**
1. In Business Settings → Kitchen M-Pesa section → enter a separate till number for the kitchen counter.
2. Trigger an STK Push from the kitchen board.
3. The STK Push should use the kitchen till, not the main bar till.
- ✅ Correct: kitchen payment goes to kitchen till.
- ❌ Bug if: STK Push always uses the business-level till regardless of counter.

**K2a-2: No kitchen override falls back to business M-Pesa**
1. Remove the kitchen till override (or leave it blank).
2. Trigger an STK Push from the kitchen board.
3. Should use the business-level M-Pesa config.
- ✅ Correct: graceful fallback.

---

### Sprint H1-H4 — Haki (Staff Fairness Ledger)

**H1-1: Contribution report loads**
1. Log in as owner → Staff dropdown → "🌟 Haki — Staff".
2. Should see a table of all staff with revenue, transaction count, and salary status.
- ✅ Correct: table renders with data.
- ❌ Bug if: 500 error or blank page.

**H2-1: Record salary payment**
1. On the Haki contribution page → click the 💵 Pay button for a staff member.
2. Enter amount, method (cash/mpesa), and period (e.g. 2026-06).
3. Submit.
- ✅ Correct: payment recorded, salary card updates to "✓ Umelipwa", staff receives SMS.
- ❌ Bug if: duplicate payment possible or SMS not sent.

**H3-1: Staff sees Kazi Yangu**
1. Log in as a staff account (Morrine).
2. Navbar → "🙌 Kazi Yangu" link should be visible (only if `haki_enabled=True` on the business).
3. Click it → should see personal contribution stats and salary status.
- ✅ Correct: page loads with the staff's own data only.
- ❌ Bug if: link missing or page shows another staff's data.

**H4-1: Recognition statement**
1. On Haki contribution page → click "🌟 Statement" for a staff member.
2. Page should render a printable statement with contribution metrics.
3. Click "📱 SMS" → staff receives the statement as an SMS.
- ✅ Correct: statement page loads; SMS sent.

**H4-2: Milestone nudge deduplication**
1. Trigger a milestone condition (e.g. staff reaches 100 transactions in the date range).
2. Reload the contribution report → notification appears in owner's bell.
3. Reload again → NO duplicate notification.
- ✅ Correct: milestone fires once per staff per period.

---

### Shift Gate Enforcement (`get_active_staff_shift`)

The `get_active_staff_shift(user_profile, business)` helper in `core/shift_views.py` controls
whether a staff member can perform any action. Return values:
- `None` → caller is owner; skip the gate entirely
- `Shift` object → caller has an open shift; proceed
- `False` → caller is staff with no open shift; block with 403/error

Gates are applied at:
- **Quick Sell** POST checkout — all staff, all business types
- **Add Transaction** — all staff
- **Kitchen checkout** (`_kitchen_checkout`) — kitchen staff
- **Kitchen receive** (`kitchen_receive`) — kitchen staff
- **Bar board** `tick_entry`, `settle_tab`, `convert_tab_to_debt`, `record_breakage`

**SG-1: Kitchen staff cannot use tiles without an open shift**
1. Log in as kitchen staff with no open shift (or close your shift).
2. Open Kitchen Board (`/kitchen/`).
3. Try to tap any food tile to add to cart.
- ✅ Correct: toast "⚠️ Fungua shift yako kwanza kabla ya kuuza." appears; item NOT added.
- ❌ Bug if: item is added to cart and sale proceeds.

**SG-2: Kitchen staff cannot receive stock without an open shift**
1. As kitchen staff with no open shift, open Kitchen Board.
2. Tap "+Pata Stok" (receive modal).
3. Submit a receipt.
- ✅ Correct: 403 response; error shown in modal.
- ❌ Bug if: stock receipt recorded successfully.

**SG-3: Add Transaction blocked without shift**
1. As any staff (not owner), close your shift.
2. Go to `/stock/add/` (Add Transaction).
3. Submit any Receipt.
- ✅ Correct: error message "Fungua shift yako kwanza..." and redirect back.
- ❌ Bug if: transaction recorded.

**SG-4: Quick Sell blocked without shift**
1. As any staff with no open shift, go to Quick Sell.
2. Add item to cart → click Checkout.
- ✅ Correct: redirect to Quick Sell with error message.
- ❌ Bug if: sale completes.

**SG-5: Owner always bypasses all gates**
1. Log in as RoyMwendwa (owner, no active shift needed).
2. Perform any of SG-1 through SG-4 actions.
- ✅ Correct: all actions succeed without needing to open a shift.
- ❌ Bug if: owner is blocked by the shift gate.

**SG-6: Bar board actions blocked without shift**
1. As bar staff with no open shift, go to Bar Board.
2. Try to add a drink (tick_entry), settle a tab, or void a tab.
- ✅ Correct: 403 JSON response `{"ok": false, "shift_required": true}`.
- ❌ Bug if: action completes.

---

## Final Checks

After all smoke tests pass:

```
python manage.py check        # 0 issues
python manage.py makemigrations --check   # No changes detected
python manage.py test         # 51 tests, 0 failures
```

All green = bar sprint sequence + K1/K2a/H1-H4/shift gate/K3 complete.

---

### Sprint K3.A — Kitchen staff in salary/expense lists

**K3A-1: Kitchen staff appears in recurring expense salary list**
1. Log in as RoyMwendwa (owner).
2. Go to `/analytics/expenses/` → Manage Recurring Expenses.
3. Click "Add Salary Line" → the staff dropdown should include any kitchen-role staff (e.g. Morrine if set to kitchen role).
- ✅ Correct: kitchen staff name visible in dropdown.
- ❌ Bug if: dropdown only shows 'staff' and 'waitress' roles, skipping kitchen.

**K3A-2: Kitchen staff appears in Haki contribution report**
1. Go to `/staff/contribution/` → the table should include kitchen-role staff rows.
- ✅ Correct: kitchen staff listed with their shift count and revenue.

---

### Sprint K3.B — Kazi Yangu scorecard parity

**K3B-1: Staff numbers match owner's view**
1. Log in as owner → `/staff/contribution/` → note the revenue figure for a staff member.
2. Log in as that staff member → `/me/` (Kazi Yangu).
3. Their revenue figure should be identical to the owner's view.
- ✅ Correct: same figure to the shilling.
- ❌ Bug if: two different numbers shown.

**K3B-2: Staff can share their own statement**
1. Log in as staff → `/me/` → click "🌟 Taarifa Yangu".
2. Page should load (their own statement) with a "📱 SMS" button.
3. Click SMS → statement SMS sent to their own phone.
- ✅ Correct: statement page loads, SMS sent.
- ❌ Bug if: 403 error or redirected away.

**K3B-3: Staff cannot view another staff member's statement**
1. As staff, manually navigate to `/staff/<other_staff_id>/statement/`.
- ✅ Correct: 403 Forbidden.
- ❌ Bug if: statement of another staff member loads.

---

### Sprint K3.C — Credit Discipline Gate

**K3C-1: Unapproved customer blocked at Quick Sell (deni)**
1. Log in as owner. Go to Quick Sell.
2. Add an item to cart, select "Deni", type a NEW customer name (e.g. "Testjohn").
3. Submit checkout.
- ✅ Correct: error message "Deni haliwezi kutolewa: Mteja huyu hajaruhusiwa..." — sale does NOT go through.
- ❌ Bug if: credit transaction is created for an unapproved customer.

**K3C-2: Owner also cannot bypass the gate at the counter**
1. As owner (RoyMwendwa), repeat K3C-1 with the same new customer name.
- ✅ Correct: SAME block applies — even the owner is blocked at the POS counter.
- ❌ Bug if: owner's credit sale goes through without approval.

**K3C-3: Approve customer → credit then works**
1. After K3C-1, go to Debt Tracker → find "Testjohn" → click "Approve for Credit".
2. Return to Quick Sell → deni sale to "Testjohn" → submit.
- ✅ Correct: credit transaction recorded, debt tracker updated.

**K3C-4: Cash/M-Pesa always available even when credit is blocked**
1. With "Testjohn" still unapproved, go to Quick Sell → add item → select CASH → checkout.
- ✅ Correct: cash sale succeeds without any credit-gate message.

**K3C-5: Overdue customer blocked at bar tab**
1. Give a customer credit → wait (or manually set their transaction date back in Django admin to > 30 days ago).
2. Go to Bar Board → try to open a tab for that customer.
- ✅ Correct: JSON error "Tab imezuiwa: Mteja ana deni la zamani...".
- ❌ Bug if: tab opens for an overdue customer.

**K3C-6: Monthly cutoff blocks on last days of month**
1. In Payment Settings → Sera ya Deni → set Debt Cycle = Monthly, cutoff days = 5.
2. On the 26th+ of a month (5 days before month end for a 30-day month), try to give credit.
- ✅ Correct: "Deni jipya haliwezi kutolewa ndani ya siku 5 za mwisho wa mwezi".
- ❌ Bug if: credit goes through without the monthly-cutoff block.

**K3C-7: Rolling-cycle business unaffected by monthly cutoff**
1. In Payment Settings → set Debt Cycle = Rolling (default).
2. On any day of the month, try to give credit to an approved customer.
- ✅ Correct: no monthly-cutoff block regardless of what day it is.

**K3C-8: Credit limit blocks when outstanding ≥ limit**
1. In Debt Tracker → customer profile → set Credit Limit = KES 500.
2. Give that customer KES 500 credit (Quick Sell deni).
3. Try to give any more credit to the same customer.
- ✅ Correct: "Kikomo cha deni ni KES 500..." error.

**K3C-9: Credit Policy Settings save correctly**
1. Go to Payment Settings → Sera ya Deni section.
2. Toggle "Washa Kinga ya Deni" OFF → click "Hifadhi Sera ya Deni".
3. Reload the page → toggle should be OFF.
4. Toggle back ON and save.
- ✅ Correct: each setting persists correctly after reload.
- Confirm: M-Pesa settings (till, paybill) are NOT erased when saving credit policy.

**K3C-10: Credit standing card on customer profile**
1. Go to Debt Tracker → click any customer.
2. A coloured card "Hali ya Mkopo" should appear below the header buttons.
- ✅ Correct: green "Sawa" for approved/no-block, red "Imezuiwa" for blocked customers with reason.

---

## Final Checks

After all smoke tests pass:

```
python manage.py check        # 0 issues
python manage.py makemigrations --check   # No changes detected
python manage.py test         # 84 tests, 0 failures
```

---

## Sprint K5 — Barrel Depletion, Theft Controls, Shift Gate

### K5.A — Envelope-based depletion (non-weighing bars)

**K5A-1: Non-weighing bar shows "Funga Pipa / Endelea" prompt at envelope boundary**
1. In Payment Settings → Barrel & Keg → ensure "Bar ina Mizani" is OFF.
2. Tap a barrel, sell until revenue hits the target (envelope = 0).
3. Tap the barrel tile again to open the sell modal.
- ✅ Correct: browser `confirm()` prompt "Barrel imefika lengo..." appears.
- Owner clicks OK (Funga Pipa) → barrel disappears from board (DEPLETED).
- Owner clicks Cancel (Endelea) → sell modal opens normally.
- ❌ Bug if: sell modal opens immediately without any prompt.

**K5A-2: Staff see toast (not prompt) at envelope boundary**
1. Log in as staff (not owner), repeat sell until envelope reached.
2. Try to open a TAPPED tile.
- ✅ Correct: toast "Barrel imefika lengo. Mwambie mwenye biashara..." and no modal.

**K5A-3: block_sales_past_target hard-blocks sales**
1. In Django admin → Business → enable "Block Sales Past Target".
2. Sell until envelope reached, then try to sell more (as owner or staff).
- ✅ Correct: toast "⛔ Barrel imefika lengo — mauzo yamezuiwa. Funga barrel kwanza."
- ❌ Bug if: sell modal still opens.

**K5A-4: Funga Pipa creates no wastage transaction**
1. Trigger the "Funga Pipa" confirm on a barrel whose envelope is reached.
2. Go to Stock → Transaction History for that item.
- ✅ Correct: no Wastage transaction created — barrel just becomes DEPLETED.

**K5A-5: Weighing bar shows weight input in tap modal**
1. In Payment Settings → enable "Bar ina Mizani".
2. Tap a SEALED barrel (click "Fungua Barrel" button).
- ✅ Correct: tap modal shows "Uzito wa barrel wakati wa kufungua (kg)" input field.
- Enter a weight and click Fungua.
- ❌ Bug if: weight input is hidden even when weighs_kegs is true.

### K5.B — Light-at-tap theft detection

**K5B-1: Entering weight lighter than gross triggers alert**
1. Enable "Bar ina Mizani". Receive a barrel with gross_weight = 60 kg.
2. When tapping, enter starting_weight = 55 kg (5 kg missing > 2 kg threshold).
- ✅ Correct: owner receives in-app notification "⚠️ [barrel]: pipa limepimwa likiwa pungufu..."
- ❌ Bug if: no notification appears despite > 2 kg discrepancy.

**K5B-2: Small discrepancy (≤ 2 kg) fires no alert**
1. Same setup, but enter starting_weight = 58.5 kg (1.5 kg below gross).
- ✅ Correct: barrel taps without any alert notification.

### K5.C — Void tab attribution on shrinkage leaderboard

**K5C-1: Voided tabs appear on the leaderboard**
1. Open and void a bar tab (as owner, click Void on the tab).
2. Go to /bar/shrinkage/ (Shrinkage Leaderboard) for today's date.
- ✅ Correct: the staff who served the tab shows a "Voids" column entry
  with count and KES total.
- ❌ Bug if: Voids column shows "—" despite confirmed voided tabs.

**K5C-2: Zero voids show "—"**
1. A staff member with shifts but no voided tabs in the period.
- ✅ Correct: Voids column shows "—" for that staff row.

### K5.D — Debt visibility label on staff permissions

**K5D-1: Staff permissions page shows debt scope**
1. Go to /staff/<id>/permissions/ for a regular bar staff member.
- ✅ Correct: a "🧾 Debt Ledger Visibility" row appears at the bottom.
- For bar-only staff: badge reads "Bar debts only".
- For kitchen-only staff (role=kitchen, can_access_bar=off): "Kitchen debts only".
- For cross-authorized staff (can_access_bar+can_access_kitchen both on): "Bar + Kitchen debts (all)".

### K5.E — Shift gate on debt payment + reminder

**K5E-1: Staff blocked from recording debt payment without a shift**
1. Log in as staff (not owner) with no open shift.
2. Go to Debt Tracker → click a customer with outstanding debt.
3. Submit a payment.
- ✅ Correct: error toast "Fungua shift yako kwanza kabla ya kurekodi malipo ya deni."
  and redirect back to customer profile — no payment created.

**K5E-2: Staff can record debt payment with an open shift**
1. Staff opens a shift (/bar/shift/open/ or kitchen shift).
2. Retry step 3 from K5E-1.
- ✅ Correct: payment is accepted and receipt is issued.

**K5E-3: Staff blocked from sending debt reminder without a shift**
1. Log in as staff with no open shift.
2. Customer profile → click "Tuma Kikumbusha".
- ✅ Correct: error "Fungua shift yako kwanza kabla ya kutuma kikumbusha."
- ❌ Bug if: SMS reminder fires without checking shift status.

**K5E-4: Owner bypasses shift gate on debt payment**
1. Log in as owner (no shift required).
2. Record a debt payment directly.
- ✅ Correct: payment goes through immediately.

---

## Sprint DJ1 — DJ / MC Performer Session Management

### DJ1 core flow

**DJ1-1: Add a performer**
1. Log in as RoyMwendwa (owner) → Navbar → 🎤 DJ / MC → "Rekodi za Sesheni" → or go to `/bar/performers/` directly.
2. Click "+ Ongeza Mwanamuziki".
3. Fill: Name = "DJ Kamau", Type = DJ, Contract = One-Off, Rate = KES 3,000, Genre = Afrobeats.
4. Save.
- ✅ Correct: performer appears in the roster list with stat badges.
- ❌ Bug if: 500 error or blank page.

**DJ1-2: Start a session from the bar board**
1. Bar Board → click "🎤 DJ/MC" button (header row).
2. Modal opens → select "DJ Kamau" from dropdown → fee pre-fills to KES 3,000 → click "▶ Anza Sesheni".
3. Modal should transition to State 1 (ACTIVE):
   - Green "ACTIVE" badge visible.
   - Check-in QR code rendered + short code below it.
   - 5-star rating widget and "Maliza Sesheni" button.
   - Distribution section: WhatsApp, Live Display, Print Card, SMS Wateja buttons.
- ✅ Correct: session starts, DJ modal shows ACTIVE state.
- ❌ Bug if: modal stays on the "Start Session" form.

**DJ1-3: Performer self-check-in (anti-fraud)**
1. From the modal, show the check-in QR to a second device (or copy the checkin URL).
2. Open `/p/<checkin_token>/checkin/` on another device (no login needed).
3. See "Confirm you are performing at [Bar Name]" page → tap "Ndio, niko hapa".
4. Wait up to 30 seconds (or reopen the modal).
- ✅ Correct: check-in badge turns green "✓ Amethibitisha HH:MM".
- ❌ Bug if: badge stays amber "Hajajibu bado" even after confirmation.

**DJ1-4: Customer feedback via QR**
1. From the modal's distribution section, scan the feedback QR code (or open `/p/<feedback_token>/`).
2. Public feedback page loads — star rating + optional comment.
3. Tap 4 stars → "Tuma Maoni".
4. Reopen the DJ/MC modal → ACTIVE state.
- ✅ Correct: the small feedback QR is visible in the distribution section; submitting on the public page works.

**DJ1-5: End session + record staff rating**
1. In the DJ/MC modal → give 5-star staff rating → click "Maliza Sesheni".
2. Modal transitions to State 2 (COMPLETED):
   - Duration shown (e.g. "1.2h").
   - Existing feedback QR shown.
   - Distribution section still visible.
   - "Lipa Cash" / "Lipa M-Pesa" buttons visible (owner only).
- ✅ Correct: session transitions to COMPLETED state.

**DJ1-6: Mark session paid → expense created**
1. In COMPLETED state → click "Lipa Cash".
2. Go to `/analytics/expenses/report/` (Expense Intelligence).
3. Look for 'Entertainment' category in the category breakdown chart.
4. Also check `/bar/z-report/` for today → "🎤 Entertainment (KES)" tile should be visible.
- ✅ Correct: expense appears in analytics AND Z-report.
- ❌ Bug if: BusinessExpense row not created or doesn't appear in Expense Intelligence.

**DJ1-7: Unverified session alert (fraud scenario)**
1. Start a new session but do NOT let the performer check in.
2. End the session immediately.
3. Check owner's in-app notification bell.
- ✅ Correct: notification "⚠️ DJ/MC session ended but [Name] never confirmed presence."
- ❌ Bug if: no notification when performer_checked_in is False at session end.

**DJ1-8: Session history page**
1. Go to `/bar/sessions/`.
2. Filter by performer "DJ Kamau".
- ✅ Correct: the sessions just created appear with Date, Duration, Fee, Paid status, Staff Rating ⭐, Customer Avg ⭐.
- Amber "Unverified" badge on any session where performer never checked in.

---

### DJ1 distribution channels

**DJ1-D1: WhatsApp share**
1. Open the DJ/MC modal on an ACTIVE or COMPLETED session.
2. Click "💬 WhatsApp" in the distribution section.
3. New tab opens with `wa.me/?text=<message>`.
- ✅ Correct: WhatsApp opens (or prompts) with pre-filled text including the performer name and feedback URL.
- On mobile: WhatsApp app opens directly with the message pre-filled.

**DJ1-D2: Live Display (TV screen)**
1. Click "📺 Live Display" in the distribution section.
2. `/p/<feedback_token>/display/` opens in a new tab.
- ✅ Correct: dark full-screen page shows:
  - Animated "LIVE USIKU WA LEO" badge with pulsing dot.
  - Performer name in large Playfair Display font.
  - Business name and start time.
  - Large QR code (feedback URL) with white background.
  - Auto-refresh every 30 s (check page source for `<meta http-equiv="refresh" content="30">`).
- ❌ Bug if: 404 or QR fails to render.

**DJ1-D3: Print Card**
1. Click "🖨 Print Card" in the distribution section.
2. `/p/<feedback_token>/display/?print=1` opens in a new tab.
- ✅ Correct: same page opens AND browser print dialog launches automatically.
- Print preview should show a white card (not dark background) with gold border, QR code, and feedback URL text.

**DJ1-D4: SMS blast to customers**
*(Only visible to owner; only clickable when business has registered customers with phone numbers.)*
1. As owner, ensure at least 1 customer exists with a phone number (Debt Tracker → any customer with a recorded phone).
2. Click "📱 SMS N Wateja" in the distribution section of an ACTIVE session.
- ✅ Correct: button shows "✓ Imetumwa kwa N wateja" in green after a moment.
- ❌ Bug if: error toast or no button at all despite customers existing.
3. Session must be ACTIVE (not COMPLETED) for SMS to send; clicking on a COMPLETED session should return an error if the view rejects non-active sessions.

**DJ1-D5: SMS count updates on modal open**
1. Add a new customer with a phone number to the Debt Tracker.
2. Close and reopen the DJ/MC modal (this triggers `session_today_api` refresh).
- ✅ Correct: SMS button label updates to reflect the new count (e.g. "SMS 5 Wateja" → "SMS 6 Wateja").

---

### DJ1 approval gate (optional — requires `performer_approval_threshold` set)

**DJ1-AP1: High-fee session requires owner approval**
1. In Django admin → Business → set `performer_approval_threshold = 2000`.
2. Log in as a staff member (not owner) → open the DJ/MC modal → start a session with fee KES 3,000.
3. Status should be "PENDING_APPROVAL" (amber badge), not ACTIVE.
4. Log in as owner → approve from the modal.
- ✅ Correct: session transitions to ACTIVE after owner approval.

---

## Final Checks (post-DJ1)

```
python manage.py check                  # 0 issues
python manage.py makemigrations --check # No changes detected
python manage.py test                   # 126 tests, all pass
```

---

## Sprint K7 — Hotfix + Cleanup

### Automated assertions
- `PerformerFeedback` model has no `ip_hash` field:
  ```python
  from core.models import PerformerFeedback
  assert not hasattr(PerformerFeedback, 'ip_hash')
  ```
- `agreed_fee` does not appear on any public (no-login) template — confirmed by:
  ```
  grep -rn "agreed_fee" templates/
  ```
  Expected files: `bar_board.html` (IS_OWNER gated JS), `session_promo_page.html` (boolean check only,
  never displays amount), `performer_list.html` (owner-gated), `performer_form.html` (owner-gated),
  `session_list.html` (owner-gated). NOT present in `performer_checkin_public.html`.

### Manual smoke tests (Render)

**K7-1 — Check-in page fee removed**
1. Owner creates a performer session (bar board → 🎤 DJ/MC → start session).
2. Open the check-in URL (`/p/<checkin_token>/checkin/`) in a private browser tab (not logged in).
3. ✅ Correct: page shows performer name, venue, and date — **no fee amount visible anywhere**.
4. Click "Ndio, niko hapa ✓" — confirm the check-in succeeds.

**K7-2 — Feedback localStorage dedup still active**
1. Open the feedback URL (`/p/<feedback_token>/`) in a private browser.
2. Submit a 4-star rating with at least one tag chip selected.
3. ✅ Correct: "Asante sana!" done screen appears, showing the submitted stars and tags.
4. Reload the page in the **same browser**.
5. ✅ Correct: page immediately shows the done screen (localStorage dedup active — no re-vote form).
6. Open the same URL in a **different browser** (or clear localStorage).
7. ✅ Correct: vote form appears again — each device can vote independently.

**K7-3 — ip_hash field gone from DB**
- Run `python manage.py dbshell` → `\d core_performerfeedback` (PostgreSQL) or
  `.schema core_performerfeedback` (SQLite).
- ✅ Correct: no `ip_hash` column present.

### Final checks (post-K7)

```
python manage.py check                  # 0 issues
python manage.py makemigrations --check # No changes detected (0084 already applied)
python manage.py test                   # 121 tests, all pass
```

---

## Sprint DJ2 — Pre-scheduled sessions + shareable promo page

### DJ2 core flow

**DJ2-1: Schedule a session for a future date**
1. Log in as RoyMwendwa (owner) → Bar Board → click "🎤 DJ/MC".
2. In the start section, click "📅 Panga kwa siku nyingine" toggle.
3. A scheduling form appears: enter a date at least 1 day in the future and an optional start time.
4. Select a performer from the dropdown (the same one used in DJ1).
5. Click "📅 Hifadhi Ratiba".
- ✅ Correct: modal transitions to show the session in "📅 Ratiba Ijayo" section with date, Share, Promo, and Anza buttons.
- ❌ Bug if: session is created as ACTIVE immediately instead of SCHEDULED.

**DJ2-2: Scheduled session appears in Ratiba Ijayo**
1. Close and reopen the 🎤 DJ/MC modal.
2. ✅ Correct: "📅 Ratiba Ijayo" section at the top shows the scheduled session with performer name and date.
3. Sessions more than 7 days in the future should NOT appear (only next 7 days shown).

**DJ2-3: Promo page renders correctly**
1. In the Ratiba Ijayo section, click "📊 Promo" for the scheduled session.
2. `/p/<feedback_token>/promo/` opens.
- ✅ Correct: dark luxury poster card with:
  - "INAKUJA HIVI KARIBUNI" gold badge (or "USIKU HUU" in raspberry if the session date is today).
  - Performer name in large Playfair Display font.
  - Date and optional start time detail rows.
  - A QR code that links back to the promo page itself.
  - "💬 WhatsApp" share button and "🔗 Nakili Link" copy button.
  - "🖨 Print Poster" button.
- ❌ Bug if: 404, missing QR code, or wrong date shown.

**DJ2-4: WhatsApp message pre-fills correctly**
1. On the promo page, click "💬 WhatsApp".
2. WhatsApp opens with a pre-filled message including:
   - Performer name (and second performer for duo).
   - Business name.
   - Formatted date.
   - Optional start time (if set).
   - The promo URL.
- ✅ Correct: message reads naturally in Swahili/English with the correct venue name.

**DJ2-5: Share from bar board modal**
1. In the Ratiba Ijayo section, click "💬 Share" for the scheduled session.
2. ✅ Correct: opens `wa.me/?text=...` with performer name, bar name, date, and promo URL.

**DJ2-6: Activate session on the night**
1. On the night of the scheduled performance, open the 🎤 DJ/MC modal.
2. In the Ratiba Ijayo section, click "▶ Anza" for that session.
- ✅ Correct: session status changes to PENDING_CONFIRMATION (see DJ3); it moves from Ratiba Ijayo into the active sessions list.
- ❌ Bug if: session stays as SCHEDULED or jumps directly to ACTIVE (skipping confirmation).

**DJ2-7: Auto-print from promo URL**
1. Open `/p/<feedback_token>/promo/?print=1` in any browser.
- ✅ Correct: browser print dialog launches automatically on page load.

---

## Sprint DJ3 — Duo support + two-step confirmation + payment privacy

### DJ3 core confirmation flow

**DJ3-1: Start a session — verify PENDING_CONFIRMATION status**
1. Bar Board → 🎤 DJ/MC → select a performer → click "▶ Anza Sesheni".
2. The modal should now show the session as "⌛ Inasubiri uthibitisho" (amber), NOT "ACTIVE".
3. A checklist appears:
   - `○ [Performer Name] — Hajajibu bado`
   - `○ Staff — Hajajibu bado`
- ✅ Correct: PENDING_CONFIRMATION state, checklist visible.
- ❌ Bug if: session immediately shows as "ACTIVE" (the new flow requires all parties to confirm first).

**DJ3-2: Performer QR check-in transitions checklist item**
1. From the PENDING_CONFIRMATION modal, show the QR to the performer (or copy `/p/<checkin_token>/checkin/`).
2. Open the check-in URL on another device (no login) → tap "Ndio, niko hapa ✓".
3. Wait up to 30 seconds (poll interval) or close and reopen the modal.
- ✅ Correct: checklist row for performer turns green "✓ [Performer Name] — Amethibitisha HH:MM".
- Session stays PENDING_CONFIRMATION until staff also confirms.
- ❌ Bug if: session jumps to ACTIVE after only the performer checks in.

**DJ3-3: Staff on-duty confirmation button**
1. With the performer checked in (step DJ3-2), the modal still shows PENDING_CONFIRMATION.
2. A blue button "👥 Thibitisha Ufika (Wewe ni Staff)" appears.
3. Click it.
- ✅ Correct: staff check mark turns green; session flips to ACTIVE.
  - "● Inaendelea" status badge appears.
  - Modal now shows the familiar ACTIVE state: star rating, "Maliza Sesheni" button, distribution section.
- ❌ Bug if: session doesn't flip to ACTIVE after both performer and staff confirm.

**DJ3-4: Cannot pay until all parties confirm**
1. End a session that was NOT fully confirmed (e.g. performer never checked in).
2. As owner, try to pay the session.
- ✅ Correct: error "Sesheni bado haijathibitishwa na pande zote. Malipo hayawezi kufanywa."
- ❌ Bug if: payment goes through even when performer_checked_in = False.

**DJ3-5: Payment SMS fires to performer (no amount)**
1. Complete a session, confirm all parties (DJ3-1 through DJ3-3), then pay (owner only).
2. Check the performer's phone (if they have a registered number on their Performer profile).
- ✅ Correct: SMS received — message mentions business name and date, **no KES amount disclosed**.
- ❌ Bug if: amount visible in SMS, or SMS not sent at all.

**DJ3-6: Performer sees payment status on check-in page**
1. After completing DJ3-2 (performer confirmed), refresh the check-in URL on the performer's device.
   (Or if `already_checked_in`, the page shows the done state immediately on load.)
2. Below the done box, a "Malipo" card appears:
   - Before payment: amber "⏳ Yanasubiri" + hint "Bookmark ukurasa huu ukague baadaye".
   - After owner marks paid: green "✓ Yamethibitishwa".
- ✅ Correct: payment status visible on the performer's private URL only (no fee amount).
- ❌ Bug if: fee amount (e.g. "KES 3,000") is shown anywhere on the check-in page.

**DJ3-7: Staff cannot see fee or payment status in bar board**
1. Log in as bar staff (not owner) → open 🎤 DJ/MC modal.
2. A COMPLETED session is visible.
- ✅ Correct: NO fee amount shown anywhere in the modal for staff. Payment status ("Amelipwa" / "Hajalipwa") is also hidden.
- ❌ Bug if: "KES X" or "Hajalipwa" text visible to staff.

### DJ3 duo flow

**DJ3-8: Start a duo session**
1. Bar Board → 🎤 DJ/MC → in the start form, tick "Duo — DJ na MC wawili" checkbox.
2. A second performer dropdown appears.
3. Select a primary performer (e.g. DJ Kamau) and a second performer (e.g. MC Wanjiru).
4. Click "▶ Anza Sesheni".
- ✅ Correct: session starts in PENDING_CONFIRMATION with the header showing "DJ Kamau & MC Wanjiru".
- Checklist shows THREE items: P1, P2, and Staff.

**DJ3-9: Both performers must check in for duo**
1. Open the primary performer's check-in URL (`/p/<checkin_token>/checkin/`) → confirm.
2. Checklist: P1 turns green, P2 and Staff still amber.
3. Open the second performer's check-in URL (`/p/<second_performer_checkin_token>/checkin/`) → confirm.
4. Checklist: P1 green, P2 green, Staff still amber. Session still PENDING_CONFIRMATION.
5. Staff clicks "👥 Thibitisha Ufika" → all three green → session flips to ACTIVE.
- ✅ Correct: all three separate QRs visible, three-step confirmation required.
- ❌ Bug if: session goes ACTIVE after only one of the two performers scans.

**DJ3-10: Second performer check-in page shows correct name**
1. Open `/p/<second_performer_checkin_token>/checkin/` (the second performer's unique URL).
- ✅ Correct: page shows **"MC Wanjiru"** in the meta-row (not "DJ Kamau").
- ❌ Bug if: wrong performer name shown, or 404.

**DJ3-11: Payment SMS goes to both performers in a duo**
1. Complete a duo session (all three confirmed), then pay.
2. Both performers' phones should receive an SMS.
- ✅ Correct: two separate SMS messages — one to DJ Kamau's number and one to MC Wanjiru's number.
- ❌ Bug if: only one SMS sent.

### DJ3 promo page — duo

**DJ3-12: Promo page shows duo names**
1. Schedule or start a duo session (DJ Kamau + MC Wanjiru).
2. Open `/p/<feedback_token>/promo/`.
- ✅ Correct: poster shows both names — "DJ Kamau" then "&" then "MC Wanjiru" in Playfair Display.
- OG title tag (visible in WhatsApp link preview) reads "🎤 DJ Kamau & MC Wanjiru LIVE @ [Venue]".
- WhatsApp share text includes both names.
- ❌ Bug if: only primary performer's name shown, or second performer name missing from OG tags.

### DJ3 session list

**DJ3-13: Session list shows second performer**
1. Go to `/bar/sessions/` (owner only).
2. Find a duo session row.
- ✅ Correct: row header shows "DJ Kamau & MC Wanjiru" with combined type badge (e.g. "DJ + MC").
3. Find a PENDING_CONFIRMATION session row.
- ✅ Correct: badge reads "Inasubiri uthibitisho" (amber).

### Final checks (post-DJ3)

```
python manage.py check                  # 0 issues
python manage.py makemigrations --check # No changes detected (0085 already applied)
python manage.py test                   # 126 tests, all pass
```

---

## Sprint T1 — Tab integrity, station scoping, prior-debt gate, promo module

### T1-1: Kitchen "Convert to Deni" works

1. Kitchen Board → open a food tab for a customer.
2. Close the offcanvas, reopen Tabs drawer → find the tab.
3. Click "→ Deni".
- ✅ Correct: tab is converted to debt; Debt Tracker shows the balance for that customer.
- ❌ Bug if: "Hitilafu ya mtandao" error (URL was `/convert-to-debt/` instead of `/debt/`).

### T1-2: Open tabs warning after shift close

1. Create at least one open tab (bar or kitchen).
2. Close the active shift.
- ✅ Correct: shift-close response shows an amber warning listing open tabs by name + "Geuza Zote Deni" button.
- ❌ Bug if: shift closes silently with no tab warning.

### T1-3: Bulk convert open tabs to debt after shift close

1. With open tabs present, close shift → amber warning appears.
2. Click "Geuza Zote Deni".
- ✅ Correct: all listed tabs converted to debt in one action; toast confirms count.
- ❌ Bug if: 400/500 error, or tabs remain OPEN after the action.

### T1-4: Prior-debt gate blocks tab creation for defaulters (bar)

1. Set a customer as a defaulter (`is_defaulter=True` in admin, or via void_tab).
2. Bar Board → type that customer's name in the tab customer field.
- ✅ Correct: amber warning appears immediately on blur showing outstanding balance + defaulter flag; sell button is blocked.
- ❌ Bug if: tab creation proceeds with no warning.

### T1-5: can_authorize_tab_accumulation toggle

1. Django admin → Staff UserProfile → enable `can_authorize_tab_accumulation`.
2. Log in as that staff member.
3. Type a debtor customer name in the tab field.
- ✅ Correct: warning shown but sell button remains enabled (staff can override).
- ❌ Bug if: button still blocked even with the permission toggled on.

### T1-6: Stock list station scoping

1. Log in as kitchen-only staff (role=kitchen, can_access_bar=False).
2. Go to /stock/.
- ✅ Correct: only kitchen store items are visible; bar items are absent.
3. Log in as bar staff (role=staff, can_access_kitchen=False).
4. Go to /stock/.
- ✅ Correct: only bar/main store items are visible; kitchen items are absent.

### T1-7: Home page tile scoping

1. Log in as kitchen-only staff.
2. Home dashboard → should NOT see "Kegs Running Low" tile or DJ/MC widget.
- ✅ Correct: bar-specific tiles hidden; kitchen revenue tile shown.
- ❌ Bug if: Kegs Running Low or DJ/MC widget visible to kitchen staff.

---

## Tab Drawer Visual Fixes (post-T1 audit)

### TAB-V1: Correct item icon per entry type

1. Open a tab that has both food (e.g. Smokies) and bar items (e.g. Pint).
2. Open the Tabs drawer.
- ✅ Correct: food items show 🍽 icon; bar/drink items show 🍺 icon.
- ❌ Bug if: Smokies shows 🍺 icon (was the original bug — all entries used 🍺 regardless).

### TAB-V2: Paid entries hidden from drawer

1. Partially settle a tab (K6 partial settlement — settle some but not all entries).
2. Close and reopen the Tabs drawer.
- ✅ Correct: only unpaid entries shown; the settled items are no longer listed. Total matches sum of visible items.
- ❌ Bug if: settled items still appear greyed-out, making the total confusing (KES 290 with KES 490 visible).

### TAB-V3: Mixed Tab badge on cross-counter merged tabs

1. Merge a food tab entry into a bar tab (or bar entries into a food tab) via cross-counter merge.
2. Open the Tabs drawer.
- ✅ Correct: tab shows "🔀 Mixed Tab" amber badge (not "🍽 Food Tab" or no badge).
- ❌ Bug if: badge still shows "Food Tab" on a tab containing both bar and kitchen items.

### TAB-V4: "Vileo tu" note only shown to bar-only staff

1. Log in as bar-only staff (no kitchen access).
2. Open a food/mixed tab in the Tabs drawer.
- ✅ Correct: sub-label shows "HH:MM · Vileo tu vinaonyeshwa hapa".
3. Log in as owner or cross-access staff.
4. Open the same tab.
- ✅ Correct: sub-label shows only "HH:MM" — no "Vileo tu" note (owner sees all items).
- ❌ Bug if: "Vileo tu vinaonyeshwa hapa" shows to the owner who sees food items too.

---

## Mixed Tab Counter Settlement Fix (2026-07-05)

### TAB-M1: Kitchen Board does NOT show bar items in food tab settlement

**Setup:** Mercy has a food tab with Smokies (kitchen item) + Kikombe/Jug (bar items merged via cross-counter).

1. Log in as owner, open Kitchen Board → Tabs drawer.
2. Find Mercy's tab.
- ✅ Correct: Only "Smokies KES 50" visible with checkbox. No Kikombe, no Jug.
- ✅ Correct: cross_notice shows "+ 2 bar item(s) — settle at Bar Board".
- ✅ Correct: "Deni: KES 50" total reflects only kitchen items.
- ❌ Bug if: Kikombe (KES 60) and/or Jug (KES 180) appear as settleable items in kitchen.

### TAB-M2: Bar Board shows bar items on a food tab as settleable

1. Log in as owner, open Bar Board → Tabs drawer.
2. Find Mercy's tab (shows 🔀 Mixed Tab badge).
- ✅ Correct: "🍽 Chakula" section shows Smokies as read-only (no checkbox).
- ✅ Correct: "🍺 Vileo" section shows Kikombe + Jug each with a checkbox.
- ✅ Correct: Checking Kikombe reveals selection row with "💰 Cash" + "📱 M-Pesa" partial settle buttons.
- ✅ Correct: Footer note "🍽 Chakula → Lipa kwenye Kitchen Board" appears below.
- ❌ Bug if: All entries show as read-only with "Lipa kwenye Kitchen Board" footer.

### TAB-M3: Partial settle of bar items on a mixed food tab

1. On bar board, check Kikombe (KES 60) on Mercy's mixed food tab.
2. Tap "💰 Cash" in the selection row.
- ✅ Correct: Toast "✓ KES 60 imelipwa. Tab bado iko wazi." (partial, tab stays open).
- ✅ Correct: Tabs drawer reloads; Kikombe disappears (now paid), Jug still shown.
- ✅ Correct: Kitchen board still shows Smokies (unpaid kitchen item) unaffected.
- ❌ Bug if: Full tab closed, or Smokies disappear from kitchen board.

### TAB-M4: Pure food tab (no cross-merge) renders correctly on bar board

1. Create a fresh food tab (kitchen only — no bar items).
2. Open bar board Tabs drawer.
- ✅ Correct: Tab shows "🍽 Food Tab" badge (no 🔀 Mixed Tab).
- ✅ Correct: All entries show in "🍽 Chakula" section as read-only (no checkboxes).
- ✅ Correct: Footer shows "Lipa kwenye Kitchen Board" (full footer, no "🍺 Vileo" section).
- ❌ Bug if: Bar section with partial settle buttons appears on a pure food tab.

```
python manage.py test   # 126 tests, all pass
```

---

## Sprint M1 — Manager Role + Owner Consumption Tracking (2026-07-07)

### M1-A: Manager role creation and auto-permissions

**M1A-1: Create a manager account**
1. Log in as RoyMwendwa (owner) → Manage → Add Staff.
2. Fill: name, phone, password. **Role = Manager (Acting Owner — no settings access)**.
3. Save.
- ✅ Correct: manager account created; appears in staff list with purple "Manager" badge in navbar when logged in.
- ✅ Correct: in Django admin → UserProfile → `can_access_bar`, `can_access_kitchen`, `can_override_restrictions`, `can_authorize_tab_accumulation` all `True` automatically.
- ❌ Bug if: permissions are all False (manager can't see bar/kitchen items).

**M1A-2: Manager navbar — operational access, no config links**
1. Log in as the manager account created in M1A-1.
- ✅ Correct: navbar shows the full owner menu (Dashboard, Stock, Quick Sell, Bar Board, Kitchen, Analytics, etc.)
- ✅ Correct: "Manage" dropdown does NOT contain "Add Staff", "Payment Settings", or "Business Settings" — these are owner-only.
- ✅ Correct: purple **Manager** badge appears next to the username in the navbar.
- ❌ Bug if: config links are visible, or the manager sees the generic staff navbar (Quick Sell + Receipts only).

**M1A-3: Manager sees revenue but NOT cost prices**
1. Log in as manager.
2. Go to Stock list → Add Transaction.
3. Select any item → Receipt type.
- ✅ Correct: cost price input is hidden (not shown, same as regular staff without the `can_input_cost_price` flag).
- ✅ Correct: selling price and revenue figures are visible on the dashboard and analytics.
- ❌ Bug if: cost price input appears (manager should not see per-item margins).

**M1A-4: Manager bypasses shift gate for owner-consumption logging**
1. Log in as manager (no open shift).
2. Go to Quick Sell → click "🥃 Mmiliki Alichukua".
3. Select an item (e.g. Whiskey), enter qty 1, click Rekodi.
- ✅ Correct: transaction recorded; stock balance reduced by 1. No shift required.
- ❌ Bug if: "Hakuna shift iliyofunguliwa" error returned.

---

### M1-B: Owner Consumption recording

**M1B-1: Staff records owner consumption during shift**
1. Log in as Morrine (regular bar staff) with an open shift.
2. Go to Quick Sell → click "🥃 Mmiliki Alichukua" button in the header.
3. Select an item (e.g. "Konyagi 250ml"), qty = 2, note = "Personal use".
4. Click ✓ Rekodi.
- ✅ Correct: success toast "✓ Konyagi 250ml (2 Bottles) imerekodiwa."
- ✅ Correct: stock balance for Konyagi reduced by 2.
- ❌ Bug if: 403 error (staff should be able to record this during a shift).

**M1B-2: Owner consumption does NOT count as revenue**
1. After M1B-1, check today's revenue on the home dashboard.
- ✅ Correct: revenue total is UNCHANGED — OwnerConsumption transactions are excluded from all revenue calculations.
- ❌ Bug if: revenue increases by the selling price of the consumed items.

**M1B-3: Owner consumption appears on Z-report**
1. Log in as RoyMwendwa (or manager). Go to `/bar/z-report/`.
- ✅ Correct: if any OwnerConsumption transactions exist for today, a raspberry "🥃 Mmiliki Alichukua" tile shows the count.
- ✅ Correct: Below the tiles, an itemised list shows each item + quantity (without the leading minus sign — e.g. "2.00" not "-2.00").
- ❌ Bug if: tile is absent despite OwnerConsumption transactions existing for the day.

**M1B-4: Stock balance reduces correctly**
1. Note the balance of any item before logging consumption.
2. Log consumption of 3 units.
3. Go to Stock list → check that item's balance.
- ✅ Correct: balance reduced by exactly 3 (OwnerConsumption stores qty = -3, which the `current_balance()` sum includes).
- ❌ Bug if: balance unchanged after consumption.

**M1B-5: Staff without shift cannot log consumption**
1. Log in as staff (Morrine) with NO open shift.
2. Go to Quick Sell → "🥃 Mmiliki Alichukua" → select item, qty 1 → Rekodi.
- ✅ Correct: error toast "Hakuna shift iliyofunguliwa. Fungua shift kwanza."
- ❌ Bug if: consumption recorded despite no shift.

**M1B-6: Item dropdown filters correctly**
1. Open the "🥃 Mmiliki Alichukua" modal.
2. The item dropdown should contain only regular (non-keg, non-produce) items.
- ✅ Correct: wines, spirits, sodas appear. Keg items (tracked by barrel) and produce items are absent.
- ❌ Bug if: keg items or produce items appear in the dropdown.

---

### M1-C: Barrel envelope block + deplete fix (2026-07-07 bugfix)

**M1C-1: Hard-block mode — owner sees deplete confirm, not just a toast**
1. In Django admin → Business → enable `block_sales_past_target = True`.
2. On a non-weighing bar, sell until the barrel's revenue envelope is exactly reached (remaining = 0).
3. Log in as owner. Tap the barrel tile to open the sell modal.
- ✅ Correct: `window.confirm()` appears: "⛔ [Barrel] imefika lengo — mauzo yamezuiwa. Bonyeza OK = Funga Pipa hii na tap mpya"
- Click **OK** → barrel moves to DEPLETED; toast "✓ Barrel imefungwa. Unaweza tap barrel mpya."; board reloads.
- Click **Cancel** → dialog dismissed; no sale; barrel remains blocked.
- ❌ Bug if: only a dead-end toast appears with no way to close the barrel.

**M1C-2: Hard-block mode — staff see blocking toast only**
1. Same setup (block_sales_past_target on, envelope reached).
2. Log in as bar staff (not owner). Tap the barrel tile.
- ✅ Correct: toast "⛔ [Barrel] imefika lengo — mauzo yamezuiwa. Mwambie mwenye biashara aifunge barrel."
- No confirm dialog. No sale.

**M1C-3: Soft-block mode unaffected**
1. Set `block_sales_past_target = False`.
2. Sell until envelope reached, then tap the barrel tile as owner.
- ✅ Correct: existing confirm dialog "⚠️ Barrel imefika lengo... OK = Funga Pipa / Cancel = Endelea kuuza".
- Behaviour unchanged from before M1.

---

### Final checks (post-M1)

```
python manage.py check                  # 0 issues
python manage.py makemigrations --check # No changes detected (0047 + 0094 already applied)
python manage.py test                   # 126 tests, all pass
```

---

## Sprint RD1 — Cross-module Receipt Deduplication

**Goal:** A customer always receives ONE receipt URL per day regardless of how many counters they buy from (bar tab, kitchen tab, Quick Sell deni, or any mix).

### RD1-A: Quick Sell credit → bar tab (QS first, then bar)

**Setup:** Customer name = "TestDedup", open the Quick Sell board.

1. In Quick Sell, sell a soda to TestDedup on Deni → complete checkout.
   - ✅ Correct: receipt issued, SMS sent with receipt URL (call this URL-1).
2. Go to Bar Board, open a keg tab for TestDedup.
   - ✅ Correct: NO second SMS. The bar board detects the QS receipt (Priority 4) and links the bar tab into it via `meta.linked_tab_ids`.
3. Sell a pint to the bar tab → settle via cash.
   - ✅ Correct: `/r/<token>/` for URL-1 now shows BOTH the soda (QS) and the pint (bar tab) lines. One URL, two counters.

### RD1-B: Bar tab first → QS deni same customer (bar first, then QS)

1. Open a bar tab for "TestDedup2" → sell a pint.
   - ✅ Correct: receipt URL-2 issued, SMS sent.
2. In Quick Sell, sell a soda to TestDedup2 on Deni.
   - ✅ Correct: no new receipt created. QS finds today's receipt for TestDedup2 and appends the soda line. No SMS (reused receipt).
3. Open URL-2 — it now shows pint + soda lines. Total updated.

### RD1-C: Kitchen deni → bar tab (kitchen first, then bar)

1. Kitchen Board → sell a chips to "TestDedup3" on Deni.
   - ✅ Correct: kitchen receipt URL-3 issued, SMS sent.
2. Bar Board → open keg tab for TestDedup3, sell a pint.
   - ✅ Correct: bar board Priority 4 links bar tab into URL-3. No second SMS.
3. URL-3 shows both chips and pint.

### RD1-D: Multiple Quick Sell deni items, same customer, same day

1. Sell item A to "TestDedup4" on Deni in Quick Sell → SMS sent.
2. Sell item B to "TestDedup4" on Deni in Quick Sell.
   - ✅ Correct: no second receipt, no second SMS. Item B appended to first receipt.
3. First receipt now shows both items with updated total.

### RD1-E: Different customers — no cross-contamination

1. Sell to "Alice" on Deni, then sell to "Bob" on Deni.
   - ✅ Correct: Alice and Bob each get their own separate receipt. No merging.

---

### Final checks (post-RD1)

```
python manage.py check                  # 0 issues
python manage.py makemigrations --check # No changes detected (no new migrations)
python manage.py test                   # 126 tests, all pass
```

---

## Owner Reporting Audit + Gap Fixes (2026-07-08)

Two bugs fixed, four design gaps closed. No model changes or migrations.

### Bug fixes

**BUG-ZR1: Z-report keg variance now scoped to tonight's barrels**

Previously: `KegBarrel.objects.filter(business=business)` — all barrels ever, showing lifetime accumulated wastage on the nightly Z-report tile.

Fixed to: TAPPED barrels + barrels closed (DEPLETED/RETURNED) on the report date only.

Verify:
1. Log in as owner → `/bar/z-report/` → today's date.
2. "Keg Variance KES" tile should reflect tonight's active barrels only — NOT a cumulative figure across all historical barrels.
- ✅ Correct: figure matches the sum of wastage on currently TAPPED barrels + any barrels depleted/returned today.
- ❌ Bug if: figure is very large (suspiciously high relative to tonight's trade) — still pulling all-time history.

**BUG-DR1: Daily report staff revenue excludes voided pours**

Previously: staff revenue sum had no void exclusion — voided keg pours inflated each bartender's reported figure.

Verify:
1. On the bar board, ring up a sale for a customer, then void that tab.
2. Go to `/bar/daily-report/` → today's date.
3. Find the staff member who served the voided sale in the Staff Performance table.
- ✅ Correct: their revenue does NOT include the voided sale amount.
- ❌ Bug if: revenue is higher than actual paid sales (void amount included).

---

### Gap 2 — DJ/MC SMS now goes to owner's personal phone

Previously: `send_sms_notification(msg, business.phone)` — SMS went to the venue's registered number.

Fixed to: loop through `business.users.filter(role='owner')` and send to each `up.phone` (personal number), same pattern as keg alerts.

Verify:
1. In Django admin → UserProfile → RoyMwendwa → confirm `phone` is set to your personal number.
2. Bar Board → 🎤 DJ/MC → start a new session (any performer).
- ✅ Correct: SMS arrives on the owner's **personal phone** (the `UserProfile.phone`), not on `business.phone` if they differ.
- Gate: `Business.event_sms_enabled` must be True (check in Business Settings).

---

### Gap 4 — Cup low-stock now sends SMS to owner

Previously: only an in-app notification fired when cup pool dropped below 30.

Fixed to: in-app notification + SMS to each owner's personal phone, gated by the same `cup_low_notified_at` cooldown.

Verify:
1. In Django admin → Business → set `cup_low_notified_at = null` (reset cooldown).
2. Bar Board → (owner or staff) → "Log Cup Purchase" — deliberately enter a small quantity so pool stays below 30.
3. Check owner's phone for SMS.
- ✅ Correct: SMS "⚠️ Vikombe vimekwisha! Bado N vikombe — nunua vikombe zaidi mapema." received.
- ✅ Correct: a second cup log immediately after does NOT fire another SMS (cooldown is active).
- ✅ Correct: logging a large purchase (pool now > 30) resets `cup_low_notified_at` to null — next time it drops below 30 the SMS will fire again.

---

### Gap 1 — Cash variance alert at shift close

When staff closes a shift with `|variance| > KES 500`, the owner now receives:
- An in-app notification: "⚠️ Tofauti ya Fedha"
- An SMS to their personal phone with the direction (upungufu = shortfall / ziada = overage) and the KES amount

Verify:
1. Open a shift as bar staff. Ring up KES 500 cash. Close the shift but enter counted cash as KES 0 (deliberate shortfall of KES 500+).
2. Check owner's in-app notifications → bell icon.
- ✅ Correct: notification "⚠️ Tofauti ya Fedha — [Staff Name]: KES 500 (upungufu). Angalia Z-Report..."
- ✅ Correct: SMS on owner's phone with same message.
- ✅ Correct: a shift closed with variance ≤ KES 500 does NOT fire any alert (within tolerance).
- ✅ Correct: owner closing their own shift — no alert fires (alert is only for staff shifts; owner has full visibility via Z-report).

---

### Gap 3 — Pouring league now includes walk-up cash/M-Pesa sales

Previously: the Bar Performance analytics pouring league used `BarTabEntry.served_by` — only tab sales counted. A bartender who serves mostly cash walk-ups was invisible.

Fixed to: shift-window attribution — for each bar shift in the date range, sum ALL `Issue` transactions (keg, non-kitchen, non-void) during that shift's time window and attribute them to the shift's staff member. Both tab and walk-up sales are captured.

Verify:
1. Open a bar shift as staff → ring up 3 pint sales as **cash** (no tab opened) → close shift.
2. Go to `/analytics/` → Bar Performance → Staff Keg Pouring League.
- ✅ Correct: that staff member appears in the league with revenue reflecting the walk-up cash pints.
- ✅ Correct: total revenue in the league roughly matches total bar revenue for the period (tab + walk-up combined).
- ❌ Bug if: bartender who served cash-only customers shows KES 0 in the league.

---

### Final checks (post-audit fixes)

```
python manage.py check                  # 0 issues
python manage.py makemigrations --check # No changes detected (no new migrations)
python manage.py test                   # 121 tests, all pass
```

---

### Bar Operations Audit (2026-07-12)

**Bugs fixed (commit 5ef70b7):**

1. `update_tab_phone` — was missing `@login_required` + `@require_POST`. Every other mutation endpoint has both; this one accepted GET requests from unauthenticated callers and returned a 403 JSON rather than redirecting to login.

2. `bar_daily_report` staff performance — did NOT skip kitchen-staff shifts (unlike Z-report which already had the exclusion). Also did NOT filter `item__store__is_kitchen=False` from the transaction queryset, so on multi-counter businesses, kitchen revenue bled into the bar staff daily report.

3. `convert_tab_to_debt` — no debt-confirmation SMS sent to the customer. Quick Sell credit sales send "KES X imewekwa kwa deni lako" on credit checkout; converting a bar tab to debt is the same accounting event but was silent.

**Manual smoke tests:**

**Tab rename → debt conversion SMS**
1. Open tab for a customer with a phone number.
2. Convert to Deni (bar board tabs drawer → Geuza Deni).
3. ✅ Correct: customer receives SMS "Deni la KES X limeandikwa (Bar). Lipa ndani ya siku N."

**Bar daily report kitchen isolation**
1. In a business with both bar and kitchen, open a kitchen shift and ring up food sales.
2. Go to `/bar/daily/` for that date.
3. ✅ Correct: kitchen staff do NOT appear in the Staff/Shift performance table.
4. ✅ Correct: kitchen revenue does NOT appear in bar staff revenue totals.

---

## Sprint BillScan — Scan to View Your Bill (2026-07-15)

**Goal:** A single static QR the owner prints and mounts on the bar wall. Customers scan with their phone camera, type their name or 4-digit PIN, and see their own running tab — no barman involvement needed.

### BillScan-1: Wall QR in Payment Settings

1. Log in as RoyMwendwa (owner) → Manage → Payment Settings.
2. Scroll to the "🪧 Wall Tab QR" card (visible only for keg/bar businesses).
- ✅ Correct: a QR code is rendered inside the card pointing to `/bar/find-tab/<business_id>/`.
- ✅ Correct: clicking "🖨️ Print QR" opens the browser print dialog; only the QR box is visible in print preview (all other page content is hidden).
- ❌ Bug if: QR card is absent, or QR fails to render (empty white box).

### BillScan-2: PIN shown in bar board tabs drawer

1. Bar Board → open a tab for a customer (name them "Test Scan").
2. Open the Tabs drawer.
3. Find the new tab in the list.
- ✅ Correct: a small "PIN: XXXX" label appears in muted grey next to the customer name.
- ✅ Correct: PIN is visible whether you're logged in as owner or as bar staff.
- ❌ Bug if: no PIN label, or label shows "PIN: " with no digits.

### BillScan-3: PIN shown in Quick Sell tabs drawer

1. Open Quick Sell → Tabs drawer (if a tab exists).
- ✅ Correct: "PIN: XXXX" label is visible next to the customer name for each open tab.

### BillScan-4: PIN shown in Kitchen Board tabs drawer

1. Kitchen Board → open a food tab → Tabs offcanvas.
- ✅ Correct: "PIN: XXXX" label visible next to the customer name (both food tabs and bar tabs shown in kitchen drawer).

### BillScan-5: Find-tab page — name search (not logged in)

1. Open a private/incognito browser window (no login).
2. Navigate to `/bar/find-tab/<business_id>/` (or scan the wall QR).
3. Type at least 2 characters of "Test Scan".
4. Tap the result card.
- ✅ Correct: page loads without redirecting to login. Shows business name + "N tabs zinafanya kazi sasa" count.
- ✅ Correct: after ~400 ms, a card appears showing "Test Scan" + "Imefunguliwa saa HH:MM AM/PM".
- ✅ Correct: tapping navigates to `/tab/<token>/` (the live bill page).
- ❌ Bug if: redirected to login, or card doesn't appear after typing.

### BillScan-6: Find-tab page — PIN direct-match

1. On the find-tab page (incognito), type the exact 4-digit PIN from BillScan-2.
- ✅ Correct: page immediately redirects to `/tab/<token>/` — no name-cards list shown.
- ❌ Bug if: a list of tab cards appears instead of direct navigation.

### BillScan-7: Wrong PIN returns helpful message

1. On the find-tab page, type a 4-digit number not matching any open tab (e.g. `0000`).
- ✅ Correct: "PIN haikupatikana. Jaribu jina lako badala yake." message appears below the input.
- ❌ Bug if: blank result, 500 error, or silent failure.

### BillScan-8: Live bill page — content (not logged in)

1. Open `/tab/<token>/` in an incognito browser.
- ✅ Correct: page loads without redirecting to login.
- ✅ Correct: business name in header; "Tawi la: Test Scan" label.
- ✅ Correct: all items listed with 🍺 (bar) or 🍽 (kitchen) icon, description, and KES amount.
- ✅ Correct: "Jumla" tile in gold shows grand total; "Bado kulipa" tile in raspberry shows outstanding.
- ✅ Correct: footer: "Powered by Duka Mwecheche".
- ❌ Bug if: blank page, missing items, or login redirect.

### BillScan-9: Live bill auto-refresh

1. On the open live bill page, add a new item to the same tab from the bar board.
2. Wait up to 20 seconds (or manually reload).
- ✅ Correct: the new item appears on the live bill page after the auto-refresh cycle.

### BillScan-10: Settled tab shows settled banner

1. Settle the tab (bar board → settle via cash or M-Pesa).
2. Reload `/tab/<token>/`.
- ✅ Correct: amber "✓ Tab hii imelipwa — asante!" banner at the top.
- ✅ Correct: no "Inasasisha kila sekunde 20" refresh note (auto-refresh stops on settled tabs).

### BillScan-11: Anonymous tab accessible via PIN only

1. Bar Board → open a tab with NO customer name (or a table number like "Table 3").
2. Tabs drawer → note the PIN for this tab.
3. On the find-tab page (incognito), type the PIN → navigates to the live bill.
4. Live bill header shows "Tab inayoendelea · Imefunguliwa HH:MM" (no "Tawi la:" line since no name).
- ✅ Correct: anonymous tabs reachable by PIN; header shows fallback text.

### BillScan-12: Rate limiting on search endpoint

1. On the find-tab page (incognito), perform 6 search queries within 60 seconds.
2. The 6th request should return the rate-limit message.
- ✅ Correct: "Subiri kidogo" error text appears.
- ❌ Bug if: unlimited requests accepted without rate limiting.

---

### Final checks (post-BillScan)

```bash
python manage.py check                  # 0 issues
python manage.py makemigrations --check # No changes detected (migration 0103 already applied)
python manage.py test                   # 117 tests, all pass
```

---

## Sprint K8 — Audit Fixes (2026-07-15)

**Automated (7 new tests, 133 total):**
- `BackfillTabTokensCommandTest` — `backfill_tab_tokens` fills blank `tab_receipt_token`/`tab_pin`
  on OPEN tabs only (leaves already-populated and SETTLED tabs untouched); PINs backfilled for
  the same business are unique.
- `NetProfitWastageDeductionTest` — regression lock: `wastage_loss` is deducted from `net_profit`
  exactly once via `total_losses` (this is the intentional 2026-07-13 formula — the July 15 audit's
  claim that this was a double-deduction was reviewed and rejected; formula is unchanged).
- `TabLiveOutstandingTileTest` — the "Bado kulipa" tile is present when a tab has an unpaid balance
  and absent once every entry is `is_paid=True`.

**Manual smoke tests:**

### K8-1: Wall QR reaches pre-existing open tabs after backfill

1. On Render (or any environment with tabs opened before this sprint), run:
   `python manage.py backfill_tab_tokens` — note the "Backfilled N tabs." count.
2. Scan the bar wall QR (or open `/bar/find-tab/<business_id>/`) and search by the name of a
   customer whose tab was opened before the sprint.
- ✅ Correct: the tab now appears in search results and opens the live bill.
- ❌ Bug if: pre-existing open tabs are still invisible to search.

### K8-2: Settled tab shows only the Jumla tile

1. Open a tab, add an item, settle it fully (cash or M-Pesa).
2. Visit `/tab/<token>/` for that tab.
- ✅ Correct: only the "Jumla" (grand total) tile is shown — no "Bado kulipa: 0" tile.
- ❌ Bug if: a "Bado kulipa" tile still appears showing 0.

### K8-3: analytics.html and delete_item.html render correctly on dark theme

1. Open Analytics → Revenue Forecast section; open any item's Delete page.
- ✅ Correct: "Product"/"Model"/"Forecast horizon" labels and the delete-confirmation subtitle are
  legible grey text (`#b0b0b0`), not near-invisible Bootstrap `text-muted`.

### Final checks (post-K8)

```bash
python manage.py check                  # 0 issues
python manage.py makemigrations --check # No changes detected
python manage.py backfill_tab_tokens    # one-time run on each deployed environment
python manage.py test                   # 133 tests, all pass
```
