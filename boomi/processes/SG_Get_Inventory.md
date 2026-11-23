# SG_Get_Inventory

Purpose: return on-hand, reserved, safety stock and daily demand of one item at every location. Feeds days-of-cover and inventory-transfer options. Shared steps S1-S6, E1/E2 and extensions: `README.md`.

## Trigger and callers
Start: Web Services Server listener, operation `SG_WSS_GetInventory` (Create, POST), request `SG_JSON_Get_Inventory_Request`, response `SG_JSON_Get_Inventory_Response`. Same JSON for N-08, Agentstudio tools, U-09 proxy.

## Input (`profiles/SG_Get_Inventory.request.json`)
| Field | Required | Notes |
|---|---|---|
| `tenant_id` | yes | sent as `X-Tenant-ID` |
| `item_id` | yes | `ITM-501` |
| `correlation_id` | no | README S2 |

`location_id` is deliberately not an input: an empty query value would make the ERP filter to zero rows. Add it only with a Decision that omits the parameter when blank.

## Shapes in order
1. **Start** WSS listener. 2. **Set Properties "Init"**: `DPP_TENANT_ID`, `DPP_ITEM_ID`, `DPP_CID` + fallback. 3. **Try/Catch**.
4. Try: **Set Properties "ERP headers"**.
5. **Connector** `SG_HTTP_MockERP` + `SG_HTTP_GET_Inventory`: GET, path `/erp/inventory`, query `item_id` = `DPP_ITEM_ID`. Response profile `SG_JSON_ERP_Inventory_Response` (= `samples/get_inventory.200.json`).
6. **Decision** on HTTP status (S5).
7. 200: **Map** `SG_MAP_Get_Inventory`. Rules: `count`; `results[]` 1:1 with `tenant_id, item_id, item_name, location_id, on_hand, reserved, safety_stock, daily_demand` (all integers stay numbers); `correlation_id` = `DPP_CID`. Do not compute available stock here (`on_hand - reserved` is the consumer's job; keep the ERP values as evidence).
8. 200 -> Set HTTP status 200 -> Notify (`count=`) -> Return Documents.
9. 400 -> pass-through; 401 / 5xx -> E1 502; Catch: timeout -> E2 504, else E1 502.

## Output
- 200: `profiles/SG_Get_Inventory.response.json` (one row per location).
- Unknown item: **HTTP 200**, `count: 0, results: []` (`samples/get_inventory.empty.json`) - the ERP does not 404 here. The caller must state "no inventory data" and not assume zero stock.
- Errors per README.
