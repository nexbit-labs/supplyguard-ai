# data/ - Synthetic dataset

Owner: **Nikhil** (N-02). All figures are illustrative synthetic data.

- `generate_seed.py` - deterministic generator (fixed seed). **Edit this, never the JSON** - a test fails if the committed files differ from the generator output.
- `seed/` - the "ERP tables", loaded by both the mock ERP (N-03) and Django `manage.py load_seed` (U-03).
- `scenarios/` - one canonical disruption event (CONTRACTS section 3) per test case, plus an `expected` block that N-04 / N-14 assert against.

```
python data/generate_seed.py                     # dates anchored on 2026-12-02 (committed version)
python data/generate_seed.py --as-of 2026-12-29 --out /tmp/demo-data  # re-anchor for a live demo day (--out required)
python -m pytest data/tests -q
```
Dates are relative to `as_of`; events happen at `as_of`T10:15:00Z. IDs and amounts do not change with `--as-of`.

## Seed files (`data/seed/`)
| File | Rows | Key | Notes |
|---|---|---|---|
| `tenants.json` | 2 | `tenant_id` | TEN-ACME (INR), TEN-GLOBEX (USD); `locations`, `settings.risk_thresholds` |
| `suppliers.json` | 100 | `supplier_id` | ACME SUP-1001..1050 primary + SUP-2001..2010 alternate-only; GLOBEX SUP-3001..3040. `capacity_per_week` null = unknown. `disruption_history[]` |
| `purchase_orders.json` | 500 | `po_id` | ACME PO-1001..1300, GLOBEX PO-5001..5200. Header `expected_date` = latest line `due_date`; `amount` = sum of lines; status OPEN/RECEIVED/CANCELLED |
| `inventory.json` | 187 | (`tenant_id`,`item_id`,`location_id`) | `on_hand`, `reserved`, `safety_stock`, `daily_demand` (units/day) at DC-PUNE / DC-CHENNAI / DC-DELHI |
| `alternate_suppliers.json` | 101 | (`tenant_id`,`item_id`,`supplier_id`) | `qualified`, `lead_time_days`, `unit_price`, `capacity` (units/week, null = unknown) |
| `shipments.json` | 20 | `shipment_id` | all IN_TRANSIT, linked to open POs |
| `manifest.json` | - | - | as_of, seed, row counts, duplicate map |

Money is `{"amount": "75.60", "currency": "INR"}` (string, 2 dp) everywhere. Every row carries `tenant_id`; no row references another tenant's data.

**Deliberate duplicates (N-12):** SUP-1046 = SUP-1011, SUP-1047 = SUP-1012, SUP-1048 = SUP-1013 (same `tax_id`, name variants).

## Impact rule behind the expected numbers
- **Evaluated:** OPEN POs of the event's supplier, same tenant.
- **Affected:** an evaluated PO with any line whose `due_date` is on or before the date the disruption ends (`event_time + expected_duration_hours`).
- **Exposure:** sum of `qty * unit_price` over the affected lines.
- **Inventory coverage (days):** `(on_hand - reserved - safety_stock) / daily_demand` per location (net of safety stock; gross without it is only informational).

N-04 must reproduce these; delay estimate and risk score are N-04's to define.
- **Overdue OPEN POs count as affected** (they are already late and get later). The random background data has ~40 of them; only matters for what-if (N-11) on non-anchor suppliers.
- Scenarios must be replayed with their own `event_time`. If `/demo/simulate-disruption` stamps "now", regenerate with `--as-of <today>` first, or the expected sets will not hold.
- Line `due_date` is what the rule reads. When the mock ERP (N-03) PATCHes a PO header `expected_date`, it must also move the line `due_date`s, so a re-run sees the new date.

## Field names (for U-03 `load_seed` and N-03 mock ERP)
CONTRACTS section 1/3/5 names are used wherever they exist; LLD 16.1 names are older and differ:

| Seed field | LLD 16.1 name | Note |
|---|---|---|
| inventory `on_hand`, `reserved`, `safety_stock` | `on_hand_qty`, `reserved_qty`, `safety_stock` | CONTRACTS s5 wins |
| inventory `daily_demand` | - | **new** - needed for coverage days (N-04). Proposed for CONTRACTS s5 in S-01 |
| PO `amount` = `{amount, currency}` | `amount`, `currency` (flat) | CONTRACTS s1 money object |
| PO `lines[]` (`line_no`, `item_id`, `item_name`, `qty`, `due_date`, `unit_price`) | PurchaseOrder (+lines) | CONTRACTS s5 |
| supplier `city`, `country` | `location` | |
| supplier `tax_id`, `capacity_per_week`, `risk_level`, `on_time_delivery_pct`, `quality_score`, `disruption_history[]` | "risk indicators" | `tax_id` is the N-12 match key |
| event `event_time`, `expected_duration_hours`, `source_system` | `start_time`, `expected_duration`, `source` | CONTRACTS s3 wins |
| `alternate_suppliers.json`, `shipments.json` | not an LLD entity | U-03: add models or skip; the mock ERP serves both |

## Running test cases against one mock ERP
TC-07 really changes PO-1001 (moves it to SUP-2001). Call the mock ERP reset endpoint (N-03) between TC-06, TC-07 and TC-08, otherwise TC-08's expected sets no longer hold.

## Proposed for S-01 (CONTRACTS changes)
- Add `daily_demand` to the s5 inventory response.
- State that CONTRACTS names win over LLD 16.1 (table above).
- Fix the s6 recovery example: `qty` 400 -> 4000, so the 33,600.00 cost delta adds up ((84.00 - 75.60) x 4000).

## Expected numbers for the demo scenario
| Metric | Value |
|---|---|
| Disrupted supplier | SUP-1008 Shakti Components Pvt Ltd (factory outage, 48 h, event EVT-000123) |
| Open POs evaluated | 3 - PO-1001, PO-1002, PO-1003 (PO-1004 is RECEIVED, ignored) |
| Affected open POs | 2 - PO-1001 (4000 x ITM-501, due +1 day), PO-1002 (2500 x ITM-501, due +2 days) |
| Exposure | INR 491,400.00 (6500 x 75.60) |
| Inventory | ITM-501 DC-PUNE covers 1.00 day (< 2-day outage -> shortage); DC-CHENNAI covers 3.33 days (transfer candidate). Coverage = `(on_hand - reserved - safety_stock) / daily_demand` (net of safety stock; N-04 decision) |
| Alternate suppliers | 2 - SUP-2001 (qualified, 2 days, 84.00, 5000/week), SUP-2002 (qualified, 5 days, 79.50, capacity unknown) |
| Rank-1 option cost delta | INR 33,600.00 = (84.00 - 75.60) x 4000 (CONTRACTS section 6 example) |
| SUP-1008 capacity | null (agents must report it as missing data) |

## Scenarios (`data/scenarios/`)
| File | TC | Supplier | What it proves | Affected POs | Exposure (INR) |
|---|---|---|---|---|---|
| `demo.json` | DEMO | SUP-1008 | live demo | PO-1001, PO-1002 | 491,400.00 |
| `tc01_valid_event.json` | TC-01 | SUP-1015 | accepted, correlated, case created | PO-1005 | 310,312.00 |
| `tc02_unknown_supplier.json` | TC-02 | SUP-9999 | flagged, no impact | - | - |
| `tc03_multiple_open_pos.json` | TC-03 | SUP-1020 | 6 open POs evaluated, 5 affected | PO-1007..PO-1011 | 537,649.00 |
| `tc04_inventory_sufficient.json` | TC-04 | SUP-1030 | ITM-530 covers ~42.5 days (net of safety stock) vs 3-day outage: no shortage | PO-1013, PO-1014 | 1,468,775.00 |
| `tc05_no_alternates.json` | TC-05 | SUP-1040 | ITM-540: no alternates, no other stock, supplier capacity unknown -> no validated alternative (`ALTERNATE_SUPPLIER`, `INVENTORY_TRANSFER`, `SPLIT_ORDER` unsupported; a `REVISED_SCHEDULE` that admits the shortage is acceptable) | PO-1015 | 474,384.00 |
| `tc06_reject.json` | TC-06 | SUP-1008 | `decision: REJECT` -> no ERP write | as demo | as demo |
| `tc07_approve.json` | TC-07 | SUP-1008 | `decision: APPROVE` -> ERP write captured | as demo | as demo |
| `tc08_erp_timeout.json` | TC-08 | SUP-1008 | `chaos_mode: timeout` -> EXECUTION_FAILED, idempotent retry | as demo | as demo |
| `tc09_duplicate_event.json` | TC-09 | SUP-1015 | `send_times: 2` -> one case | PO-1005 | 310,312.00 |
| `tenant_isolation.json` | AC-08 | SUP-3005 (GLOBEX) | ACME credential, other tenant's supplier -> rejected | - | - |

Each scenario file: `{scenario, test_case, description, as_of, event, [decision], [chaos_mode], [send_times], expected}`. Only `event` is sent to the API; the rest is test-runner metadata. TC-06/07/08 reuse the demo data with their own `event_id`s so they can run in one database without tripping the duplicate check.
