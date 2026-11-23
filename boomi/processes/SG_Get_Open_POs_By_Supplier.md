# SG_Get_Open_POs_By_Supplier

Purpose: return every OPEN purchase order (with lines) of one supplier. Used by correlation (N-08) to find affected POs. Shared steps S1-S6, E1/E2 and extensions: `README.md`.

## Trigger and callers
Start: Web Services Server listener, operation `SG_WSS_GetOpenPOsBySupplier` (Create, POST), request `SG_JSON_Get_Open_POs_By_Supplier_Request`, response `SG_JSON_Get_Open_POs_By_Supplier_Response`. Same JSON for N-08, Agentstudio tools, U-09 proxy.

## Input (`profiles/SG_Get_Open_POs_By_Supplier.request.json`)
| Field | Required | Notes |
|---|---|---|
| `tenant_id` | yes | sent as `X-Tenant-ID` |
| `supplier_id` | yes | must match `SUP-<digits>`, else the ERP returns 400 `VALIDATION_ERROR` (passed through) |
| `correlation_id` | no | as in README S2 |

The status filter is fixed to `OPEN` in the operation (CONTRACTS s5); it is not an input.

## Shapes in order
1. **Start** WSS listener (S1). 2. **Set Properties "Init"** (S2): `DPP_TENANT_ID`, `DPP_SUPPLIER_ID`, `DPP_CID` + fallback. 2a. **Decision "Tenant valid?"** (README S2a): `DPP_TENANT_ID` non-empty and matches `^TEN-[A-Z0-9]+$`; false -> E3 (`VALIDATION_ERROR`, HTTP 400, `correlation_id`) -> Notify -> Return Documents, NO ERP call. Never call the ERP without `X-Tenant-ID`. 3. **Try/Catch** (S3); if HTTP errors are thrown instead of returned, the Catch branch maps the status as in README "Two possible behaviours".
4. Try: **Set Properties "ERP headers"**.
5. **Connector** `SG_HTTP_MockERP` + `SG_HTTP_GET_PurchaseOrders`: GET, path `/erp/purchase-orders`, query parameters `supplier_id` = `DPP_SUPPLIER_ID`, `status` = static `OPEN`. Response profile `SG_JSON_ERP_PurchaseOrders_Response` (= `samples/get_purchase_orders.200.json`).
6. **Decision** on HTTP status (S5).
7. 200: **Map** `SG_MAP_Get_Open_POs_By_Supplier` -> `SG_JSON_Get_Open_POs_By_Supplier_Response`. Rules: `count` = `count`; `results[]` loops 1:1 from ERP `results[]` with fields `po_id, tenant_id, supplier_id, order_date, expected_date, status, amount.amount, amount.currency` and `lines[]` (`line_no, item_id, item_name, qty, due_date, unit_price.amount, unit_price.currency`); `correlation_id` = `DPP_CID`. Dates stay `YYYY-MM-DD` strings, money stays string (no number conversion).
8. 200 -> Set HTTP status 200 -> Notify (add `count=`) -> Return Documents.
9. 400 (bad id format) -> pass-through error, HTTP 400. 401 / 5xx -> E1, 502. Catch: timeout -> E2 504, else E1 502.

## Output
- 200 with POs: `profiles/SG_Get_Open_POs_By_Supplier.response.json` (sample trimmed to 2 POs).
- Supplier with no open POs, or unknown supplier id with valid format: **HTTP 200** `{"correlation_id":"...","count":0,"results":[]}` (`samples/get_purchase_orders.empty.json`). This process does NOT return 404; callers who need to know the supplier exists call `SG_Get_Supplier`.
- Errors per README.
