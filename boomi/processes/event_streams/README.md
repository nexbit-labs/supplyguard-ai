# boomi/processes/event_streams/ - Event Streams topics, publisher, subscriber (Uday, U-08)

Task U-08: three Event Streams topics, a publisher process that Django calls, and a subscriber that starts Nikhil's correlation process (N-08).
Nothing here was built in Boomi by Claude (no Boomi access). These are specs; Uday builds them by hand and exports the components back into this folder. Every Boomi-UI detail marked **(verify)** is from memory of the product and must be checked on screen; if the UI differs, change the spec, not the contract.

Data-pipeline analogy: a topic is a Kafka-style append-only log (a staging table that consumers read at their own pace). The publisher is the ingest job that writes one row per event; the subscriber is the downstream job that reads new rows and kicks off the next ETL step.

## Layout
```
boomi/processes/event_streams/
  README.md                      this file: topics, publisher, subscriber, correlation, idempotency, hand-off decision, checklist
  SG_Disruption_Direct.md        fallback spec (CONTRACTS s8): no Event Streams, listener starts the correlation directly
  profiles/                      JSON used to IMPORT the Boomi JSON profiles
    SG_Event_Disruption.json         body of topic supplier.disruption (CONTRACTS s3, exact)
    SG_Event_ShipmentDelay.json      body of topic shipment.delay (s3 + shipment_id, po_id)
    SG_Event_RecoveryStatus.json     body of topic recovery.status (s3 "Other topics")
    SG_Publish_Event.response.json   what SG_Publish_Event returns to Django (Boomi-internal, Django ignores the body)
  samples/                       example messages to paste into the Event Streams "publish test message" box
  export/                        (after building) exported component XML
```
Boomi component folder `SupplyGuard`, prefix `SG_` (Component Locking is OFF, see `../README.md` naming rules). Shared-component conventions (profiles, error template E1..E3, tenant validation S2a, Notify log lines) are defined in `boomi/processes/README.md` on the U-07 branch; this task reuses the same ideas and does not duplicate the ERP connection.

## 1. Topics

| Topic | Message body (profile) | Producer | Consumer |
|---|---|---|---|
| `supplier.disruption` | canonical event, CONTRACTS s3, exactly the 10 fields `event_id, tenant_id, supplier_id, event_type, severity, event_time, expected_duration_hours, description, source_system, correlation_id` (`profiles/SG_Event_Disruption.json`) | `SG_Publish_Event` (called by Django `BoomiClient.publish_event`) | `SG_Subscribe_Disruption` |
| `shipment.delay` | same shape, `event_type` = `SHIPMENT_DELAY`, plus `shipment_id`, `po_id` (`profiles/SG_Event_ShipmentDelay.json`) | `SG_Publish_Event` (routes by `event_type`/presence of `shipment_id`) | none yet; created so the contract topic exists (open question 3) |
| `recovery.status` | `{risk_case_id, execution_id, status, correlation_id, time}` (`profiles/SG_Event_RecoveryStatus.json`) | `SG_Execute_Approved_Action` (U-10) | none yet (Django could poll or subscribe later; open question 4) |

Settings per topic. Event Streams does not offer every Kafka knob; confirm each field on the create screen **(verify)** and write the real value into `docs/BOOMI_ENV.md` section 3.

| Setting | Value | Why |
|---|---|---|
| Message key | `tenant_id:supplier_id` (as a message property / key, **verify** whether the UI exposes a key; if not, put it in message property `key`) | events for one supplier of one tenant stay in order (a later `QUALITY_HOLD` must not overtake an earlier `FACTORY_OUTAGE`), different tenants never share an ordering lane. For `recovery.status` use `risk_case_id`. |
| Ordering | per key only, not global | the demo has one subscriber; global ordering would cap throughput for no gain |
| Partitions | the default the UI offers (1 is enough for the demo) | one subscriber, a handful of messages per demo |
| Retention | the longest the licence allows, target 7 days (**verify** the limit in a training account) | lets us replay a demo event after fixing a bug; the stored copy of record is Django, the topic is transport, so long retention is a convenience not a requirement |
| Message TTL / expiry | none, or the max | a disruption event must not silently expire before the subscriber (on a laptop runtime that may be off) reads it |
| Message properties on every publish | `correlation_id`, `tenant_id`, `event_id`, `idempotency_key` | the subscriber can filter, log and dedupe without parsing the body |
| Subscription | one durable subscription `SG_Sub_Disruption_Correlate` on `supplier.disruption` | durable = messages sent while the runtime is off are delivered when it comes back |

Never put an API key, token or the `X-Api-Key` value in a message body or property.

## 2. Publisher process `SG_Publish_Event`

What Django sends (read from `backend/apps/integrations/boomi.py` and `services.py`, not guessed):
- `POST {BOOMI_BASE_URL}{BOOMI_EVENT_PATH}`, default path `/ws/simple/createDisruptionEvent`. That is the Boomi WSS listener URL shape for an operation named `createDisruptionEvent` (U-07 uses the same `.../ws/simple/create<Object>` pattern; confirm the exact path on the operation, then set `BOOMI_EVENT_PATH` to match).
- Body: the canonical event (`canonical_event()`): the 10 s3 fields, plus `shipment_id` and `po_id` only when set. `tenant_id` is the credential's tenant. `expected_duration_hours` is a JSON number.
- Headers: `X-Correlation-ID` (= body `correlation_id`), `Idempotency-Key` = `<tenant_id>:<event_id>`, `Accept: application/json`, and either `X-Api-Key: <key>` (`BOOMI_AUTH_MODE=apikey`, default) or HTTP Basic (`BOOMI_AUTH_MODE=basic`, `BOOMI_API_KEY=user:password`).
- Timeouts: connect 3 s, read 5 s, **whole publish capped at 8 s including one retry** (`BOOMI_TOTAL_TIMEOUT_SECONDS`). Django treats any **2xx as success** (the response body is not read), 429/502/503/504 and network errors as transient (retried once), other 4xx as non-retryable (401/403 = AUTH, others VALIDATION), anything else as DOWNSTREAM. Django never follows redirects. So `SG_Publish_Event` must answer in well under 5 s and must not redirect.
- A failed publish marks the event `PUBLISH_FAILED`; Django re-sends it with the same `Idempotency-Key` if the same event_id is posted again. **So the publisher can receive the same key more than once and must be idempotent (section 5).**

Listener: Web Services Server operation `SG_WSS_createDisruptionEvent` (Create / POST, JSON request `SG_JSON_Event_Disruption`, JSON response `SG_JSON_Publish_Event_Response`, Return Application Error Responses ON), exposed as `/ws/simple/createDisruptionEvent`. Auth: Basic (U-09 puts API Management in front; the key header is the contract, CONTRACTS s8 says the listener with basic auth is the fallback when API Management is unavailable).

Shapes, in order:
1. **Start** WSS listener above.
2. **Set Properties "Init"**: `DPP_CID` = body `correlation_id`, else inbound header `X-Correlation-ID`, else Unique Id; `DPP_IDEM` = inbound header `Idempotency-Key`; `DPP_TENANT_ID` = body `tenant_id`; `DPP_EVENT_ID` = body `event_id`; `DPP_EVENT_TYPE` = body `event_type`. (Inbound headers are Dynamic Document Properties of the listener **(verify** exact names: `Inbound HTTP Header > X-Correlation-ID` style).)
3. **Decision "Auth + tenant valid?"** (reject, never publish):
   - Authentication: if the listener enforces Basic auth, an unauthorised call is rejected by the runtime with 401 before the process starts (nothing to build; confirm in the test). If the process is behind API Management (U-09) the gateway does it. If neither is on (a header-key design), a Decision compares inbound `X-Api-Key` to Process Property `SG_PP_Events.publish_key` and answers 401 with error E4 below.
   - Tenant: `DPP_TENANT_ID` matches `^TEN-[A-Z0-9]+$` (same regex rule as U-07 S2a); false -> E3 (400).
   - `DPP_IDEM` must equal `DPP_TENANT_ID + ":" + DPP_EVENT_ID`; mismatch -> E3 (400). Catches a caller that signs one key and sends another event.
4. **Decision "Duplicate?"** (section 5): look up `DPP_IDEM` in the dedupe store. Seen -> go to step 8 with status `DUPLICATE` **without publishing again**.
5. **Decision "Topic?"**: `DPP_EVENT_TYPE` = `SHIPMENT_DELAY` and body has `shipment_id` -> topic `shipment.delay`; else `supplier.disruption`. (One call from Django, two possible topics; the only rule is the CONTRACTS s3 text. See open question 3.)
6. **Try/Catch** around the connector. **Connector** `SG_ES_Events` + operation `SG_ES_Publish` (Event Streams, Produce, topic chosen in step 5; one operation per topic, `SG_ES_Publish_Disruption`, `SG_ES_Publish_Shipment`). Message body = the incoming JSON document, **unchanged**. Message properties from section 3.
7. Catch: publish failed -> E1 (502, `DOWNSTREAM_ERROR`) with `correlation_id`; a timeout -> E2 (504). Django retries only on 429/502/503/504, so these two are the right transient answers. Do **not** record the idempotency key before a successful publish.
8. **Record** `DPP_IDEM` in the dedupe store (after step 6 succeeded). **Message** response `{"correlation_id":"{DPP_CID}","event_id":"{DPP_EVENT_ID}","topic":"...","status":"PUBLISHED"|"DUPLICATE"}` (`profiles/SG_Publish_Event.response.json`), HTTP **200** (Django accepts any 2xx).
9. **Notify** `SG_Publish_Event cid={DPP_CID} tenant={DPP_TENANT_ID} event={DPP_EVENT_ID} topic=.. status=..` -> **Return Documents**. Never log the body (description is free text) or any key.

Error bodies use the CONTRACTS s1 shape: E1 `DOWNSTREAM_ERROR` 502, E2 `DOWNSTREAM_TIMEOUT` 504, E3 `VALIDATION_ERROR` 400, E4 `UNAUTHENTICATED` 401 (`samples/error.unauthorized.401.json`), each with `correlation_id`.

## 3. Subscriber process `SG_Subscribe_Disruption`

Trigger: Start shape = Connector, `SG_ES_Events` + operation `SG_ES_Listen_Disruption` (Event Streams, **Listen**, topic `supplier.disruption`, subscription `SG_Sub_Disruption_Correlate`, **verify** whether the UI calls the mode "Listen" or "Consume"). The process is deployed to the runtime and starts by itself for every message.

Shapes:
1. **Start** Event Streams listen operation (output document = the message body).
2. **Set Properties "Init"**: `DPP_CID` = body `correlation_id` (cross-check against message property `correlation_id`; if they differ, use the body and log a warning); `DPP_TENANT_ID`, `DPP_EVENT_ID`, `DPP_IDEM` = `tenant_id:event_id` from the body.
3. **Decision "Already processed?"** (section 5): key `DPP_IDEM` in the processed store. Seen -> Notify `skipped duplicate` -> **Stop (continue)**; do not call N-08.
4. **Try/Catch**. Try: **Process Call** (or HTTP Client, see section 6) to Nikhil's `SG_Disruption_Correlate` passing **the event document unchanged**, plus the dynamic process property `DPP_CID` (a Process Call carries dynamic properties with the document; over HTTP send `X-Correlation-ID` and the same body).
5. On success: **Record** `DPP_IDEM` in the processed store, then **Notify** `SG_Subscribe_Disruption cid=.. tenant=.. event=.. status=handed_to_correlate`.
6. Catch: **do not acknowledge as done and do not swallow**. Route the original document, the error class and `DPP_CID` to the dead-letter path (U-11 owns it; until then, **Throw Exception** so Event Streams redelivers or flags it, and Notify `status=failed cid=..`). Never put a secret or the whole body in the error text. The idempotency key is only recorded on success, so a redelivered message is processed again, as it should be.

N-08 contract this relies on (from `tasks/NIKHIL.md` N-08): process `SG_Disruption_Correlate` takes "event" as input; its input profile must equal `profiles/SG_Event_Disruption.json`. The subscriber does no mapping: if N-08 needs another shape, change N-08, not the topic (the topic body is a contract).

## 4. `correlation_id` end to end

| Hop | How it travels | Where to check |
|---|---|---|
| Django -> `SG_Publish_Event` | header `X-Correlation-ID` and body `correlation_id` (same UUID; Django uses `event.correlation_id`) | Boomi process log line `SG_Publish_Event cid=..` |
| `SG_Publish_Event` | Dynamic Process Property `DPP_CID`: body, else header, else generated | Process Reporting > Document properties |
| Publisher -> topic | message body field `correlation_id` (unchanged) **and** message property `correlation_id` | Event Streams message viewer |
| Topic -> subscriber | `DPP_CID` re-set from the body; mismatch with the property is logged | `SG_Subscribe_Disruption cid=..` |
| Subscriber -> `SG_Disruption_Correlate` | Process Call: DPP travels; HTTP: `X-Correlation-ID` header + body | N-08 log line (Nikhil) |
| Every Notify/log line | `cid=` first, then `tenant=`, `event=`; never keys or bodies | Process Reporting log |

Rule: nobody generates a new `correlation_id` for an event that already has one. Only the publisher may generate one, and only if both header and body are empty (Django always sends both, so this is a safety net).

## 5. Idempotency on `tenant_id:event_id`

Two layers:
1. **Django** dedupes on `(tenant, event_id)` before it publishes (a duplicate API call never reaches Boomi). It re-sends only an event whose publish failed, with the same `Idempotency-Key`.
2. **Boomi**: the key `tenant_id:event_id` is checked in the publisher (do not publish twice) and in the subscriber (do not correlate twice; Event Streams delivery is at-least-once).

Dedupe store, simplest option first, all inside the training-account limits (no extra connector; the 3+3 connection budget is in section 7):
- **(a) Process-local persisted property / cache (recommended for the demo):** Boomi *Process Property* or *Document Cache* component keyed by `DPP_IDEM`, persisted on the runtime. Pros: no extra connection, runnable on the local runtime. Cons: lost if the runtime's working data is cleared; it is per environment, so a second runtime would not share it.
- **(b) Database / Django lookup:** ask Django (an endpoint on `integrations`, owner Uday) whether `tenant:event` is already `PUBLISHED`. Pros: one source of truth. Cons: a new endpoint and a call per message; out of scope for this specs piece.
The recommendation is (a), with Django's own dedupe as the real safety net: the worst case after a cache loss is one duplicate message, and N-08 / `POST /internal/risk-cases` is itself idempotent on `event_id` (CONTRACTS s3: duplicate event_id -> existing case, **confirm with Nikhil that `/internal/risk-cases` keeps that behaviour**, open question 5).

## 6. Subscriber hand-off to N-08: explicit decision, not solved

BOOMI_ENV.md section 9: Boomi processes cannot call processes in another account, and topics are per account. Topics and `SG_Subscribe_Disruption` live in Uday's account (`trainingvisinigiriudaykir-VL9DS4`, runtime `ESI-PC28`); `SG_Disruption_Correlate` is built in Nikhil's own account. A Process Call from Uday's subscriber to Nikhil's process is therefore **not possible as it stands**.

| | Option A: import N-08 into Uday's account | Option B: HTTPS between the two accounts |
|---|---|---|
| How | Nikhil exports `SG_Disruption_Correlate` and its profiles to `boomi/processes/correlation/export/`; Uday imports them, rewires the connections (his `SG_Get_*` calls become Process Calls or localhost HTTP) and Process-Calls it from the subscriber | Nikhil exposes `SG_Disruption_Correlate` as a WSS listener in his account and a URL reachable from Uday's runtime; `SG_Subscribe_Disruption` calls it with HTTP Client (`X-Correlation-ID`, body unchanged) |
| For | One account, one runtime: Process Call, no network hop, the 2 s budget is easy, no extra connection, one place to look at logs. Matches "exports under `boomi/` are the hand-off format". | Each member keeps their account. No import step. |
| Against | Nikhil's later edits must be re-imported by hand; Component Locking is OFF so edits in two accounts drift; N-08 uses Agentstudio (N-05/N-06 agents) which lives in **Nikhil's** account, so the imported copy still has to call agents in his account over HTTPS. | One more HTTP Client connection in Uday's account (budget: 2 Test slots left, section 7); Nikhil's runtime must be on and reachable (his laptop, firewall/tunnel; the listener port is not public); two runtimes must be online during demos. |
| Cost to build | ~0.5 day, mostly Nikhil | ~0.5 day, mostly network setup |
| Recommendation | **A** for the hackathon demo (fewer moving parts), noting the agent calls stay HTTPS to Nikhil's account (his decision for N-09) | choose B if Nikhil keeps editing N-08 daily |

Decision needed from Uday and Nikhil before N-08 is wired. **Until then the spec in section 3 stays valid for both options; only step 4 changes** (Process Call for A, HTTP Client for B). For a demo without any cross-account step, see `SG_Disruption_Direct.md`.

## 7. Connection budget (3 Standard + 3 Test total; BIMBA already uses 1 Test; U-07 plans 1 Test for `SG_HTTP_MockERP`)

| Connection | Needed? | Notes |
|---|---|---|
| `SG_ES_Events` (Event Streams) | **yes, 1** | one connection is shared by all operations (publish disruption, publish shipment, listen); only operations differ. A WSS listener does not count as a connection component. |
| `SG_HTTP_MockERP` | already planned (U-07) | not part of U-08 |
| Option B only: `SG_HTTP_Correlate` | +1 | would leave 0 Test slots; so prefer option A |
| Total after U-07 + U-08 (option A) | BIMBA 1 + ERP 1 + Events 1 = 3 Test | exactly the 3 Test slots; U-09 (API Management proxy) and U-10 (execute action) must then **reuse** these or use production slots. Check it on Manage > Licensing before building. **(verify** whether Event Streams counts as a Test or Standard connection in this account) |

## 8. Environment extensions
`SG_ES_Events` credentials/token (Event Streams environment token) are set under Manage > Environments > Extensions, never in the component or git. `SG_PP_Events.publish_key` (Password, only if a header key is checked in Boomi). Nothing in this folder contains a key or a real URL.

## 9. When to use the fallback
Use `SG_Disruption_Direct.md` (CONTRACTS s8: "Event Streams -> Boomi process triggered directly by API Management endpoint") only if one of these is true on the day: Event Streams is not enabled or shows no environment; the connection budget cannot take `SG_ES_Events`; topic create/produce fails after one retry; or the cross-account hand-off (section 6) is unresolved and a live demo is due. The payload, the headers and the idempotency key are identical, so Django needs no change. Cost: no replay and no fan-out to a second subscriber, and the "Event Streams" slide must say "fallback".

## 10. Manual checklist for Uday (Boomi UI, ~60-90 min, nothing here has been run)
1. **Pre-checks.** Manage > Licensing: note free connection slots (section 7). Event Streams: open it (`Services > Event Streams`), confirm your environment `BIMBA-OPCUA-Test` is listed (as it was on 2026-11-19). If Event Streams does not open, stop and use the fallback.
2. **Create the three topics** in Event Streams: Create Topic -> name `supplier.disruption`, then `shipment.delay`, then `recovery.status`. Take the defaults for partitions; set retention as in section 1. Write down what the screen really offered (key, retention limit) into `docs/BOOMI_ENV.md` section 3.
3. **Create the subscription** `SG_Sub_Disruption_Correlate` on `supplier.disruption` (Subscriptions tab -> Create). Keep it durable if offered.
4. **Create the token / credentials** Event Streams shows for the environment (Event Streams > Environment > Token or Manage > Account). Do not paste it into git or chat; it goes into the extension in step 7.
5. **Create JSON profiles** in the `SupplyGuard` folder: Create > Profile > JSON > Import, paste each `profiles/*.json` -> `SG_JSON_Event_Disruption`, `SG_JSON_Event_ShipmentDelay`, `SG_JSON_Event_RecoveryStatus`, `SG_JSON_Publish_Event_Response`.
6. **Create the connection** `SG_ES_Events` (Connector: Boomi Event Streams) plus operations `SG_ES_Publish_Disruption`, `SG_ES_Publish_Shipment`, `SG_ES_Listen_Disruption` (topic/subscription per sections 2-3), and the WSS operation `SG_WSS_createDisruptionEvent`.
7. **Build `SG_Publish_Event`** and **`SG_Subscribe_Disruption`** from sections 2 and 3. For the N-08 call, leave a **Stop** shape and a Notify as a placeholder until the section 6 decision is made. Attach everything to the environment and set the extensions (token).
8. **Deploy** both processes to the runtime `ESI-PC28` (keep the PC awake). Confirm the listener is Online (Runtime Management > Listeners) and read the real URL of `SG_WSS_createDisruptionEvent`.
9. **Publish a test message with curl** against the listener (replace host/port and the credential with your own; do not paste them in git):
   `curl -i -X POST <listener-url>/ws/simple/createDisruptionEvent -H "Content-Type: application/json" -H "X-Correlation-ID: 6f1c0a52-3b8e-4d7a-9c1e-2a5f7b9d4e10" -H "Idempotency-Key: TEN-ACME:EVT-000123" -H "X-Api-Key: <key>" --data @samples/disruption.factory_outage.json`
   Expect HTTP 200 and `status: PUBLISHED`.
10. **Observe**: Event Streams > `supplier.disruption` shows the message with the properties of section 1; Process Reporting shows `SG_Subscribe_Disruption` started within about 2 s with the same `cid`. Send the same curl again: expect `DUPLICATE`, no second subscriber run.
11. **Django side**: set `BOOMI_BASE_URL` (the listener base), `BOOMI_EVENT_PATH` and `BOOMI_API_KEY` in your `.env`; from the app run `POST /api/v1/demo/simulate-disruption`; check `GET /integrations/status` and that the event's `publish_status` becomes `PUBLISHED`.
12. **Run the scenarios** in `boomi/TEST_LOG.md` (U-08 TEST PLAN), change each `NOT RUN` to date/PASS|FAIL, then export the components to `export/` (View XML or Platform API) and check `grep -ri "x-api-key\|token" export/` shows only header names. Tell Nikhil the outcome of the section 6 decision.

## Open questions
1. **Cross-account hand-off (section 6):** option A (import N-08 into Uday's account) or B (HTTPS to Nikhil's account)? Needs Uday + Nikhil.
2. **Event Streams limits in a training account:** real retention, key/partition options, whether it consumes a Test or Standard connection slot, and whether "Listen" is the right operation name (all marked verify).
3. **`shipment.delay`:** CONTRACTS s3 only says "same shape plus `shipment_id`, `po_id`". Is the routing rule in `SG_Publish_Event` (type `SHIPMENT_DELAY` + `shipment_id`) right, and which process consumes it? Django currently only sends to one path.
4. **`recovery.status`:** allowed values of `status` are not listed in CONTRACTS (sample uses `SUCCEEDED`, matching N-10's execution outcomes) and `time` format is assumed ISO-8601 UTC. Who consumes it? U-10 produces it.
5. Does N-04 `POST /internal/risk-cases` stay idempotent on `event_id`? The dedupe store in section 5 is only a cache if so.
6. Dedupe store: confirm option (a) (Document Cache / persisted property) is acceptable, or ask for a Django lookup endpoint.
7. Exact listener path for `createDisruptionEvent` and whether Django's `BOOMI_EVENT_PATH` default matches (must be set from the real URL after step 8).
8. The publisher answers HTTP 200; confirm that nothing in Django expects 202.
9. `SG_Publish_Event.response` fields `topic` and `status` are added by this spec (Django ignores the body, so no contract change); drop them if you prefer an empty 200.
