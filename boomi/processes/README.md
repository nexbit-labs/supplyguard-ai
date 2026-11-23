# boomi/processes/ - Integration process specs (Uday)

Task U-07: four read-only processes that fetch data from the mock ERP and return canonical JSON.
Nothing here was built in Boomi by Claude (no Boomi access). Specs are click-by-click; Uday builds them and exports the components back into this folder.

## Layout
```
boomi/processes/
  README.md                          this file: shared components, shared build pattern, checklist
  SG_Get_Supplier.md                 build spec
  SG_Get_Open_POs_By_Supplier.md     build spec
  SG_Get_Inventory.md                build spec
  SG_Get_Alternate_Suppliers.md      build spec
  profiles/                          sample JSON used to IMPORT the Boomi JSON profiles (request, response, error)
  samples/                           raw mock-ERP responses captured from a real run (uvicorn, seed data)
  export/                            (after building) exported component XML, one file per component
  correlation/                       Nikhil's N-08 - not touched here
```

## Naming (Component Locking is OFF, so the prefix is the lock)
Folder `SupplyGuard` in the Boomi account. Every component starts with `SG_`.

| Kind | Name |
|---|---|
| Processes | `SG_Get_Supplier`, `SG_Get_Open_POs_By_Supplier`, `SG_Get_Inventory`, `SG_Get_Alternate_Suppliers` |
| Connection (ONE, shared) | `SG_HTTP_MockERP` |
| Process Property component (shared) | `SG_PP_MockERP` |
| HTTP Client operations | `SG_HTTP_GET_Supplier`, `SG_HTTP_GET_PurchaseOrders`, `SG_HTTP_GET_Inventory`, `SG_HTTP_GET_AlternateSuppliers` |
| WSS listener operations | `SG_WSS_GetSupplier`, `SG_WSS_GetOpenPOsBySupplier`, `SG_WSS_GetInventory`, `SG_WSS_GetAlternateSuppliers` |
| JSON profiles | `SG_JSON_<Process>_Request`, `SG_JSON_<Process>_Response`, `SG_JSON_Error`, `SG_JSON_ERP_<Entity>_Response` (the raw ERP reply) |
| Maps | `SG_MAP_<Process>` |

Quota: the account has 3 Standard + 2 Test connection slots, so all four processes reuse `SG_HTTP_MockERP`; only the operations differ. Do not create a connection per process.

## Shared connection and environment extensions
`SG_HTTP_MockERP` (Connector: HTTP Client). Nothing environment-specific is stored in the component; everything below is set under Manage > Environments > Extensions (Connections / Process Properties).

| Field | Where | Local runtime (Uday, ESI-PC28) | Hosted runtime |
|---|---|---|---|
| Base URL | connection extension `URL` | `http://localhost:8081` (mock-erp default port, see `mock-erp/README.md`) | `https://<erp-host-or-tunnel>` (U-12) |
| Connect timeout | connection extension | `5000` ms | `5000` ms |
| Read timeout | connection extension | `30000` ms (CONTRACTS chaos hang is >30 s; see TEST_LOG scenario about reads) | `30000` ms |
| ERP API key | Process Property `SG_PP_MockERP.erp_api_key`, **Password** type, extensible; sent as header `X-Api-Key` | the value of `MOCK_ERP_API_KEY` the ERP was started with (leave the ERP unauthenticated locally only if you must; then the header is ignored) | real key, never in git |

The base `URL` field on the connection itself holds only a harmless default (`http://localhost:8081`); the real value is always the extension. Authentication on the connection stays `None` because the mock ERP wants a custom header, not Basic/OAuth. The API key is never logged and never appears in these files. (Verify in the UI that a Password-type process property can be made an extension; if not, fall back to a Text property with the key in the extension and mark it sensitive.)

## Tenancy decision (for Uday to confirm)
CONTRACTS section 1 says a system caller's credential maps to a tenant and tenant is never taken from the body. These processes deviate from that, knowingly:
- (a) Decision: for the demo the listener trusts the calling backend / proxy to supply the `tenant_id` it derived from the user's JWT, in the request body. The process validates its format (S2a) and forwards it as `X-Tenant-ID`.
- (b) Risk: the WSS listener uses one Basic-auth credential with no tenant binding, so any holder of that credential can read any tenant by changing `tenant_id`.
- (c) Mitigation planned in U-09: the API Management proxy / key policy binds each credential to one tenant and rejects a body `tenant_id` that does not match (403); the listener is not exposed publicly (only the runtime host / proxy can reach it).
- (d) This is a conscious deviation from CONTRACTS section 1 until U-09 lands. CONTRACTS is not edited here. Uday: confirm or choose an alternative (e.g. one listener credential per tenant now).

## Shared build pattern (all four processes follow it; each spec lists only what differs)

Common steps, referenced from the specs as S1..S6:

**S1 Start (Web Services Server listener).** Why a listener: it is an HTTP endpoint Nikhil's N-08 (HTTP Client or Process Call), Agentstudio Integration tools and the API Management proxy (U-09, CONTRACTS section 8 fallback "listener with basic auth") can all call with the same JSON. Operation type `Create` (POST), JSON request profile in, JSON response profile out, Return Application Error Responses ON. The full URL is shown on the operation; the shape is `.../ws/simple/create<ObjectName>` (confirm in the UI). Auth on the listener: User/Basic auth now, replaced by the API proxy in U-09.

**S2 Set Properties "Init".** Rows, in order: `DPP_TENANT_ID` = request profile element `tenant_id`; `DPP_<KEY>` = the id element(s) of this process; `DPP_CID` = request element `correlation_id`. Then Decision "DPP_CID is empty?" -> true: Set Properties `DPP_CID` = inbound HTTP header `X-Correlation-ID` (Dynamic Document Property of the listener); if still empty a second Set Properties uses Unique Id. Order of precedence: header/body from the caller, else a generated UUID. The tenant is supplied in the body by the trusted backend/proxy (never typed by an end user); see "Tenancy decision".

**S2a Tenant validation Decision (mandatory, right after S2; its false branch makes NO ERP call).** Decision "Tenant valid?": `DPP_TENANT_ID` is not empty AND matches regex `^TEN-[A-Z0-9]+$` (Decision shape does not do regex: use a Data Process Custom Scripting step or a Decision with "Starts With" `TEN-` plus a Groovy/regex check; the regex is the rule). False branch -> Message error E3 -> HTTP status 400 -> Notify -> Return Documents:
```
E3: {"error":{"code":"VALIDATION_ERROR","message":"tenant_id is required and must match TEN-[A-Z0-9]+","details":{"field":"tenant_id"},"correlation_id":"{2}"}}
```
(same shape as `profiles/SG_Error.response.json`). Rule: **the process must never call the ERP without the `X-Tenant-ID` header set.** The mock ERP treats a missing header as "all tenants" (it would return other tenants' data), and Boomi may drop an empty header value, so this check is the only guard. Every spec's shape list contains this Decision as step 2a.

**S3 Try/Catch** around steps S4-S5. All error handling for connection/timeout failures happens on the Catch path (S6).

**S4 Set Properties "ERP headers" then HTTP Client Connector (GET).** Operation `SG_HTTP_GET_*`: method GET, connection `SG_HTTP_MockERP`, **Return HTTP Errors = ON if the option exists (see "Two possible behaviours" below)** (so 4xx/5xx come back as documents, not exceptions), response profile `SG_JSON_ERP_*_Response`, Request Headers defined: `Accept: application/json`, `X-Correlation-ID`, `X-Tenant-ID`, `X-Api-Key`. Header values: X-Correlation-ID = `DPP_CID`, X-Tenant-ID = `DPP_TENANT_ID`, X-Api-Key = `SG_PP_MockERP.erp_api_key`. Path/query values come from the DPPs in the Connector shape's Parameters tab. No `Idempotency-Key` (GET only). Retries: none; do not wrap the call in a retry loop (4xx must never retry; 5xx is surfaced to the caller, whose own policy decides).

**Two possible behaviours of 4xx/5xx (check which one the UI shows; TEST_LOG row "error behaviour").** (A) If the operation has the option "Return HTTP Errors" and it is ON, 4xx/5xx arrive as normal documents and S5 maps them as written. (B) If the option does not exist or is OFF, an HTTP error is thrown into the Try/Catch. Then the Catch branch (S6) must read the status first, from the Connector DDP "HTTP Status Code" if it is populated, else by parsing the Try/Catch error message (which contains the status code), and apply the SAME mapping as S5: 404/400 -> pass-through error with the same status; 401 and 5xx -> E1 (502); "timed out" -> E2 (504); other (refused, DNS, SSL) -> E1 `unreachable`. For (B) the ERP response body is usually not available, so for 404/400 rebuild the error with a Message shape (`NOT_FOUND` / `VALIDATION_ERROR`, `message` "not found at ERP" / "invalid request", `correlation_id` = `DPP_CID`). The DDP labels named in this spec are unconfirmed; use whatever the Set Properties list actually offers.

**S5 Decision on HTTP status** (DDP "Connector > HTTP Status Code"; compare as number):
- `200` -> Map `SG_MAP_*` -> S5a success.
- `404` or `400` -> the ERP body is already the CONTRACTS section 1 error body: Map `SG_JSON_Error` -> `SG_JSON_Error` unchanged (a pass-through map; set `correlation_id` from `DPP_CID`), listener HTTP status = the same code (404 / 400). Caller sees `NOT_FOUND` / `VALIDATION_ERROR`.
- `401` -> do NOT forward `UNAUTHENTICATED` (it would look like the caller's own auth failed). Build error E1 (`DOWNSTREAM_ERROR`, details `{"erp_status":401}`), HTTP 502. Notify: ERP credential wrong.
- anything else (5xx, e.g. chaos `INTERNAL_ERROR`) -> error E1 with `erp_status` = the code, HTTP 502.
- **S5a success path:** map, then Set Properties listener HTTP status `200`, then Notify (log line), then Return Documents.

**S6 Catch path.** Decision on Try/Catch error message (DDP "Try/Catch Error Message"): contains `timed out` -> error E2 (`DOWNSTREAM_TIMEOUT`, HTTP 504); otherwise (connection refused, DNS, SSL) -> E1 (`DOWNSTREAM_ERROR`, HTTP 502, `details.reason` = "unreachable"). Both end in Notify then Return Documents.

**Error documents** are built with a Message shape (type JSON, variables from DPPs) so no map is needed:
```
E1: {"error":{"code":"DOWNSTREAM_ERROR","message":"mock ERP call failed","details":{"erp_status":"{1}"},"correlation_id":"{2}"}}
E2: {"error":{"code":"DOWNSTREAM_TIMEOUT","message":"mock ERP did not answer in time","details":{},"correlation_id":"{2}"}}
```
`{1}` = DDP "Connector > HTTP Status Code" (or `unreachable` on the Catch path), `{2}` = `DPP_CID`. Set the listener response status with Set Properties just before Return Documents (DDP "Web Services Server > HTTP Response Status Code"; label may differ slightly in the UI).

**Logging (Notify shape, level INFO, one per process end):** `SG_Get_<X> cid=<DPP_CID> tenant=<DPP_TENANT_ID> key=<id> erp_status=<code> count=<n>`. Never log the API key, request headers, or response bodies. Leave connector request/response logging off.

**Response contract.** Every success response carries `correlation_id`. Lists keep the ERP envelope `count` + `results`; the supplier process wraps the record in `supplier`. Field names are the mock ERP's (see Contract vs mock ERP differences); amounts stay `{amount, currency}` strings per CONTRACTS section 1. Empty list = HTTP 200 with `count: 0, results: []` (not an error).

## Contract vs mock ERP differences (captured from the real running mock ERP, 23 Nov 2026)
CONTRACTS section 5 is a one-line table; the real API (mock-erp/app) is a superset. Nothing contradicts; these are the details to code against. CONTRACTS is not edited here (S-01 owns that).

| Topic | CONTRACTS s5 says | Mock ERP actually does | Recommendation |
|---|---|---|---|
| Response envelope | not stated | List endpoints return `{"count": n, "results": [...]}`; supplier returns the bare record | Keep the envelope; add to CONTRACTS s5 |
| Auth / tenant headers | s1: `X-Api-Key` -> tenant | `X-Api-Key` only checked when the ERP has `MOCK_ERP_API_KEY` set; tenant comes from optional header `X-Tenant-ID` (TEN-<A-Z0-9>); absent = all tenants. Unknown tenant -> 404 `NOT_FOUND` "unknown tenant". Other tenant's supplier -> 404 | Boomi always sends `X-Tenant-ID` (so a wrong tenant never leaks data); document header in CONTRACTS s5 (already an open item in mock-erp/README) |
| Tenant header strictness | `X-Tenant-ID` not mentioned in s5 | It is optional in the ERP, but **effectively required on reads for tenant isolation**: without it the ERP returns all tenants. The ERP rejects values outside `TEN-[A-Z0-9]+` with 400 | Recommended follow-up for CONTRACTS s5 (owner Nikhil / all three, not edited here): state that Boomi always sends it. The Boomi processes enforce this themselves (README S2a) |
| PO shape | "lines: item_id, qty, due_date, unit_price" | PO header: `po_id, tenant_id, supplier_id, order_date, expected_date, status, amount{amount,currency}, lines[]`; line: `line_no, item_id, item_name, qty, due_date, unit_price{amount,currency}`. Date field is `due_date` on lines, `expected_date` on header (no `expiry_date`) | Use as captured |
| Inventory | "on_hand, reserved, safety_stock per location" | also `tenant_id, item_id, item_name, location_id, daily_demand`; one row per (item, location) | Use as captured; `daily_demand` is needed for days-of-cover |
| Alternates | "qualified alternates with lead_time_days, unit_price, capacity" | row = `tenant_id, item_id, supplier_id, qualified, lead_time_days, unit_price{amount,currency}, capacity`; `capacity` is null when unknown (units/week); **no supplier name**; the `qualified` filter is optional and defaults to all rows | Boomi sends `qualified=true`; callers must treat `capacity: null` as UNKNOWN and never invent a number |
| Supplier record | "supplier master" | `supplier_id, tenant_id, name, tax_id, category, city, country, status, risk_level, on_time_delivery_pct, quality_score, capacity_per_week (nullable), contact_email, disruption_history[{date,event_type,delay_days}]` | Use as captured |
| Unknown ids | not stated | unknown supplier -> 404 `NOT_FOUND`. Unknown `item_id` (inventory, alternates) or supplier with no OPEN POs -> **200 with `count: 0`**, not 404. PO list only validates `supplier_id` against `^SUP-[0-9]+$` (400 `VALIDATION_ERROR` otherwise) | Empty array is passed through; callers decide whether "no rows" is an error |
| Errors | s1 error body | Matches s1 exactly (also on 401). Chaos 500 uses code `INTERNAL_ERROR` (not in s1; Boomi maps it to `DOWNSTREAM_ERROR`) | Add `INTERNAL_ERROR` to s1 (already an open item) |
| `_chaos` | "test only" | **Affects PATCH only; reads are never slowed or failed.** | The DOWNSTREAM_TIMEOUT test for READ processes cannot use `_chaos`; see TEST_LOG U-07 plan (blackhole URL). Optional: ask Nikhil for a read chaos mode |

## Export the components back into git (after building)
1. For each component in the `SupplyGuard` folder, open it and use its View XML option (component menu; wording varies by UI version), or fetch it with the Platform API `GET /Component/<id>` using your own credentials, and copy the XML.
2. Save as `boomi/processes/export/<ComponentName>.xml` (processes, maps, profiles, operations, `SG_HTTP_MockERP` with the URL left as the default only, `SG_PP_MockERP` with an empty key).
3. Check that no file contains the API key or a real hostname (`grep -ri x-api-key boomi/processes/export` should show only the header name).
4. Commit on the task branch; record runs in `boomi/TEST_LOG.md`.

## Manual checklist for Uday (in the Boomi UI, about 60-90 min for all four)
1. Decide the environment (U-01 open item): reuse `BIMBA-OPCUA-Test` or create `SG-Dev`, attached to the local runtime on ESI-PC28. Create folder `SupplyGuard`.
2. Start the mock ERP: `cd mock-erp && MOCK_ERP_API_KEY=<key> uvicorn app.main:app --port 8081`. Check `http://localhost:8081/health`.
3. Create JSON profiles by importing `profiles/*.json` (Create > Profile > JSON > Import > paste file). Raw ERP replies: import each `samples/get_*.200.json` as `SG_JSON_ERP_<Entity>_Response`. Names per the table above.
4. Create connection `SG_HTTP_MockERP` (HTTP Client, URL default `http://localhost:8081`, auth None) and Process Property component `SG_PP_MockERP` with `erp_api_key` (Password).
5. Create the four HTTP Client operations and four WSS operations (specs).
6. Build the four processes following their spec files, one at a time; the first (`SG_Get_Supplier`) establishes the pattern, copy it for the others (Create > Clone, then change operation, path/query and maps).
7. Attach everything to the environment. Under Environment Extensions set connection `URL` and timeouts, and `SG_PP_MockERP.erp_api_key`.
8. Deploy the four processes to the runtime (packaged component or direct deploy).
9. Run every scenario in `boomi/TEST_LOG.md` (U-07 TEST PLAN), change `NOT RUN` to PASS/FAIL, date and your name.
10. Export the XML (section above), commit, and tell Nikhil the four listener URLs for N-08 (and BOOMI_ENV.md section 9 hand-off if his account differs).
