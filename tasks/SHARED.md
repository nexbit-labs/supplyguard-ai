# Tasks - Shared (whole team)

There is no team lead. Each shared task has one **driver** who is accountable for getting it done; everybody contributes.

> Same format as the personal task files - `scripts/progress.py` parses it.

## Handoff
- **Last updated:** 2026-11-19 (setup, tasks reassigned by role)
- **Current task:** S-01
- **Branch:** -
- **Where I left off:** Contracts v0.1 drafted in `docs/CONTRACTS.md`.
- **Next step:** 30-min call: walk through CONTRACTS, agree changes, Uday tags v1.
- **Blockers:** none

---

### S-01: Freeze contracts v1
- **Status:** blocked
- **Sprint:** S0 (19-20 Nov) | **Est:** 0.5d | **Depends:** -
- **Driver:** Uday
- **Branch:** `uday/S-01-contracts`
- **Model hint:** opus / high
- **Deliverables:** `docs/CONTRACTS.md` v1 agreed by all three; changelog entry.
- **Acceptance:**
  - [ ] Roop approved sections 4 (UI-facing endpoints) and 2 (roles shown in the UI)
  - [ ] Nikhil approved sections 3, 5, 6, 7
  - [ ] Uday approved sections 1, 2, 4, 8
- **Done in:** -
- **Notes:** needs uday: run the 30-min call with Roop + Nikhil using `docs/CONTRACTS_V1_AGENDA.md` (55 items, each with a recommended decision; on branch `uday/S-01-contracts`), record accept/reject per item, then tell Claude "S-01 decided" so it applies them to CONTRACTS.md v1. Decide on the call: PO `expected_date` (header, used by section 5 PATCH and LLD) vs line `due_date` (seed snapshot) - section 6 evidence example uses `expected_date`. Also ratify Nikhil's schema tightenings listed in `ai/README.md` 'Proposed for S-01'.

### S-02: Sprint-end integration demos
- **Status:** todo
- **Sprint:** every sprint (23 Nov, 30 Nov, 7 Dec, 14 Dec, 20 Dec, 26 Dec) | **Est:** 1h each | **Depends:** -
- **Driver:** rotating - Uday (m1, m4), Nikhil (m2, m5), Roop (m3, m6)
- **Branch:** -
- **Model hint:** haiku / low (`/status all` to prepare)
- **Deliverables:** that sprint's driver merges `collaboration` -> `main` via PR, tags `m1`..`m6`, runs a 15-min demo of the milestone in `docs/PROJECT_PLAN.md`, appends notes to `progress/SESSION_LOG.md`.
- **Acceptance:**
  - [ ] m1 (S0) [ ] m2 (S1) [ ] m3 (S2) [ ] m4 (S3) [ ] m5 (S4) [ ] m6 (S5)
- **Done in:** -
- **Notes:** -

### S-03: End-to-end demo dry run
- **Status:** todo
- **Sprint:** S5 (24 Dec) | **Est:** 0.5d | **Depends:** U-10, R-05, R-06
- **Driver:** Roop
- **Branch:** -
- **Model hint:** -
- **Deliverables:** full 7-scene run on the hosted environment, issues filed as tasks.
- **Acceptance:**
  - [ ] Completed without manual DB edits
- **Done in:** -
- **Notes:** -

### S-04: Security review
- **Status:** todo
- **Sprint:** S5 (22-23 Dec) | **Est:** 0.5d | **Depends:** U-04, U-11
- **Driver:** Uday
- **Branch:** `uday/S-04-security-review`
- **Model hint:** opus / high (run `/security-review` on the branch diff)
- **Deliverables:** checklist from BRD 15.5 verified; no secrets in repo/logs; findings fixed or filed.
- **Acceptance:**
  - [ ] TC-10 passes, secrets scan clean, every BRD 15.5 bullet has evidence
- **Done in:** -
- **Notes:** -

### S-05: Final submission package
- **Status:** todo
- **Sprint:** S6 (27-31 Dec) | **Est:** 1d | **Depends:** R-12, R-11, R-10
- **Driver:** Roop
- **Branch:** `roop/S-05-submission`
- **Model hint:** opus / high review, haiku / low checklist
- **Deliverables:** demo video, deck, Marketplace package, README polish, final `docs/TEST_REPORT.md` (Nikhil), submission form, tag `v1.0`.
- **Acceptance:**
  - [ ] Submitted before 31 Dec 2026
- **Done in:** -
- **Notes:** -
