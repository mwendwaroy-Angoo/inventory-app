# Bar Module Sprint Test & Verification Guide

**Scope:** F1 → F6 (plus B0). Use this to verify the full bar sprint sequence on the live app after deployment.

---

## Automated Tests

Run with:
```
python manage.py test
```

Expected: **51 tests, 0 failures**.

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

## Final Checks

After all smoke tests pass:

```
python manage.py check        # 0 issues
python manage.py makemigrations --check   # No changes detected
python manage.py test         # 51 tests, 0 failures
```

All green = bar sprint sequence complete.
