# N-10 Workflow design: RiskCase state machine + decision API

Status: implemented on `nikhil/N-10-state-machine--impl` (sonnet). Q1-Q3 are decided (section 6); the table below is the
final 16-edge version (T16 added). `reopen_for_analysis` became `reanalyze_case`.
Sources: `docs/CONTRACTS.md` s1, s2, s4, s7, s10 (v0.3/v0.4), `docs/BRD_HLD_LLD.md` 16.4, 16.6 to 16.8,
`backend/apps/core/models/cases.py` (status guard), `apps/accounts/permissions.py` (U-04),
`apps/core/tenancy.py`, `apps/audit/service.py` (U-05), `apps/core/exceptions.py`.

**In data-pipeline terms:** a RiskCase is a job with a status column. `transition()` is the single
"job runner" that may move that column: it locks the row, checks the move against a whitelist of
edges, writes the new status and an audit row in one database transaction, or changes nothing.
The decision API is the human approval gate in the pipeline; only an APPROVE releases the
ERP-write step, and only after the approval is committed.

---

## 0. Files the implementation creates

| File | Content |
|---|---|
| `apps/workflow/states.py` | `S = RiskCase.Status` alias, `Trigger` enum, `Edge` dataclass, `TRANSITIONS` table (s1), `is_allowed(frm, to)` |
| `apps/workflow/exceptions.py` | `WorkflowError` hierarchy (s2.4) |
| `apps/workflow/services.py` | `transition()` (s2), `actionable_recommendations()`, `record_decision()` (s3), `start_execution()`, `dispatch_execution()`, `retry_execution()`, `record_execution_result()`, `close_case()`, `reanalyze_case()` (s4) |
| `apps/workflow/hooks.py` | `ExecutionRequest`, `ExecutionTrigger` protocol, `AnalysisRequest`, `AnalysisTrigger` protocol, stub implementations, `get_execution_trigger()`, `get_analysis_trigger()` (s4.4) |
| `apps/workflow/serializers.py` | `DecisionRequestSerializer`, `DecisionResponseSerializer` |
| `apps/workflow/views.py` | `RecommendationDecisionView` |
| `apps/workflow/urls.py` | `recommendations/<str:pk>/decision` |
| `backend/tests/test_workflow_*.py` | s5 |

Cross-stream one-liners the implementation also needs (tell Uday in the commit / hand-off; they are
additive and in his files):
1. `apps/accounts/permissions.py`: `"recommendations.decision"` -> `routed=True`; add the route to
   `ROUTES` / `EXPECTED_ROUTED` in `tests/test_role_matrix.py` (U-04 hand-off says "ask Uday").
2. `config/urls.py`: include `apps.workflow.urls` under `/api/v1/`.
3. `apps/core/exceptions.py`: when `exc` has a dict attribute `details`, use it as
   `error.details` (today non-validation errors always get `{}`). 3 lines; without it the
   `reason` codes of s3.5 are lost. Fallback if Uday says no: put the reason code in
   `message` as a `REASON: text` prefix.
4. `config/settings.py`: `WORKFLOW_EXECUTION_TRIGGER` and `WORKFLOW_ANALYSIS_TRIGGER` dotted paths
   (defaults to the stubs in `apps/workflow/hooks.py`).

---

## 1. Transition table

Every (from, to) pair listed here is allowed. **Every pair not listed is forbidden**
(`InvalidTransition`, 409). A move to the current state is the idempotent no-op of s2.3, not an
edge. 10 states -> 90 ordered pairs of distinct states: 16 allowed, 74 forbidden (T16 added by Q3).

Triggers: **SYS-IMPACT** (N-04 service, SYSTEM), **SYS-AGENT** (N-07 analysis persistence, SYSTEM),
**USER-DECISION** (decision API, a PROCUREMENT_MANAGER user), **SYS-EXEC** (execution start /
result, SYSTEM), **USER-RETRY** (U-11 retry endpoint, PROCUREMENT_MANAGER), **USER-CASE**
(close: PROCUREMENT_MANAGER or ADMIN; re-analyse a REJECTED case: PROCUREMENT_MANAGER only; Q2).

"Round" below means `case.analysis_round`; "actionable" means a Recommendation of the case with
`round == case.analysis_round`, `unsupported = False` and `status = PENDING`
(`actionable_recommendations(case)` returns that queryset; it is the only definition used).

| # | from -> to | trigger / allowed actor | preconditions (checked inside the lock) | side effects (same transaction) |
|---|---|---|---|---|
| T1 | RECEIVED -> ANALYZING | SYS-IMPACT / SYSTEM | none (the case exists with its impact; N-04 calls it right after creating the case) | status; audit |
| T2 | ANALYZING -> RECOMMENDATION_READY | SYS-AGENT / SYSTEM | an `AgentRun(agent=RECOVERY_PLANNER, round=round, valid=True)` exists for the case | status; audit (`data.actionable_count`) |
| T3 | RECOMMENDATION_READY -> PENDING_APPROVAL | SYS-AGENT / SYSTEM | `actionable_recommendations(case).count() >= 1` (**never PENDING_APPROVAL with zero options**) | status; audit |
| T4 | RECOMMENDATION_READY -> CLOSED | SYS-AGENT / SYSTEM | `actionable_recommendations(case).count() == 0` (TC-05: no option, or all `unsupported`) | status; audit (`data.no_viable_option_reason` copied from the case) |
| T5 | PENDING_APPROVAL -> APPROVED | USER-DECISION / PROCUREMENT_MANAGER | ctx `approval`: saved, `decision == APPROVE`, `approval.risk_case_id == case.pk`, `approval.round == round`, its recommendation not `unsupported` | status; audit (`approval_id`, `recommendation_id`) |
| T6 | PENDING_APPROVAL -> REJECTED | USER-DECISION / PROCUREMENT_MANAGER | ctx `approval` with `decision == REJECT`, same case and round; and `actionable_recommendations(case).count() == 0` (the rejected one was the last actionable option, see s3.4) | status; audit |
| T7 | PENDING_APPROVAL -> ANALYZING | USER-DECISION / PROCUREMENT_MANAGER | ctx `approval` with `decision == REQUEST_CHANGES`, same case and round | status; **`analysis_round += 1`**; audit (`from_round`, `to_round`, `comments`); `on_commit`: `AnalysisTrigger.request_analysis(...)` |
| T8 | REJECTED -> ANALYZING | USER-CASE / PROCUREMENT_MANAGER | none | status; **`analysis_round += 1`**; audit; `on_commit`: `AnalysisTrigger.request_analysis(...)` |
| T9 | REJECTED -> CLOSED | USER-CASE / PROCUREMENT_MANAGER or ADMIN (Q2) | `reason` non-blank | status; audit |
| T10 | APPROVED -> EXECUTING | SYS-EXEC / SYSTEM | ctx `execution`: `status == PENDING`, `execution.approval.decision == APPROVE`, `execution.approval.risk_case_id == case.pk`, `execution.approval.round == round` | status; audit (`execution_id`) |
| T11 | EXECUTING -> RECOVERED | SYS-EXEC / SYSTEM | ctx `execution` of this case with `status == SUCCEEDED` (the result was verified by re-reading the PO, TC-07) | status; **`recovered_at = now()`**; audit |
| T12 | EXECUTING -> EXECUTION_FAILED | SYS-EXEC / SYSTEM | ctx `execution` of this case with `status == FAILED` and `error_class` set | status; audit (`error_class`, `retryable`) |
| T13 | EXECUTION_FAILED -> EXECUTING | USER-RETRY / PROCUREMENT_MANAGER | ctx `execution` of this case with `execution.retryable` True (FAILED, TRANSIENT/DOWNSTREAM, `retry_count < EXECUTION_MAX_RETRIES`), evaluated **before** `retry_count` is incremented | status; audit (`retry_count` after increment is written by `retry_execution`) |
| T14 | EXECUTION_FAILED -> CLOSED | USER-CASE / PROCUREMENT_MANAGER or ADMIN (Q2) | `reason` non-blank | status; audit |
| T15 | RECOVERED -> CLOSED | USER-CASE / PROCUREMENT_MANAGER or ADMIN | none | status; audit |
| T16 | ANALYZING -> CLOSED | USER-CASE / PROCUREMENT_MANAGER or ADMIN (Q3) | `reason` non-blank | status; audit |

Notes:
- "Every APPROVED case has exactly one APPROVE approval in the current round" holds by T5 plus the
  partial unique constraint on Approval; "no ERP write without a recorded approval" holds because
  the only way into EXECUTING is T10 / T13, both of which require an `Execution` whose approval is
  an APPROVE of the current round (and `Execution.save()` refuses any other approval).
- Only edges into ANALYZING from PENDING_APPROVAL or REJECTED bump the round. T1 does not
  (round 1 is the first analysis).
- `analysis`, `no_viable_option_reason` and old Recommendations are not cleared on a new round.
  Recommendations are kept per round (history); N-07 overwrites `case.analysis` when the next round
  is validated. The UI filters recommendations by `round == analysis_round`.
- CLOSED is terminal. RECOVERED is only left by T15.
- ANALYZING exits are T2 (agents succeed) and T16 (a manual close, Q3). An invalid agent output leaves
  the case in ANALYZING with an `AgentRun(valid=False)`; Boomi / N-09 calls the agent again.

### 1.1 Table encoding (`states.py`)

```python
class Trigger(models.TextChoices):  # plain Enum is fine too
    SYS_IMPACT = "SYS_IMPACT"; SYS_AGENT = "SYS_AGENT"; USER_DECISION = "USER_DECISION"
    SYS_EXEC = "SYS_EXEC"; USER_RETRY = "USER_RETRY"; USER_CASE = "USER_CASE"

@dataclass(frozen=True)
class Edge:
    frm: str
    to: str
    trigger: Trigger
    actor: str                          # "SYSTEM" or a user role, e.g. "PROCUREMENT_MANAGER"
    precondition: Callable[[RiskCase, dict, str], str | None]   # (case, ctx, reason) -> error text or None
    bumps_round: bool = False
    sets_recovered_at: bool = False
    requests_analysis: bool = False     # on_commit AnalysisTrigger

TRANSITIONS: dict[tuple[str, str], Edge] = {(e.frm, e.to): e for e in [...16 edges...]}
```

The table is data; `transition()` has no per-state `if` chains. The test suite imports
`TRANSITIONS` only to assert it has exactly the 16 pairs of s1 (written out again by hand in the
test, so a change to one side fails).

---

## 2. `transition(case, to_state, *, actor, reason, correlation_id=None, **ctx)`

```python
def transition(case: RiskCase, to_state: str, *, actor, reason: str,
               correlation_id=None, **ctx) -> TransitionResult
```
- `case`: a `RiskCase` instance (only `pk` and `tenant_id` are trusted; everything else is
  re-read under the lock).
- `actor`: `User` (a saved user), `SystemPrincipal` (an API-key caller: `request.user`), or the
  string `"SYSTEM"`. Any other value -> `TypeError`.
- `reason`: short human text, stored in the audit row (`data.reason`), max 500 chars.
- `correlation_id`: explicit UUID; else `case.correlation_id` (see step 9).
- `ctx`: keyword objects the edge's precondition needs: `approval=`, `execution=`,
  `recommendation=`, `comments=`. Unknown keys are allowed and ignored by preconditions; model
  instances among them are written to the audit row as ids only (`approval_id`, ...).

### 2.1 Algorithm

```
1  if to_state not in RiskCase.Status.values: raise ValueError
2  actor_kind, actor_role, actor_tenant_id = _actor_info(actor)
       # User -> ("USER", user.role, user.tenant_id); SystemPrincipal or "SYSTEM" -> ("SYSTEM", "SYSTEM", principal.tenant_id or None)
3  with transaction.atomic():
4      try:
           locked = RiskCase.objects.select_for_update().get(pk=case.pk, tenant_id=case.tenant_id)
       except RiskCase.DoesNotExist: raise CaseNotFound
5      if actor_tenant_id is not None and actor_tenant_id != locked.tenant_id: raise CaseNotFound
           # same answer as "missing": no cross-tenant leak
6      frm = locked.status
       if frm == to_state:                                   # idempotent repeat (s2.3)
           _refresh_caller(case, locked)
           return TransitionResult(locked, frm, to_state, changed=False, audit_event=None)
7      edge = TRANSITIONS.get((frm, to_state))
       if edge is None: raise InvalidTransition(frm, to_state)
       if edge.actor == "SYSTEM" and actor_kind != "SYSTEM": raise TransitionNotPermitted
       if edge.actor != "SYSTEM" and (actor_kind != "USER" or actor_role != edge.actor): raise TransitionNotPermitted
       err = edge.precondition(locked, ctx, reason)
       if err: raise PreconditionFailed(frm, to_state, err)
8      fields = ["status", "updated_at"]
       with status_change_allowed():
           locked.status = to_state
           if edge.bumps_round:     locked.analysis_round += 1; fields.append("analysis_round")
           if edge.sets_recovered_at: locked.recovered_at = timezone.now(); fields.append("recovered_at")
           locked.save(update_fields=fields)
9      event = record(actor, "risk_case.status_changed", locked,
                      correlation_id=correlation_id or locked.correlation_id,
                      data={"from": frm, "to": to_state, "reason": reason,
                            "trigger": edge.trigger, "analysis_round": locked.analysis_round,
                            **_ctx_ids(ctx),                       # approval_id, execution_id, recommendation_id, comments
                            **({"request_correlation_id": rid} if (rid := get_correlation_id()) and rid != str(cid) else {})})
       # `record()` never gets tenant=; it derives it from the case and checks the actor's.
10     if edge.requests_analysis:
           transaction.on_commit(partial(_request_analysis, locked.pk, locked.tenant_id,
                                         locked.analysis_round, reason, ctx.get("comments", ""), cid))
11 _refresh_caller(case, locked)      # caller's instance: status, analysis_round, recovered_at, updated_at
12 return TransitionResult(locked, frm, to_state, changed=True, audit_event=event)
```

- `select_for_update` is a no-op on SQLite; the concurrency tests in s5.5 run on Postgres only.
- `transition()` may be called inside an outer `atomic()` (N-07, the decision API, execution
  result). Then step 3 is a savepoint and steps 10/`on_commit` fire when the **outermost**
  transaction commits. A raised `WorkflowError` rolls back the savepoint; callers that let it
  propagate roll back their own writes too, which is the intent.
- Audit and status are in one transaction: if `record()` raises (e.g. tenant mismatch), the
  status change is rolled back. No status change without its audit row.
- `_refresh_caller(case, locked)`: if `case is not locked`, call
  `case.refresh_from_db(fields=["status", "analysis_round", "recovered_at", "updated_at"])`.
  Reason: a caller still holding a stale instance and calling `case.save()` without
  `update_fields` would otherwise silently write the old status back (the guard compares with the
  loaded value, which is also old). Rule for every caller: **save RiskCase only with
  `update_fields`, never including `status`.**
- No asyncio tasks inside `status_change_allowed()` (the model docstring explains why).

### 2.2 Return value

```python
@dataclass(frozen=True)
class TransitionResult:
    case: RiskCase           # the locked, updated instance
    from_state: str
    to_state: str
    changed: bool            # False only for the idempotent repeat
    audit_event: AuditEvent | None
```

### 2.3 Idempotency rule

`transition(case, X)` when the locked case is already in `X` returns `changed=False`, writes **no**
audit row, checks **no** precondition, schedules **no** `on_commit` hook and does not touch
`analysis_round` / `recovered_at`. This makes Boomi callback retries (same execution result twice)
harmless. It does not weaken the decision API: decisions are refused earlier by the recommendation
and case-state checks of s3.3, so a second APPROVE never reaches `transition()` as a no-op that
could create a second execution. Any other repeat that must be refused (e.g. a second retry) is
refused by its own service function, not by `transition()`.

### 2.4 Exceptions (`apps/workflow/exceptions.py`)

All subclass `WorkflowError(rest_framework.exceptions.APIException)`, so a view can let them
propagate and the contract handler renders the s1 error body. Each carries `details = {"reason":
<CODE>, "from": ..., "to": ...}` (needs cross-stream change 3).

| Exception | HTTP / code | `details.reason` | When |
|---|---|---|---|
| `CaseNotFound` | 404 `NOT_FOUND` | `NOT_FOUND` | case missing, or in another tenant than the actor |
| `InvalidTransition` | 409 `CONFLICT` | `INVALID_TRANSITION` | (from, to) not in the table |
| `PreconditionFailed` | 422 `BUSINESS_RULE` | `PRECONDITION_FAILED` (+ `message` = the edge's text) | edge exists, precondition false |
| `TransitionNotPermitted` | 403 `FORBIDDEN` | `ACTOR_NOT_PERMITTED` | wrong actor kind / role for the edge (defence in depth: the view's permission check normally stops it earlier) |
| `DecisionConflict` | 409 `CONFLICT` | see s3.5 | decision API state checks |
| `DecisionRejected` | 422 `BUSINESS_RULE` | see s3.5 | decision API business rules |

`ValueError` / `TypeError` (bad `to_state`, bad actor) are programming errors and stay 500s.

---

## 3. `POST /api/v1/recommendations/{id}/decision`

### 3.1 Request

```json
{ "decision": "APPROVE" | "REJECT" | "REQUEST_CHANGES", "comments": "text" }
```
- `decision`: required, one of the three.
- `comments`: string, max 2000 chars, trimmed. **Required (non-blank) for REJECT and
  REQUEST_CHANGES** (it is the feedback the re-analysis and the audit need, TC-06); optional for
  APPROVE (default `""`). R-05 / R-06 must show the field as required for those two.
- Any other key (including `tenant_id`, `decided_by`, `round`) is ignored. The actor is
  `request.user`; the tenant is `request.tenant`.

### 3.2 View

`RecommendationDecisionView(TenantScopedMixin, GenericAPIView)`, `queryset =
Recommendation.objects.all()`, `lookup_field = "pk"`, `permission_classes = [RolePermission]`,
`permission_key = "recommendations.decision"` (ROLE_MATRIX: POST -> PROCUREMENT_MANAGER only;
ADMIN, OPS_MANAGER, VIEWER and SYSTEM are refused with 403). drf-spectacular annotations with the
request and response serializers.

Order of checks (each later check is reached only if the earlier ones pass):
1. Authentication -> 401 `UNAUTHENTICATED`.
2. `RolePermission` -> 403 `FORBIDDEN` (also inactive tenant). This runs **before** the lookup, so
   a non-PM never learns whether an id exists.
3. Body validation -> 400 `VALIDATION_ERROR`.
4. `get_object()` (tenant-scoped) -> 404 `NOT_FOUND` for a missing id **and** for another tenant's
   recommendation (identical body; the mixin audits `security.cross_tenant_denied`, TC-10).
5. `record_decision(rec, decision, comments, user=request.user)` (s3.3), whose exceptions map to
   409 / 422.

### 3.3 `record_decision(recommendation, decision, comments, *, user) -> DecisionResult`

In `services.py`, so Flow-side or admin tooling can reuse it. One `transaction.atomic()`.
**Lock order is always RiskCase first, then Recommendation** (same order everywhere, so two
decisions on the same case cannot deadlock).

```
with transaction.atomic():
  1  case = RiskCase.objects.select_for_update().get(pk=rec.risk_case_id, tenant_id=user.tenant_id)
  2  rec  = Recommendation.objects.select_for_update().get(pk=rec.pk, tenant_id=user.tenant_id)
  3  checks, in this order:
       case.status != PENDING_APPROVAL         -> DecisionConflict("CASE_NOT_PENDING_APPROVAL", current=case.status)
       rec.round   != case.analysis_round      -> DecisionConflict("STALE_ROUND", round=rec.round, current_round=...)
       rec.unsupported                         -> DecisionRejected("UNSUPPORTED_OPTION", reasons=rec.unsupported_reasons)
       rec.status  != PENDING                  -> DecisionConflict("ALREADY_DECIDED", status=rec.status)
  4  approval = Approval.objects.create(risk_case=case, recommendation=rec, round=rec.round,
                                         decided_by=user, decision=decision, comments=comments)
       # IntegrityError on uniq_approval_one_approve_per_round -> DecisionConflict("ALREADY_APPROVED")
       #   (backstop only: on Postgres the row lock in step 1 already serialises the requests)
  5  rec.status = {APPROVE: APPROVED, REJECT: REJECTED, REQUEST_CHANGES: CHANGES_REQUESTED}[decision]
     rec.save(update_fields=["status"])
  6  record(user, "recommendation.decided", rec, correlation_id=case.correlation_id,
            data={"decision", "comments", "approval_id", "risk_case_id", "round", "rank", "option_type"})
  7  branch (s3.4)
return DecisionResult(approval, rec, case, execution or None)
```

The `IntegrityError` in step 4 must be caught around a nested `atomic()` (savepoint) so the outer
transaction stays usable for raising the clean error.

### 3.4 Per decision

| decision | Recommendation.status | case transition | other effects |
|---|---|---|---|
| APPROVE | APPROVED | T5 PENDING_APPROVAL -> APPROVED (ctx `approval`, `recommendation`) | `Execution.objects.create(approval=approval, status=PENDING, target_system="mock-erp", request_payload=rec.proposed_action)` in the same transaction; then `transaction.on_commit(partial(start_execution, execution.pk, case.tenant_id, case.correlation_id))`. Other PENDING options of the round are left PENDING (not decidable any more, because the case left PENDING_APPROVAL). |
| REJECT | REJECTED | if `actionable_recommendations(case)` is now empty: T6 -> REJECTED; otherwise **no case transition** (the PM can still approve or reject another option of the round) | no execution, no `on_commit` of any kind (TC-06) |
| REQUEST_CHANGES | CHANGES_REQUESTED | T7 -> ANALYZING, round + 1 (ctx `approval`, `comments`) | `on_commit` AnalysisTrigger (scheduled by `transition()`); no execution (TC-06) |

**TC-06 rule:** `start_execution` / `ExecutionTrigger.trigger` are reachable from the decision path
only in the APPROVE branch and only through `transaction.on_commit`. If anything after the
Approval insert raises, the transaction rolls back and the `on_commit` callback is discarded, so a
rolled-back approval never triggers execution.

**Double APPROVE in one round is impossible**, by three independent layers: (1) the case row lock
serialises decisions, and the second one sees `case.status == APPROVED` -> 409
`CASE_NOT_PENDING_APPROVAL`; (2) `rec.status != PENDING` -> 409 `ALREADY_DECIDED`; (3) the partial
unique constraint `uniq_approval_one_approve_per_round` -> 409 `ALREADY_APPROVED` (the layer that
still holds on SQLite). Plus `Execution.approval` is one-to-one.

### 3.5 Responses

**201 Created** (an Approval was created):
```json
{
  "approval_id": "APR-000012",
  "recommendation_id": "REC-000101",
  "decision": "APPROVE",
  "recommendation_status": "APPROVED",
  "risk_case_id": "RC-000045",
  "case_status": "APPROVED",
  "analysis_round": 1,
  "execution_id": "EXE-000007",
  "decided_by": "USR-3",
  "decided_at": "2026-12-10T09:30:00Z",
  "correlation_id": "6f1c..."
}
```
`execution_id` is null for REJECT / REQUEST_CHANGES. `case_status` is the status after the decision
(`APPROVED`, `REJECTED`, `PENDING_APPROVAL` for a non-final reject, or `ANALYZING`).
`analysis_round` is the case's round after the decision (+1 after REQUEST_CHANGES).
`correlation_id` is the case's. The response header `X-Correlation-ID` stays the request's (U-05
middleware).

**Errors** (s1 body `{"error": {code, message, details, correlation_id}}`):

| HTTP | code | `details.reason` | when |
|---|---|---|---|
| 400 | VALIDATION_ERROR | (field errors) | bad / missing `decision`; blank `comments` on REJECT or REQUEST_CHANGES; comments > 2000 |
| 401 | UNAUTHENTICATED | - | no / invalid token |
| 403 | FORBIDDEN | - | role is not PROCUREMENT_MANAGER (ADMIN, OPS_MANAGER, VIEWER, SYSTEM key), or inactive tenant |
| 404 | NOT_FOUND | - | no such recommendation in the caller's tenant (incl. another tenant's) |
| 409 | CONFLICT | `CASE_NOT_PENDING_APPROVAL` | case not in PENDING_APPROVAL (`details.current_status`) |
| 409 | CONFLICT | `STALE_ROUND` | recommendation from an older round |
| 409 | CONFLICT | `ALREADY_DECIDED` | recommendation status is not PENDING |
| 409 | CONFLICT | `ALREADY_APPROVED` | unique-constraint backstop |
| 422 | BUSINESS_RULE | `UNSUPPORTED_OPTION` | recommendation flagged `unsupported` by N-07 (`details.unsupported_reasons`) |

Flow (R-06) retrying a decision after a timeout gets 409 `ALREADY_DECIDED` /
`CASE_NOT_PENDING_APPROVAL`; it should read the case and treat that as "already recorded".

---

## 4. Where the trigger points live

### 4.1 Impact -> case (N-04 service + U-06 wrapper)
`apps.intelligence.services.create_risk_case(...)` only creates the case (at RECEIVED, the model
default) with its impact and AffectedPO rows; it does **not** call `transition()`.
The RECEIVED -> ANALYZING move (T1) happens in U-06's wrapper
`apps.integrations.risk_cases.create_risk_case_from_snapshot` (via `move_to_analyzing`), right
after `create_risk_case` returns, as
`transition(case, ANALYZING, actor=<SystemPrincipal of the API key>, reason="impact computed")`;
the wrapper also heals an existing case still in RECEIVED. Boomi (N-08) then invokes the agents.

### 4.2 N-07 analysis persistence (`POST /internal/risk-cases/{id}/analysis`, SYSTEM)
In one `atomic()`:
1. Lock the case (`select_for_update`, tenant = `request.tenant`). If `status != ANALYZING` ->
   409 `CONFLICT` (late or duplicate agent output) and nothing is written, not even an AgentRun
   (Boomi logs the 409).
2. Write one `AgentRun(round=case.analysis_round, raw, valid, validation_errors)` per agent output.
3. If the recovery plan is invalid (schema / plan-wide fact error): stop, no transition, return
   N-07's error response. Case stays ANALYZING (Q3).
4. Else write the Recommendation rows (`round = case.analysis_round`, `unsupported`,
   `unsupported_reasons`), `case.analysis`, `case.no_viable_option_reason` with
   `case.save(update_fields=[..., "updated_at"])` (never `status`).
5. `transition(case, RECOMMENDATION_READY, actor=request.user, reason="analysis validated")` (T2).
6. If `actionable_recommendations(case).exists()`:
   `transition(case, PENDING_APPROVAL, actor=request.user, reason="options ready for approval")` (T3),
   else `transition(case, CLOSED, actor=request.user, reason="no viable option: " + reason)` (T4).

**As built (N-07 persist, AC-07):** the case lock is held for the whole call, but the work runs as an AgentRun insert followed by a SAVEPOINT for steps 4-6. If the savepoint fails (data error, a refused transition), only it is rolled back; the AgentRun commits (raw output is always kept) and the caller gets a 422 `AGENT_OUTPUT_NOT_APPLIED` (or the transition's own error). A duplicate or late call never reaches the insert: it sees the new status under the lock and gets 409 with nothing written. `round` is required in the request body; a mismatch is 409 `STALE_ROUND`. A repeated rank is a plan-level validation error (422).

T2 and T3/T4 are two transitions (two audit rows) in one transaction; the UI never sees
RECOMMENDATION_READY as a resting state in the normal path, but the audit trail shows it (AC-07).
If Flow (R-06) later needs to create the approval task between T2 and T3, T3 moves to Flow's
callback; nothing in `transition()` changes.

### 4.3 Execution (U-10 / U-11)

Execution row writes during the workflow happen in these N-10 service functions, which U-10 and
U-11's endpoints call. They own the Boomi / ERP side; N-10 owns the case state.

```python
def start_execution(execution_id: str, tenant_id: str, correlation_id) -> None
```
Runs from `on_commit` after an APPROVE. `atomic()`: lock case, then
`Execution.objects.select_for_update().get(pk=execution_id, tenant_id=tenant_id)`. If
`execution.status != PENDING` or `case.status != APPROVED` -> return (idempotent; logs at info).
Else `transition(case, EXECUTING, actor="SYSTEM", reason="execution started", execution=exe)`
(T10), `exe.status = RUNNING`, `exe.started_at = now()`, save with `update_fields`. After commit,
`dispatch_execution(execution_id, tenant_id, correlation_id)`.

```python
def dispatch_execution(execution_id, tenant_id, correlation_id) -> None
```
Builds `ExecutionRequest` (s4.4) and calls `get_execution_trigger().trigger(req)` **outside** any
transaction. If `trigger()` raises: `record_execution_result(exe, outcome="FAILED",
error_class="DOWNSTREAM", error=<sanitised class name + short message>, actor="SYSTEM")`, log, do
not re-raise (the decision request already returned 201).

```python
def retry_execution(execution, *, actor: User, correlation_id=None) -> TransitionResult
```
For U-11's `POST /executions/{id}/retry` (PROCUREMENT_MANAGER). `atomic()`: lock case, lock
execution; `transition(case, EXECUTING, actor=actor, reason="retry requested", execution=exe)`
(T13: checks `exe.retryable` before the increment), then `exe.retry_count += 1`,
`exe.status = RUNNING`, `exe.error_class = exe.error = None`, `exe.started_at = now()`,
`exe.finished_at = None`; save; `on_commit(dispatch_execution)`. **Same Execution id = same ERP
`Idempotency-Key`**, so a retry can never duplicate the PO update (TC-08). Not retryable ->
`PreconditionFailed` (422).

```python
def record_execution_result(execution, *, outcome: Literal["SUCCEEDED", "FAILED"],
                            error_class=None, error=None, response_status=None,
                            response_payload=None, actor, correlation_id=None) -> TransitionResult
```
For U-10's `POST /internal/executions/{id}/result` (SYSTEM). `outcome="SUCCEEDED"` means **the
ERP write was verified by re-reading the PO** (TC-07); an unverified success is reported as
`FAILED` with `error_class="BUSINESS"`. `atomic()`: lock case, lock execution.
- Idempotent repeat: `exe.status == outcome` already -> return the no-op result (no audit row).
- `exe.status != RUNNING` -> `DecisionConflict("EXECUTION_NOT_RUNNING")` (409).
- Write `status`, `finished_at`, `response_status`, `response_payload` (already sanitised by the
  caller), `error_class`, `error`; then `transition(case, RECOVERED | EXECUTION_FAILED,
  actor=actor, reason=..., execution=exe)` (T11 / T12; T11 sets `recovered_at`).
- Applying the verified PO change to Django's PO read model stays U-10's decision (CONTRACTS s10).

### 4.4 Hook interfaces (`apps/workflow/hooks.py`)

```python
@dataclass(frozen=True)
class ExecutionRequest:
    execution_id: str        # also the ERP Idempotency-Key
    tenant_id: str
    risk_case_id: str
    approval_id: str
    recommendation_id: str
    option_type: str
    proposed_action: dict    # Recommendation.proposed_action == Execution.request_payload
    correlation_id: str      # the case's; Boomi must pass it through (s1)
    attempt: int             # execution.retry_count + 1

class ExecutionTrigger(Protocol):
    def trigger(self, request: ExecutionRequest) -> None:
        """Hand the approved action to Boomi (SG_Execute_Approved_Action). Must return quickly;
        the result comes back later via POST /internal/executions/{id}/result.
        Raise on dispatch failure (connection error, non-2xx)."""

@dataclass(frozen=True)
class AnalysisRequest:
    tenant_id: str
    risk_case_id: str
    round: int
    reason: str
    feedback: str            # the REQUEST_CHANGES / re-analyse comments, for the agents
    correlation_id: str

class AnalysisTrigger(Protocol):
    def request_analysis(self, request: AnalysisRequest) -> None: ...
```
- `get_execution_trigger()` / `get_analysis_trigger()`: `import_string(settings.WORKFLOW_..._TRIGGER)()`,
  not cached (tests override the setting).
- Stubs (defaults until U-10 / N-09 exist): `StubExecutionTrigger` and `StubAnalysisTrigger` log
  at INFO and write an audit row `execution.dispatch_stubbed` / `analysis.request_stubbed` with the
  request ids. The stub execution trigger does **not** fake a result; the case stays EXECUTING
  until a result is posted (the demo can post one by hand or U-10 plugs in).
- U-10 implements `ExecutionTrigger` in `apps.integrations` (BoomiClient POST to the execution
  process) and sets `WORKFLOW_EXECUTION_TRIGGER`. Boomi verifies the approval reference by calling
  back with `execution_id`; the Execution row exists and is linked to an APPROVE approval before
  Boomi is ever called.
- N-09 implements `AnalysisTrigger` (Boomi process call, or the offline Claude fallback).
- An exception in `request_analysis` is logged and audited (`analysis.request_failed`), not
  re-raised; the case stays ANALYZING and can be re-requested (Q3).

### 4.5 Case actions (T8, T9, T14, T15)

`close_case(case, *, actor, reason)` and `reanalyze_case(case, *, actor, reason, comments="")` are
thin wrappers over `transition()` (CLOSED / ANALYZING). `reanalyze_case` refuses any state but
REJECTED (409). No HTTP endpoint is defined in N-10 because CONTRACTS s4 has none; see Q2 for the
proposal.

### 4.6 Known gap: lost `on_commit`
If the process dies between the APPROVE commit and `start_execution`, the case stays APPROVED with
a PENDING Execution. A management command `workflow_resume_pending` (in this app) re-runs
`start_execution` for every `Execution(status=PENDING)` whose case is APPROVED and older than 60 s.
Small; build it with the rest.

---

## 5. Test plan

Pytest, Django test DB, factories from `tests/factories.py`. Files `tests/test_workflow_transitions.py`,
`tests/test_workflow_decision_api.py`, `tests/test_workflow_execution.py`,
`tests/test_workflow_concurrency.py`. Execution / analysis triggers replaced by a recording fake via
`settings.WORKFLOW_EXECUTION_TRIGGER` (a module-level list of received requests, cleared per test).
`on_commit` callbacks run with `django_capture_on_commit_callbacks(execute=True)` (pytest-django)
or `TestCase.captureOnCommitCallbacks`.

### 5.1 Table-driven transitions (acceptance item 1)
- `ALLOWED` written out by hand in the test (16 pairs). Assert `set(TRANSITIONS) == ALLOWED`.
- **Every allowed pair**: parametrize over the 16; a fixture builds a case in `frm` with the ctx
  the precondition needs (approval / execution / agent run / recommendations; built with
  factories, status set via a test helper that uses `status_change_allowed()`), calls
  `transition()` with the edge's actor; assert status, `changed=True`, one new
  `AuditEvent(action="risk_case.status_changed")` with `data.from/to/reason`, correct
  tenant and `correlation_id == case.correlation_id`; T7/T8 bump the round by exactly 1; T11 sets
  `recovered_at`; others leave round / `recovered_at` untouched.
- **Every forbidden pair**: parametrize over all `(a, b)` with `a != b` not in `ALLOWED` (74);
  assert `InvalidTransition`, status unchanged in the DB, no audit row.
- **Every self pair** (10): `changed=False`, no audit row, no hook scheduled.
- **Preconditions**: each allowed edge with a precondition gets a failing variant
  (T2 without a valid planner run / with only an invalid one; T3 with zero actionable (all
  unsupported, or other round); T4 with one actionable; T5/T6/T7 with missing approval, wrong
  decision, other round, other case; T6 with an actionable option left; T10 with a non-PENDING
  execution or an approval of an older round; T11/T12 with the wrong execution status; T13 with
  retry_count at the max and with error_class VALIDATION; T9/T14 blank reason) -> `PreconditionFailed`,
  nothing written.
- **Actor rules**: SYSTEM on a user edge, a VIEWER / OPS_MANAGER / ADMIN user on a PM edge, a PM
  user on a SYSTEM edge -> `TransitionNotPermitted`.
- **Tenant**: actor of tenant B on a case of tenant A -> `CaseNotFound`, nothing written.
- **Guard**: after `transition()`, the caller's stale instance has the new status
  (`_refresh_caller`); `case.save()` on it afterwards does not revert the status.
- **Atomicity**: patch `record` to raise -> status unchanged in the DB.
- **Return value** fields.

### 5.2 Decision API: roles and tenant (acceptance item 3)
- PROCUREMENT_MANAGER -> 201 for each decision.
- OPS_MANAGER, VIEWER, ADMIN -> 403 `FORBIDDEN`; SYSTEM API key -> 403; anonymous -> 401. For each:
  no Approval row, recommendation and case unchanged.
- A PM of tenant B on tenant A's recommendation -> 404 with the same body as a random id; one
  `security.cross_tenant_denied` audit row in tenant B; nothing changed in tenant A.
- Inactive tenant -> 403.
- `tenant_id` / `decided_by` in the body are ignored.

### 5.3 Decision API: rules
- Case not PENDING_APPROVAL (one test per other state) -> 409 `CASE_NOT_PENDING_APPROVAL`.
- Recommendation of round 1 when the case is at round 2 -> 409 `STALE_ROUND`.
- `unsupported` recommendation, each decision -> 422 `UNSUPPORTED_OPTION`.
- Recommendation already APPROVED / REJECTED / CHANGES_REQUESTED -> 409 `ALREADY_DECIDED`.
- Blank comments on REJECT / REQUEST_CHANGES -> 400; missing decision / bad value -> 400.
- APPROVE: Approval row, rec APPROVED, case APPROVED, Execution PENDING linked to the approval with
  `request_payload == proposed_action`, response body fields; after commit callbacks: case
  EXECUTING, Execution RUNNING, the fake trigger received exactly one `ExecutionRequest` with the
  right ids and `correlation_id`.
- Second APPROVE on the same rec (sequential) -> 409, still exactly one Approval(APPROVE), one
  Execution, one trigger call. APPROVE on another rec of the same round after the first -> 409
  `CASE_NOT_PENDING_APPROVAL`.
- REJECT with two actionable options: first REJECT -> case stays PENDING_APPROVAL; second -> case
  REJECTED. An unsupported sibling does not count as actionable.
- REQUEST_CHANGES -> case ANALYZING, round + 1, fake analysis trigger called once with the comments;
  old-round recs now answer 409 `STALE_ROUND`.
- Error body shape matches s1 and carries `details.reason` (after cross-stream change 3).
- Audit: `recommendation.decided` with decision and comments, and `risk_case.status_changed`, both
  with the case's correlation id (AC-07, R-06 "decision visible in audit").

### 5.4 TC-06
- REJECT (final and non-final) and REQUEST_CHANGES with `execute=True` on-commit capture: the fake
  execution trigger list is empty, no Execution row exists, the audit row holds the comments.
- APPROVE whose transaction is rolled back after the Approval insert (patch `transition` to raise
  after the insert) -> no Execution, no trigger call, no Approval row.
- `start_execution` called by hand for an Execution whose approval is REJECT cannot exist
  (`Execution.save()` raises) - one assertion to keep the invariant visible.

### 5.5 Concurrency (Postgres only)
Marked `@pytest.mark.skipif(connection.vendor != "postgresql")`, `transaction=True` DB.
- Two threads APPROVE two different recs of the same case at the same time (barrier): exactly one
  201, the other 409; one Approval(APPROVE), one Execution, one trigger call.
- Two threads APPROVE the same rec: same result.
- APPROVE racing REQUEST_CHANGES on the same case: exactly one succeeds; final state is consistent
  (APPROVED + 1 execution, or ANALYZING + round 2 and no execution).
- Two concurrent `record_execution_result` calls with the same outcome: one transition, one audit
  row.
- Each thread closes its DB connection at the end.

### 5.6 Execution services
- `start_execution` twice -> one transition, one trigger call (idempotent).
- Trigger raises -> case EXECUTION_FAILED, Execution FAILED with `error_class=DOWNSTREAM`,
  `retryable` True.
- `retry_execution`: retry_count + 1, same execution id in the second `ExecutionRequest`
  (`attempt == 2`); not retryable (VALIDATION, or count at max) -> 422 and nothing changed.
- `record_execution_result` SUCCEEDED -> RECOVERED with `recovered_at`; FAILED -> EXECUTION_FAILED;
  same result twice -> no-op; result for a non-RUNNING execution -> 409.
- `workflow_resume_pending` picks up an APPROVED case with a PENDING execution.

Coverage target: 95% lines on `apps/workflow/`.

---

## 6. Decisions on the open questions (Q1-Q3, decided by Nikhil)

- **Q1 (decided: yes). Flow's caller identity (R-06).** The decision API requires a human
  PROCUREMENT_MANAGER JWT. A SYSTEM API key gets 403. Roop's Flow app (R-06) must call it with the
  approving user's token. No SYSTEM-on-behalf-of-user variant.
- **Q2 (decided). Case actions.** N-10 ships the service functions `close_case()` and
  `reanalyze_case()` only, no HTTP endpoints. PROCUREMENT_MANAGER and ADMIN may close; re-analysing
  a REJECTED case is PROCUREMENT_MANAGER only. **Proposal for the CONTRACTS freeze (S-01):**
  - `POST /risk-cases/{id}/close` body `{"reason": "text"}` -> 200 with the case summary;
    PROCUREMENT_MANAGER, ADMIN; calls `close_case()`; 409 `INVALID_TRANSITION` from a state with no
    CLOSED edge; 422 `PRECONDITION_FAILED` for a blank reason (T9, T14, T16).
  - `POST /risk-cases/{id}/reanalyze` body `{"comments": "text"}` -> 200; PROCUREMENT_MANAGER only;
    calls `reanalyze_case()`; 409 unless the case is REJECTED. Bumps `analysis_round`.
  - Each needs a ROLE_MATRIX row (`risk-cases.close`, `risk-cases.reanalyze`) and a role-matrix
    test row; Uday owns those files.
- **Q3 (decided: yes). Stuck in ANALYZING.** `ANALYZING -> CLOSED` is the 16th allowed transition
  (T16): manual, PROCUREMENT_MANAGER or ADMIN, `reason` required. It is in the table-driven test.

Decided in this spec without asking (change them here if you disagree): comments required on
REJECT / REQUEST_CHANGES; REJECT of a non-last option keeps the case in PENDING_APPROVAL; N-10
creates the Execution row in the decision transaction (U-10 receives it, never creates it);
RECOMMENDATION_READY -> CLOSED is automatic in N-07 when no option is actionable; decision returns
201; audit rows use the case's correlation id (request id kept in `data.request_correlation_id`).
