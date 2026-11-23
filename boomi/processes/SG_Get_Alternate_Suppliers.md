# SG_Get_Alternate_Suppliers

Purpose: return qualified alternate suppliers for one item with lead time, unit price and capacity. Feeds the Recovery Planner (ALTERNATE_SUPPLIER / SPLIT_ORDER options). Shared steps S1-S6, E1/E2 and extensions: `README.md`.

## Trigger and callers
Start: Web Services Server listener, operation `SG_WSS_GetAlternateSuppliers` (Create, POST), request `SG_JSON_Get_Alternate_Suppliers_Request`, response `SG_JSON_Get_Alternate_Suppliers_Response`. Same JSON for N-08, Agentstudio tools, U-09 proxy.

## Input (`profiles/SG_Get_Alternate_Suppliers.request.json`)
| Field | Required | Notes |
|---|---|---|
| `tenant_id` | yes | sent as `X-Tenant-ID` |
| `item_id` | yes | `ITM-501` |
| `correlation_id` | no | README S2 |

## Shapes in order
1. **Start** WSS listener. 2. **Set Properties "Init"**: `DPP_TENANT_ID`, `DPP_ITEM_ID`, `DPP_CID` + fallback. 2a. **Decision "Tenant valid?"** (README S2a): `DPP_TENANT_ID` non-empty and matches `^TEN-[A-Z0-9]+$`; false -> E3 (`VALIDATION_ERROR`, HTTP 400, `correlation_id`) -> Notify -> Return Documents, NO ERP call. Never call the ERP without `X-Tenant-ID`. 3. **Try/Catch** (if HTTP errors are thrown instead of returned, the Catch branch maps the status as in README "Two possible behaviours").
4. Try: **Set Properties "ERP headers"**.
5. **Connector** `SG_HTTP_MockERP` + `SG_HTTP_GET_AlternateSuppliers`: GET, path `/erp/alternate-suppliers`, query `item_id` = `DPP_ITEM_ID`, `qualified` = static `true`. Response profile `SG_JSON_ERP_AlternateSuppliers_Response` (= `samples/get_alternate_suppliers.200.json`).
6. **Decision** on HTTP status (S5).
7. 200: **Map** `SG_MAP_Get_Alternate_Suppliers`. Rules: `count`; `results[]` 1:1 with `tenant_id, item_id, supplier_id, qualified, lead_time_days, unit_price.amount, unit_price.currency, capacity`; `capacity` null stays null (JSON null, never 0, never defaulted: unknown capacity must reach the planner as unknown); `correlation_id` = `DPP_CID`.
8. 200 -> Set HTTP status 200 -> Notify (`count=`) -> Return Documents.
9. 400 -> pass-through; 401 / 5xx -> E1 502; Catch: timeout -> E2 504, else E1 502.

## Output
- 200: `profiles/SG_Get_Alternate_Suppliers.response.json`. Sample for ITM-501: SUP-2001 (2 days, 84.00 INR, capacity 5000) and SUP-2002 (5 days, 79.50 INR, capacity null).
- Item with no qualified alternates / unknown item: **HTTP 200**, `count: 0, results: []`.
- `count: 0` means "no data / unknown item"; callers must not read it as zero capacity or "no alternates exist". Likewise `capacity: null` is unknown, not zero.
- The row has no supplier name; callers needing it join with `SG_Get_Supplier`.
- Errors per README.
