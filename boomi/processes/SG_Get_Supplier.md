# SG_Get_Supplier

Purpose: return one supplier master record from the mock ERP as canonical JSON. First process to build; the others clone it. Shared steps S1-S6, error templates E1/E2 and extensions are in `README.md`.

## Trigger and callers
Start shape: Web Services Server listener, operation `SG_WSS_GetSupplier` (Create, POST), request `SG_JSON_Get_Supplier_Request`, response `SG_JSON_Get_Supplier_Response` (README S1). Callable by N-08 (HTTP Client or Process Call), Agentstudio Integration tools and the U-09 API proxy with the same JSON body.

## Input (profile: `profiles/SG_Get_Supplier.request.json`)
| Field | Required | Notes |
|---|---|---|
| `tenant_id` | yes | `TEN-ACME`; set by the trusted caller, sent to the ERP as `X-Tenant-ID` |
| `supplier_id` | yes | `SUP-1001` |
| `correlation_id` | no | else header `X-Correlation-ID`, else generated |

## Shapes in order
1. **Start** WSS listener (S1).
2. **Set Properties "Init"** (S2): `DPP_TENANT_ID`, `DPP_SUPPLIER_ID` (= `supplier_id`), `DPP_CID`; correlation fallback decision.
3. **Try/Catch** (S3).
4. Try path: **Set Properties "ERP headers"** (S4 headers).
5. **Connector** `SG_HTTP_MockERP` + operation `SG_HTTP_GET_Supplier`: GET, path `/erp/suppliers/{1}`, path element 1 = `DPP_SUPPLIER_ID` (Parameters tab). Response profile `SG_JSON_ERP_Supplier_Response` (= `samples/get_supplier.200.json`).
6. **Decision** on HTTP status (S5): 200 / 404+400 / 401 / other.
7. 200: **Map** `SG_MAP_Get_Supplier` from `SG_JSON_ERP_Supplier_Response` to `SG_JSON_Get_Supplier_Response`. Rules: every ERP field maps 1:1 into `supplier.*` (`supplier_id, tenant_id, name, tax_id, category, city, country, status, risk_level, on_time_delivery_pct, quality_score, capacity_per_week, contact_email, disruption_history[]{date,event_type,delay_days}`); `correlation_id` = process property `DPP_CID`. Null stays null (`capacity_per_week` null = unknown; do not default it).
8. 200: **Set Properties** HTTP status 200 -> **Notify** `SG_Get_Supplier cid=.. tenant=.. key=<supplier_id> erp_status=200` -> **Return Documents**.
9. 404/400: pass-through error map (S5) with listener status 404/400 -> Notify -> Return Documents.
10. 401 and other codes: Message E1 with HTTP 502 -> Notify -> Return Documents.
11. Catch path (S6): timeout -> E2 (504); else E1 (502) -> Notify -> Return Documents.

## Output
- 200: `profiles/SG_Get_Supplier.response.json` shape: `{"correlation_id": "...", "supplier": {...}}`.
- 404 (unknown supplier, or a supplier of another tenant): `{"error":{"code":"NOT_FOUND","message":"supplier SUP-9999 not found","details":{"supplier_id":"SUP-9999"},"correlation_id":"..."}}` (`samples/get_supplier.404.json`).
- 502 `DOWNSTREAM_ERROR`, 504 `DOWNSTREAM_TIMEOUT` per README.

## Correlation and logging
`DPP_CID` goes to the ERP as `X-Correlation-ID` (ERP echoes it) and into every response/error body. Log line contains ids and status only; never `X-Api-Key`.
