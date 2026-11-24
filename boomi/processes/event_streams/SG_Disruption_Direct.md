# SG_Disruption_Direct (fallback, CONTRACTS section 8)

Purpose: the same work as `SG_Publish_Event` + `SG_Subscribe_Disruption`, with **no Event Streams**. CONTRACTS s8: "Event Streams -> Boomi process triggered directly by API Management endpoint". Spec only; nothing was built or run.

## When to use it
Use it only if on the day one of these holds, and say "fallback" in the demo:
- Event Streams does not open, shows no environment, or topic create/produce fails after one retry;
- `SG_ES_Events` does not fit the connection budget (see `README.md` section 7);
- the cross-account hand-off to N-08 (README section 6) is unresolved and a live demo is due (with option A the listener can Process-Call N-08 directly).
Do not use it when Event Streams works: it loses replay, retention and fan-out to a second subscriber, and "publish within 2 s" becomes "listener -> correlate" latency.

## Contract (identical to the normal path, so Django changes nothing)
- Listener operation `SG_WSS_createDisruptionEvent`, POST `/ws/simple/createDisruptionEvent`, request profile `SG_JSON_Event_Disruption` (`profiles/SG_Event_Disruption.json`, CONTRACTS s3 fields exactly), same headers as README section 2: `X-Correlation-ID`, `Idempotency-Key` = `tenant_id:event_id`, `X-Api-Key` (or Basic).
- Response: HTTP 200 with `profiles/SG_Publish_Event.response.json` (`status` `PUBLISHED` meaning "accepted and handed on"), or an error body of CONTRACTS s1. Django accepts any 2xx and has an 8 s total budget, so the listener must **answer quickly**.

## Shapes
1. **Start** WSS listener (same as README section 2 step 1).
2. **Set Properties "Init"** and **Decision "Auth + tenant valid?"**: exactly README section 2 steps 2-3 (`DPP_CID`, `DPP_IDEM`, `DPP_TENANT_ID`, `DPP_EVENT_ID`; reject 401/400, never process).
3. **Decision "Duplicate?"** on `DPP_IDEM` (README section 5, same store): seen -> response `DUPLICATE`, stop.
4. **Branch (2 paths)**:
   - Path 1: **Set Properties** `status=PUBLISHED` -> **Notify** -> **Return Documents** (Django gets its answer immediately; this is the "accepted" reply).
   - Path 2: **Try/Catch** -> hand the event document, unchanged, to `SG_Disruption_Correlate` (Process Call in option A / HTTP Client in option B, README section 6), with `DPP_CID` / header `X-Correlation-ID`. On success **Record** `DPP_IDEM`; on failure Notify `status=failed cid=..` and route to the dead-letter path (U-11; until then Throw Exception).
   Do Path 2 asynchronously relative to Path 1 so Django's 5 s read timeout is not consumed by N-08 (Branch runs paths in order in a normal process, so for true async set the listener operation to **Asynchronous** / or put Path 2 in a separate process started with Process Call "Wait for process to complete" OFF **(verify** the exact option name)).
5. Correlation, logging, idempotency: same rules as README sections 4-5. Log line `SG_Disruption_Direct cid=.. tenant=.. event=.. status=..`.

## Differences to the normal path
| | Normal (Event Streams) | Fallback |
|---|---|---|
| Transport | topic `supplier.disruption`, durable subscription | direct call |
| If the runtime is off during the event | message waits in the topic | Django marks `PUBLISH_FAILED`, re-sends on the next post of the same event |
| Replay | yes (retention) | no |
| Second subscriber | possible | not without changing this process |
| Components | `SG_Publish_Event` + `SG_Subscribe_Disruption` + `SG_ES_Events` | this one process, no Event Streams connection |

Switching back needs no Django change; only which process is deployed behind `/ws/simple/createDisruptionEvent`. Do not deploy both on the same path.
