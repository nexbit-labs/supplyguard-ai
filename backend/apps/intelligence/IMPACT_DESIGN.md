# N-04 Impact + risk scoring engine - design spec

Owner: Nikhil. Status: implemented in `impact.py` (branch `nikhil/N-04-impact-engine--impl`); Q1-Q3 resolved (see s8).
Implement exactly this; anything not stated here is out of scope for N-04.

**In pipeline terms:** `impact.py` is one pure transform step. Input = one event record + one ERP extract
(supplier, open POs with lines, inventory rows, alternates). Output = one "impact" record. No DB, no HTTP,
no clock, no randomness: same input -> same output, so it is trivially unit-testable and reusable by
`POST /internal/risk-cases` (later, after U-03), N-07 (fact check) and N-11 (what-if on a fresh snapshot).

Sources this spec is built from: `docs/CONTRACTS.md` s3, s4 (`POST /internal/risk-cases`), s5, s6;
`ai/schemas/agent_input.schema.json` (`$defs.impact`), `ai/schemas/common.schema.json` (`money`);
`data/README.md` ("Impact rule behind the expected numbers", demo table); `data/scenarios/*.json`;
`data/seed/*.json`; `mock-erp/app/main.py` (response shapes).

---

## 1. Input

### 1.1 `event` - CONTRACTS s3 canonical event (dict, as received)
Fields read: `tenant_id`, `supplier_id`, `event_type`, `severity`, `event_time` (`YYYY-MM-DDTHH:MM:SS[.f]Z`),
`expected_duration_hours` (number >= 0), optional `po_id` (on `shipment.delay` events). Everything else is ignored.

### 1.2 `snapshot` - the ERP extract, exactly as the mock ERP / Boomi returns it
```python
snapshot = {
    "captured_at": "2026-12-02T10:15:03Z",          # optional, not used by the math
    "supplier": {...} | None,                        # GET /erp/suppliers/{id}  -> plain object (404 -> None)
    "purchase_orders": {"count": 3, "results": [...]},   # GET /erp/purchase-orders?supplier_id=&status=OPEN
    "inventory": {"count": 2, "results": [...]},         # GET /erp/inventory?item_id=  (one call per item, results concatenated)
    "alternate_suppliers": {"count": 2, "results": [...]},  # GET /erp/alternate-suppliers?item_id=
}
```
- Each of the three collections may be a `{count, results}` page **or** a plain list (the agent_input
  `snapshot` stores plain lists). Normalise with `rows()` (s2). `count` is not trusted; `results` is.
- Row shapes are the seed shapes (`data/seed/*.json`), keys used:
  - PO: `po_id`, `tenant_id`, `supplier_id`, `status`, `lines[]` -> `line_no`, `item_id`, `qty` (int), `due_date` (`YYYY-MM-DD`), `unit_price` `{amount: str, currency}`.
  - inventory: `tenant_id`, `item_id`, `location_id`, `on_hand`, `reserved`, `safety_stock`, `daily_demand` (units/day; may be absent/null in other ERPs).
  - alternate: `tenant_id`, `item_id`, `supplier_id`, `qualified` (bool), `lead_time_days`, `unit_price`, `capacity` (units/week, null = unknown).
  - supplier: `supplier_id`, `name`, `capacity_per_week` (null = unknown). Only used for data-quality flags.
- `tenant_id` may be missing on rows (agent snapshots strip nothing today, but don't rely on it). A row whose
  `tenant_id` is present **and different** from `event.tenant_id` -> raise `SnapshotError` (it is an upstream
  tenant-isolation bug; never silently drop it).

### 1.3 `thresholds` - tenant parameters (optional, defaults below)
```python
@dataclass(frozen=True)
class TenantThresholds:
    medium: int = 40                   # tenant.settings.risk_thresholds.medium
    high: int = 65                     # ...high
    critical: int = 85                 # ...critical
    exposure_full_scale: Decimal = Decimal("500000")   # exposure (tenant currency) that scores 100% of the exposure weight
    delay_full_scale_days: int = 5     # delay that scores 100% of the delay weight
    recovery_buffer_days: int = 0      # extra days added to every line delay (supplier ramp-up); 0 = none
    require_qualified_alternates: bool = True   # tenant.settings.require_qualified_alternates
    currency: str = "INR"              # tenant.currency; used only when there are no affected lines
```
`thresholds_from_tenant(tenant: dict | None) -> TenantThresholds` maps a `tenants.json` row
(`settings.risk_thresholds.{medium,high,critical}`, `settings.require_qualified_alternates`, `currency`,
optional `settings.risk_thresholds.exposure_full_scale` (per-tenant exposure scale, sits next to 40/65/85; legacy fallback `settings.exposure_full_scale`), `settings.delay_full_scale_days`, `settings.recovery_buffer_days`);
missing keys fall back to the defaults. Validate `0 <= medium <= high <= critical <= 100` else `ValueError`.

---

## 2. Rules and pure functions (`backend/apps/intelligence/impact.py`)

Conventions: all money and ratios are `decimal.Decimal` internally (build from the **string** amount, never
from float). Money out = `{"amount": "<2dp string>", "currency": "XXX"}` quantized with `ROUND_HALF_UP`.
Dates: `due_date` is treated as `00:00:00Z` of that day. No `datetime.now()` anywhere.

```python
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

class SnapshotError(ValueError): ...

@dataclass(frozen=True)
class DisruptionWindow:
    start: datetime          # event_time (aware, UTC)
    end: datetime            # start + expected_duration_hours
    outage_days: Decimal     # expected_duration_hours / 24 (unrounded)

@dataclass(frozen=True)
class AffectedLine:
    po_id: str
    line_no: int
    item_id: str
    qty: int
    due_date: date
    unit_price: Decimal
    currency: str
    delay_days: int
    exposure: Decimal

def rows(collection: dict | list | None) -> list[dict]
def thresholds_from_tenant(tenant: dict | None) -> TenantThresholds
def parse_window(event: dict) -> DisruptionWindow
def select_evaluated_pos(event: dict, purchase_orders: list[dict]) -> list[dict]
def line_delay_days(due: date, window: DisruptionWindow, recovery_buffer_days: int = 0) -> int
def select_affected_lines(evaluated_pos: list[dict], window: DisruptionWindow,
                          recovery_buffer_days: int = 0) -> list[AffectedLine]
def inventory_coverage(item_id: str, inventory: list[dict], outage_days: Decimal) -> list[dict]
def item_min_coverage(coverage_rows: list[dict]) -> Decimal | None
def shortage_gap_ratio(min_coverage: Decimal | None, has_inventory_rows: bool,
                       outage_days: Decimal) -> Decimal | None
def mitigation_level(item_id: str, affected_qty: int, alternates: list[dict],
                     require_qualified: bool = True) -> Decimal
def score_components(*, item_gaps: dict[str, Decimal | None], max_delay_days: int,
                     total_exposure: Decimal, mitigation: dict[str, Decimal],
                     severity: str, t: TenantThresholds) -> dict[str, Decimal]
def risk_score(components: dict[str, Decimal]) -> int
def severity_band(score: int, t: TenantThresholds) -> str
def money(amount: Decimal, currency: str) -> dict
def compute_impact(event: dict, snapshot: dict, thresholds: TenantThresholds | None = None) -> dict
def to_agent_impact(result: dict) -> dict
```

### 2.1 Disruption window - `parse_window`
`start = event_time`, `end = start + timedelta(hours=expected_duration_hours)`,
`outage_days = Decimal(str(expected_duration_hours)) / 24`. Negative duration or unparsable time -> `ValueError`.

### 2.2 Evaluated POs - `select_evaluated_pos` (TC-03)
**Rule:** every PO with `status == "OPEN"` and `supplier_id == event.supplier_id` (and same tenant, s1.2) is
evaluated. If `event.po_id` is present (shipment events) the set is narrowed to that one PO (empty if it is not
open / not this supplier). Re-filter even though Boomi asks for `status=OPEN`: a sloppy extract must not leak
RECEIVED/CANCELLED POs (demo: PO-1004 is RECEIVED and must be ignored). Output sorted by numeric PO suffix.

### 2.3 Affected lines - `select_affected_lines`
**Rule (data/README.md):** a line is affected iff `due_date <= end.date()` (date of disruption end, UTC).
This includes overdue lines (`due_date` before `event_time`): they are already late and get later.
A PO is affected iff it has at least one affected line. Lines due after the window are not affected
(PO-1003, PO-1012), even though the PO was evaluated.

- `qty` must be an int >= 0 (bool/negative -> `SnapshotError`). `qty == 0` lines are skipped (nothing to deliver).
- `exposure = qty * unit_price` (Decimal). `unit_price == 0` is allowed -> exposure `0.00` + flag (s2.9).
- All affected lines must share one currency, else `SnapshotError("mixed currencies ...")`.

### 2.4 Delay - `line_delay_days`
```
delay_days = max(1, ceil((end - due_00:00Z).total_seconds() / 86400)) + recovery_buffer_days
```
i.e. the supplier cannot deliver before the disruption ends, so the earliest arrival is `end`; the delay is the
gap between the promised date and `end`, rounded **up** to whole days, minimum 1 for an affected line.
Integer arithmetic on seconds (no float `ceil` on hours). Only called for affected lines.
PO-row delay = max over its lines for that item; case `estimated_delay_days` = max over all affected lines.

### 2.5 Inventory coverage - `inventory_coverage` (one dict per (item, location) row of that item)
```
available_qty        = on_hand - reserved - safety_stock          (int, may be negative)
coverage_days        = max(0, available_qty) / daily_demand       (Decimal; None if daily_demand missing/None/0)
coverage_days_gross  = (on_hand - reserved) / daily_demand         (data/README.md definition; None likewise)
transfer_surplus_qty = max(0, available_qty - ceil(daily_demand * outage_days))   (None if daily_demand missing/0)
```
Safety stock is protected: shortage decisions use `coverage_days` (net). `coverage_days_gross` is emitted only so
the README demo numbers stay reproducible (see s4.1 discrepancy). `daily_demand == 0` means "no consumption here":
coverage None, no flag. Missing/None `daily_demand` means "unknown": coverage None **and** a flag.
Rows sorted by `location_id`. Output values: coverage numbers rounded to 2 dp (`float` in JSON is fine for these,
schema type is `number`), computed unrounded.

`item_min_coverage(rows)` = min `coverage_days` over rows where it is not None; None if no such row.
**Why min, not pooled:** POs carry no ship-to location, so the worst DC is the binding constraint. Moving stock
between DCs is a recovery option (INVENTORY_TRANSFER), so the engine must not assume it; it only reports
`transfer_surplus_qty` per location as a candidate.

### 2.6 Shortage - `shortage_gap_ratio` (TC-04)
Stock must last from `event_time` until supply resumes, i.e. `outage_days`.
| situation | `shortage` | gap ratio |
|---|---|---|
| no inventory rows at all for the item (tenant-wide) | `True` | `1` (+ flag "no inventory record for ITM-x") |
| rows exist, `min_coverage` is None (demand unknown everywhere) | `False` (never claim an unsupported shortage) | `None` = unknown (+ flag) |
| `min_coverage < outage_days` | `True` | `min(1, (outage_days - min_coverage) / outage_days)` |
| `min_coverage >= outage_days` (incl. `outage_days == 0`) | `False` | `0` |

`shortage` is per item; every affected-PO row of that item carries the same flag and the item's `min_coverage`
as `inventory_coverage_days`.

### 2.7 Mitigation level - `mitigation_level` (0 = fully mitigable, 1 = nothing)
Candidates = alternates for `item_id`, excluding the disrupted supplier, filtered to `qualified == True` when
`require_qualified` (default). `known = sum(capacity for c in candidates if capacity is not None)`.
| condition (first match) | level |
|---|---|
| `known >= affected_qty` and `known > 0` | `0` |
| `known > 0` | `0.25` (partial) |
| candidates exist but all capacity null | `0.5` (unknown) |
| no candidates | `1` |
Capacity is units/week; we compare it with the affected qty without time scaling on purpose (simple, explainable;
recovery planning is the agent's job). Null capacity -> flag "capacity unknown for alternate SUP-x (ITM-y)".

### 2.8 Risk score - `score_components`, `risk_score`, `severity_band`
Five components, each a Decimal in `[0, 1]`, weighted to 100 points:

| component | weight | value |
|---|---|---|
| `shortage` | 40 | if any item has `shortage`: `0.5 + 0.5 * max(gap over shortage items)`; elif any item gap is None (unknown): `0.25`; else `0` |
| `exposure` | 20 | `min(1, total_exposure / exposure_full_scale)` |
| `mitigation` | 15 | `max(mitigation_level over affected items)`; `0` if no affected items |
| `delay` | 15 | `min(1, max_delay_days / delay_full_scale_days)`; `0` if no affected lines |
| `severity` | 10 | event severity `LOW 0.25, MEDIUM 0.5, HIGH 0.75, CRITICAL 1.0` (unknown value -> `ValueError`) |

```
raw        = 40*shortage + 20*exposure + 15*mitigation + 15*delay + 10*severity
risk_score = int(clamp(raw, 0, 100).quantize(Decimal("1"), ROUND_HALF_UP))
risk_level = CRITICAL if score >= critical, HIGH if >= high, MEDIUM if >= medium, else LOW
```
Weights are module constants `WEIGHTS = {"shortage": 40, "exposure": 20, "mitigation": 15, "delay": 15, "severity": 10}`
(sum asserted == 100 at import). Rationale: a confirmed shortage is what stops a production line, so it dominates;
money and missing alternates come next; the supplier's own severity label is a weak signal (10%).
With no affected lines the score is just the severity part (max 10 -> LOW).

### 2.9 Data-quality flags (feed agent_input `data_quality_flags`; agents echo them into `missing_data`)
Exact strings, in this order, de-duplicated:
1. `supplier not found in ERP` (snapshot supplier is None)
2. `supplier capacity not provided` (supplier `capacity_per_week` is None or key missing)
3. `daily_demand missing for {item_id} at {location_id}` (per affected-item row)
4. `no inventory record for {item_id}`
5. `capacity unknown for alternate {supplier_id} ({item_id})` (only for candidates of affected items)
6. `unit_price is zero for {po_id} line {line_no}`

### 2.10 Supplier not found (TC-02)
`snapshot["supplier"] is None` -> return the empty result: `supplier_found: False`, all lists empty,
`total_exposure` 0.00 in `thresholds.currency`, `risk_score` 0, `risk_level` `LOW`, flags `["supplier not found in ERP"]`.
Do not compute components (severity is not scored for an unknown supplier).

---

## 3. Output

### 3.1 `compute_impact(...)` -> full engine result (stored with the case, shown in UI, used by N-07/N-11)
```json
{
  "supplier_found": true,
  "window": {"start": "2026-12-02T10:15:00Z", "end": "2026-12-04T10:15:00Z", "outage_days": 2.0},
  "evaluated_po_ids": ["PO-1001", "PO-1002", "PO-1003"],
  "affected_po_ids": ["PO-1001", "PO-1002"],
  "affected_item_ids": ["ITM-501"],
  "affected_pos": [
    {"po_id": "PO-1001", "item_id": "ITM-501", "line_nos": [1], "due_date": "2026-12-03",
     "affected_qty": 4000, "estimated_delay_days": 2,
     "exposure_amount": {"amount": "302400.00", "currency": "INR"},
     "inventory_coverage_days": 1.0, "shortage": true}
  ],
  "items": [
    {"item_id": "ITM-501", "affected_qty": 6500, "min_coverage_days": 1.0, "shortage": true,
     "shortage_gap_ratio": 0.5, "mitigation_level": 0.25,
     "candidate_alternate_supplier_ids": ["SUP-2001", "SUP-2002"]}
  ],
  "inventory_coverage": [
    {"item_id": "ITM-501", "location_id": "DC-CHENNAI", "on_hand": 800, "reserved": 100, "safety_stock": 200,
     "daily_demand": 150, "available_qty": 500, "coverage_days": 3.33, "coverage_days_gross": 4.67,
     "transfer_surplus_qty": 200}
  ],
  "qualified_alternate_supplier_ids": ["SUP-2001", "SUP-2002"],
  "total_exposure": {"amount": "491400.00", "currency": "INR"},
  "estimated_delay_days": 2,
  "shortage": true,
  "score_components": {
    "shortage":   {"weight": 40, "value": "0.7500", "points": "30.00"},
    "exposure":   {"weight": 20, "value": "0.9828", "points": "19.66"},
    "mitigation": {"weight": 15, "value": "0.2500", "points": "3.75"},
    "delay":      {"weight": 15, "value": "0.4000", "points": "6.00"},
    "severity":   {"weight": 10, "value": "0.7500", "points": "7.50"}
  },
  "risk_score": 67,
  "risk_level": "HIGH",
  "data_quality_flags": ["supplier capacity not provided", "capacity unknown for alternate SUP-2002 (ITM-501)"]
}
```
- `affected_pos`: one row per **(po_id, item_id)** (the schema allows one `item_id` per row). Several lines of the
  same item in one PO are summed (`affected_qty`, `exposure_amount`), `estimated_delay_days` = max, `line_nos`
  lists them, `due_date` = earliest. A PO with two items -> two rows. Sorted by PO numeric suffix, then item_id.
- `items` sorted by item_id; `inventory_coverage` by (item_id, location_id), only for affected items.
- `qualified_alternate_supplier_ids` = sorted union of `candidate_alternate_supplier_ids` (matches the scenario
  `expected.qualified_alternate_supplier_ids` key).
- `score_components.value` 4 dp string, `points` 2 dp string (display only; the score is computed from unrounded values).
- `money()` always 2 dp (`"25555.50"`, not `"25555.5"`).

### 3.2 `to_agent_impact(result)` -> agent_input `impact` block (strict, `additionalProperties: false`)
```json
{"risk_score": 67, "total_exposure": {"amount": "491400.00", "currency": "INR"},
 "affected_pos": [{"po_id": "PO-1001", "item_id": "ITM-501", "affected_qty": 4000, "estimated_delay_days": 2,
                   "exposure_amount": {"amount": "302400.00", "currency": "INR"},
                   "inventory_coverage_days": 1.0, "shortage": true}]}
```
Exactly the 7 keys per row required by `agent_input.schema.json#/$defs/impact`; drop `line_nos`/`due_date`.
`data_quality_flags` goes to the agent_input top level (the service does that, not this function).
A test must validate this block against the schema (`ai/schemas`, `referencing` registry as in `ai/tests`).

Mapping to the LLD `AffectedPO` model (U-03, later): `affected_qty` -> `affected_qty`, `estimated_delay_days` ->
`estimated_delay` (days), `exposure_amount` -> `exposure_amount`; `risk_score`/`risk_level` -> RiskCase. That wiring is
the "service" piece, not N-04 impl.

---

## 4. Worked examples (hand-computed from `data/seed/`, as_of 2026-12-02)

### 4.1 DEMO - SUP-1008, FACTORY_OUTAGE, HIGH, 48 h (`demo.json`)
Window 2026-12-02T10:15Z -> 2026-12-04T10:15Z, outage_days 2.
| PO | status | line | due | affected? | delay | exposure |
|---|---|---|---|---|---|---|
| PO-1001 | OPEN | ITM-501 x 4000 @ 75.60 | 12-03 | yes | 34.25 h -> **2** | 302,400.00 |
| PO-1002 | OPEN | ITM-501 x 2500 @ 75.60 | 12-04 | yes | 10.25 h -> **1** | 189,000.00 |
| PO-1003 | OPEN | ITM-502 x 1500 @ 120.00 | 12-20 | no | - | - |
| PO-1004 | RECEIVED | - | - | not evaluated | - | - |

Evaluated 3, affected 2, exposure **INR 491,400.00** (6500 x 75.60). **Matches README.**

Inventory ITM-501:
| DC | on_hand | reserved | safety | demand | available | coverage (net) | gross (README) | surplus |
|---|---|---|---|---|---|---|---|---|
| DC-CHENNAI | 800 | 100 | 200 | 150 | 500 | 3.33 | 4.67 | 200 |
| DC-PUNE | 1100 | 200 | 300 | 600 | 600 | 1.00 | 1.50 | 0 |

min coverage 1.00 < 2 -> **shortage** (matches README `shortage_expected: true`), gap (2-1)/2 = 0.5.
**Discrepancy:** README says "DC-PUNE covers 1.5 days, DC-CHENNAI 4.7 days" - that is the gross formula
`(on_hand - reserved)/daily_demand` from the README "Impact rule". This spec uses net-of-safety-stock (1.00 / 3.33)
for decisions and also emits the gross numbers. Both lead to the same verdicts (PUNE short, CHENNAI transfer
candidate). Open question Q1.

Alternates ITM-501: SUP-2001 (qualified, cap 5000), SUP-2002 (qualified, cap null) -> known 5000 < 6500 -> level 0.25.
Both are listed as qualified -> **matches README** (2 alternates). Supplier capacity null -> flag (README: "agents must report it as missing data").

Score: shortage 40 x 0.75 = 30.00; exposure 20 x 491400/500000 = 19.656; mitigation 15 x 0.25 = 3.75;
delay 15 x 2/5 = 6.00; severity 10 x 0.75 = 7.50 -> raw 66.906 -> **67 HIGH**.

### 4.2 TC-03 - SUP-1020, CRITICAL, 72 h (`tc03_multiple_open_pos.json`)
Window end 2026-12-05T10:15Z, outage 3. Evaluated PO-1007..PO-1012 (6). PO-1012 (ITM-521, due 12-27) not affected.
| PO | item | qty | due | delay | exposure |
|---|---|---|---|---|---|
| PO-1007 | ITM-520 | 500 | 12-03 | 3 | 64,715.00 |
| PO-1008 | ITM-521 | 300 | 12-03 | 3 | 82,185.00 |
| PO-1009 | ITM-522 | 700 | 12-04 | 2 | 332,836.00 |
| PO-1010 | ITM-523 | 450 | 12-04 | 2 | 25,555.50 |
| PO-1011 | ITM-520 | 250 | 12-05 | 1 | 32,357.50 |
Total **537,649.00** (matches README/scenario). Each item has one DC-DELHI row 400/100/100/150 -> available 200,
coverage 1.33 (gross 2.00) < 3 -> shortage, gap 0.5556. Alternate SUP-2004 qualified cap 4000 >= qty -> mitigation 0.
Score: 40 x 0.7778 = 31.11; exposure min(1, 1.075) = 20; mitigation 0; delay 15 x 3/5 = 9; severity 10 -> 70.11 -> **70 HIGH**.

### 4.3 TC-04 - SUP-1030, HIGH, 72 h (`tc04_inventory_sufficient.json`)
PO-1013 (ITM-530 x 2000, due 12-03, delay 3, 839,300.00), PO-1014 (x 1500, due 12-04, delay 2, 629,475.00).
Total **1,468,775.00** (matches). ITM-530 DC-PUNE 20000/1000/2000/400 -> available 17000, coverage **42.50**
(gross 47.50 = README "~47") >= 3 -> **no shortage** (TC-04 passes). SUP-2005 cap 6000 >= 3500 -> mitigation 0.
Score: 0 + 20 + 0 + 9 + 7.5 = 36.5 -> ROUND_HALF_UP -> **37 LOW**. (Exact .5: the rounding test case.)

### 4.4 TC-05 - SUP-1040, HIGH, 96 h (`tc05_no_alternates.json`)
PO-1015 ITM-540 x 1200 @ 395.32 due 12-03 -> 82.25 h -> delay **4**, exposure **474,384.00** (matches).
ITM-540 DC-PUNE 150/100/0/100 -> available 50, coverage 0.50 < 4 -> shortage, gap 0.875. No alternates -> mitigation 1.
Supplier capacity null -> flag. `qualified_alternate_supplier_ids: []`.
Score: 40 x 0.9375 = 37.5; 20 x 0.948768 = 18.98; 15; 15 x 4/5 = 12; 7.5 -> 90.975 -> **91 CRITICAL**.

### 4.5 TC-01 - SUP-1015, MEDIUM, 24 h (bonus, `tc01_valid_event.json`)
PO-1005 ITM-515 x 800 due 12-03, end 12-03T10:15 -> delay 1, exposure **310,312.00** (matches). PO-1006 RECEIVED ignored.
ITM-515 DC-PUNE 250/50/50/250 -> coverage 0.60 < 1 -> shortage (scenario `shortage_expected: true`), gap 0.4.
SUP-2003 cap 2000 >= 800 -> 0. Score 28 + 12.41 + 0 + 3 + 5 = 48.41 -> **48 MEDIUM**.

Summary: every README / scenario exposure, evaluated set, affected set, alternate set and shortage flag is
reproduced. The only disagreement is the README coverage-days formula (gross vs net), see Q1.

---

## 5. Test table (`backend/apps/intelligence/tests/test_impact.py`)
Scenario tests load `data/seed/*.json` + `data/scenarios/*.json` directly (wrap lists as `{count, results}` to
exercise the page path) and assert against the scenario `expected` block + the numbers in s4.
Unit tests use tiny inline dicts. Target >= 90% line coverage of `impact.py` (`pytest --cov=apps.intelligence.impact`).

| # | case | input | expected |
|---|---|---|---|
| S1 | demo end-to-end | demo event + seed | evaluated [1001,1002,1003], affected [1001,1002], exposure "491400.00" INR, items [ITM-501], shortage true, alternates [SUP-2001,SUP-2002], delays 2/1, score 67, HIGH |
| S2 | TC-03 all open POs | tc03 + seed | evaluated 6, affected 5 (PO-1012 excluded), exposure "537649.00", 4 items, score 70 HIGH |
| S3 | TC-04 no shortage | tc04 + seed | shortage false on every row, coverage 42.5, exposure "1468775.00", score 37 LOW |
| S4 | TC-05 no alternates | tc05 + seed | alternates [], mitigation component 1, shortage true, score 91 CRITICAL, flag "supplier capacity not provided" |
| S5 | TC-01 | tc01 + seed | affected [PO-1005], "310312.00", shortage true, score 48 MEDIUM |
| S6 | TC-02 unknown supplier | supplier None | supplier_found false, empty lists, score 0 LOW, flag "supplier not found in ERP" |
| S7 | agent block valid | `to_agent_impact(S1..S5)` | validates against `agent_input.schema.json#/$defs/impact`; rows have exactly 7 keys |
| U1 | `rows` | page dict / list / None | list / list / [] |
| U2 | status filter | OPEN + RECEIVED + CANCELLED PO | only OPEN evaluated |
| U3 | other supplier PO in extract | PO of SUP-9 | not evaluated |
| U4 | `event.po_id` narrowing | 2 open POs, event po_id = 2nd | evaluated = [2nd] |
| U5 | tenant mismatch | PO with tenant TEN-GLOBEX | raises `SnapshotError` |
| U6 | window boundary | due == end date; due == end date + 1 | affected; not affected |
| U7 | overdue line | due 5 days before event, 24 h outage | affected, delay 7 (ceil of 6.43 d) |
| U8 | delay min 1 | end exactly 00:00Z on due date (duration 24 h, event 00:00Z) | delay 1 |
| U9 | recovery buffer | buffer 2 | every delay +2 |
| U10 | no open POs | supplier ok, POs [] | affected [], exposure "0.00" in thresholds.currency, score = severity points only, LOW |
| U11 | multi-line, same item | PO lines (ITM-A 10 @ 1.00 due d1), (ITM-A 5 @ 2.00 due d2) | one row, qty 15, exposure "20.00", line_nos [1,2], delay = max, due = min |
| U12 | multi-line, two items | lines ITM-A, ITM-B | two rows, same po_id |
| U13 | line after window in multi-line PO | 1 in, 1 out | only in-window line counted |
| U14 | zero price | unit_price "0.00" | exposure "0.00", flag "unit_price is zero for PO-x line 1" |
| U15 | qty 0 line | qty 0 | skipped, not affected |
| U16 | bad qty | qty -1 or True | `SnapshotError` |
| U17 | mixed currency | INR + USD lines | `SnapshotError` |
| U18 | money rounding | 3 @ "0.335" (4-dp snapshot price) | "1.01" (ROUND_HALF_UP) |
| U19 | multiple locations | DC1 cov 5, DC2 cov 1, outage 2 | min 1 -> shortage, gap 0.5; DC1 surplus reported |
| U20 | missing daily_demand | row without key / None | coverage None, flag, shortage false, shortage component 0.25 if no other shortage |
| U21 | daily_demand 0 | demand 0 | coverage None, no flag |
| U22 | negative available | safety > on_hand - reserved | available negative, coverage 0, shortage |
| U23 | no inventory rows | item absent | shortage true, gap 1, flag "no inventory record for ITM-x" |
| U24 | outage 0 h | duration 0 | outage_days 0, no shortage, only overdue/today lines affected |
| U25 | null supplier capacity | capacity_per_week None / key missing / 1000 | flag / flag / no flag |
| U26 | mitigation levels | known >= qty / 0<known<qty / all null / none / unqualified only (require True) / unqualified only (require False) | 0 / 0.25 / 0.5 / 1 / 1 / per capacity |
| U27 | disrupted supplier in alternates | alternate row with event supplier_id | excluded |
| U28 | null alternate capacity flag | SUP-2002 | "capacity unknown for alternate SUP-2002 (ITM-501)" |
| U29 | severity mapping | LOW/MEDIUM/HIGH/CRITICAL/"FOO" | 0.25/0.5/0.75/1/`ValueError` |
| U30 | clamping | exposure 2x full scale, delay 20 d | components 1, 1 |
| U31 | score rounding | raw 36.5 / 36.49 | 37 / 36 |
| U32 | bands | 39,40,64,65,84,85 with defaults; custom thresholds | LOW,MEDIUM,MEDIUM,HIGH,HIGH,CRITICAL |
| U33 | `thresholds_from_tenant` | seed tenant / None / partial settings / medium > high | values / defaults / merged / `ValueError` |
| U34 | bad event | negative duration, bad event_time | `ValueError` |
| U35 | weights | `WEIGHTS` | sum 100 |
| U36 | determinism + no mutation | call twice, deep-copy inputs before | identical output, inputs unchanged |
| U37 | flag order / dedupe | several flags, duplicates | order of s2.9, unique |

---

## 6. Edge cases (decisions, one line each)
- **No open POs** (or none in the window): valid result, empty `affected_pos`, exposure 0.00, score = severity points (<= 10, LOW). The service decides whether a case is created (TC-03 `risk_case_expected` is for cases with impact).
- **Missing `daily_demand`**: coverage None, never claim a shortage from it, flag it, score it as "unverified" (0.25 of the shortage weight).
- **Null capacity** (supplier or alternate): flag it, never guess; alternate with null capacity counts as "unknown" (0.5) in mitigation.
- **Zero price**: exposure 0.00, flag; the line still counts as affected (qty and delay are real).
- **Multiple lines per PO**: rows per (PO, item), summed; different items -> separate rows.
- **Multiple locations**: per-location coverage; the item's coverage is the **minimum**; surplus at other DCs is reported, not assumed.
- **Overdue OPEN lines**: affected, delay grows with lateness.
- **Unknown supplier**: empty result + flag (TC-02).
- **Tenant mismatch / mixed currency / bad qty**: `SnapshotError` - an upstream bug, fail loudly.

## 7. Out of scope (later pieces)
- Persistence (RiskCase/AffectedPO), endpoint, auth, tenant from API key -> "service wiring" piece after U-03.
- Fact-checking agent output against the snapshot -> N-07. What-if endpoint -> N-11 (calls `compute_impact`).
- Incoming supply from other suppliers' open POs, partial deliveries, DC-to-PO allocation: not modelled (documented assumption).

## 8. Decisions on the former open questions (Nikhil)
- **Q1 coverage formula - decided: net of safety stock** for decisions (PUNE 1.00, CHENNAI 3.33 days); gross is still
  emitted. `data/README.md` demo row updated.
- **Q2 exposure scale per tenant - decided: per-tenant parameter** `settings.risk_thresholds.exposure_full_scale`
  (default 500000, same object as medium/high/critical so each tenant can override it). To be added to tenant settings
  in U-03 / CONTRACTS; until then the default applies.
- **Q3 demo label - decided: weights are not tuned**; the demo scores 67 HIGH.
