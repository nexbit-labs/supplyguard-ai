# CLAUDE.md - SupplyGuard AI (Boomi World 2027 Hackathon)

You are working in a 3-person team repo. Read this whole file at the start of every session. It is short on purpose; details live in the linked docs.

## 1. What we are building
SupplyGuard AI: multi-tenant SaaS (React + Django REST + PostgreSQL) with a Boomi backend (Integration, Event Streams, Agentstudio, Flow, API Management, optional Data Hub) that detects supplier disruptions, finds affected purchase orders, explains risk with evidence, recommends recovery options, gets human approval and executes the approved change in a (mock) ERP - with a full audit trail.
Hard deadline: **31 Dec 2026**. Plan + sprints: `docs/PROJECT_PLAN.md`. Requirements: `docs/BRD_HLD_LLD.md`. Interfaces: `docs/CONTRACTS.md`.

## 2. Who owns what
There is no team lead; shared tasks have a named driver (`tasks/SHARED.md`).

| Member | Background | Task file | Branch prefix | Owns |
|---|---|---|---|---|
| Roop | Frontend (React) | `tasks/ROOP.md` | `roop/` | `frontend/`, `backend/apps/portal/` (UI read APIs), `boomi/flow/`, `boomi/marketplace/`, `.github/workflows/ci.yml`, `docs/DEMO_SCRIPT.md` |
| Nikhil | Data engineer (Python, AI/ML) | `tasks/NIKHIL.md` | `nikhil/` | `ai/`, `data/`, `mock-erp/`, `backend/apps/intelligence/`, `backend/apps/workflow/`, `boomi/agents/`, `boomi/processes/correlation/`, `tests/e2e/` |
| Uday | Data engineer (Python, AI/ML) | `tasks/UDAY.md` | `uday/` | `backend/` project + `apps/{tenants,accounts,core,audit,integrations}`, `boomi/processes/` (except `correlation/`), `boomi/api/`, `deploy/`, `docker-compose.yml`, `docs/BOOMI_ENV.md`, `docs/INSTALL.md` |
| Everyone | - | `tasks/SHARED.md` | driver's prefix | `docs/CONTRACTS.md` (changes need all 3) |

**Identify the member** from the command argument (`/resume nikhil`), else from `git config user.name`, else ask. Work only on that member's tasks. Do not edit another stream's folders - if you need a change there, write it in your task's Notes and in the Handoff "Blockers" line, and tell the user.

**Explain in the member's language.** Claude writes most of the code, so the person must still understand and verify it. At the start of each task give a short "what this is and why" (max 6 lines) in that member's terms, and before asking them to check something, say exactly what to look at.
- Roop (React): explain backend, Boomi and data ideas through frontend analogies - components, props/state, API calls, forms, routing.
- Nikhil and Uday (Python data engineers): explain Django, Boomi and React ideas through data-pipeline analogies - ETL steps, schemas, jobs, connectors, DataFrames.

## 3. Session protocol (mandatory)
**Start:** the SessionStart hook already ran `python scripts/gitflow.py local-sync` (auto-pull: updates `collaboration`, pulls your task branch, merges the latest `collaboration` into it; skipped if you have uncommitted changes). Then `python scripts/progress.py <member>` -> read that member's task file (Handoff block first) -> pick the `in-progress` task, else the first `todo` task whose Depends are all `done` -> create/checkout its branch from `collaboration`.
**During:** follow section 4 (advisor + subagents). Commit small, often, with the trailers in section 5.
**Keep going:** when a task is done, `/done` merges it into `collaboration` and Claude immediately continues with the member's next ready task in the same session (tell the user "Next: <ID> <title>" in one line, no need to ask). Repeat until nothing is ready, the user says stop, or the context is getting long.
**End (never skip, even if the work is unfinished):** run `/handoff` - it updates the task file Handoff block + Status + checkboxes, appends to `progress/SESSION_LOG.md`, regenerates `PROGRESS.md`, commits and pushes. Unpushed work does not exist for the rest of the team.

Slash commands in `.claude/commands/`: `/resume <member>`, `/status [member|all]`, `/handoff`, `/done <task-id>`, `/sync`, `/merge-prs`.

## 4. Advisor + nested sessions (how every task is done) - ENFORCED
The session the member opens is the **advisor**: **Opus 5.5, medium effort** (high only for design/security decisions). It is the most expensive model, so it only thinks: explains the task, plans, starts nested sessions, merges their branches, decides. **It does not write code itself** (a hook blocks it).

**Every task is built with nested sessions.** A nested session is a separate Claude Code session the advisor starts for one piece of the task. In the Claude Desktop app it appears in the sidebar under the advisor, so the member can open, watch and steer it. Each one runs on the model and effort that piece needs, in its own worktree and branch. Independent pieces run **at the same time**, so the task finishes faster and the expensive model is used only for thinking.

**How the advisor runs a task**
1. **Plan table first.** Write it into the task's `Notes:` line and show it to the member: `piece | runs as | model / effort | branch`. Example for R-02:
   | piece | runs as | model / effort | branch |
   |---|---|---|---|
   | serializers | nested session | sonnet / medium | `roop/R-02-read-apis--serializers` |
   | views + urls | nested session | sonnet / medium | `roop/R-02-read-apis--views` |
   | tenant-scope tests | nested session | sonnet / medium | `roop/R-02-read-apis--tests` |
   | find U-04 mixin + fixtures | subagent `investigator` | haiku / low | - |
   | run all suites | subagent `test-runner` | haiku / low | - |
   | review | subagent `reviewer-lite` | sonnet / medium | - |
2. **Push the task branch**, then **start one nested session per code piece, all in one message** (Desktop: `start_session` with `initiation: "user_asked"`, `context: "fresh"`, `use_worktree: true`, `source_branch: <task branch>`, `model`, `effort`, `wake_when_done: true`). Brief template (the brief is all the nested session knows):
   > SupplyGuard AI, task `<ID>` for `<member>`, piece `<sub>`. First run `git checkout -b <task-branch>--<sub>`. Build: <exact files, CONTRACTS section, acceptance items for this piece>. Stay inside <folders>; do not touch the other pieces' files. Write tests and run them. Commit with trailers (`Task: <ID>`, `Status: in-progress`, `Member: <member>`) and `git push -u origin <task-branch>--<sub>`. Report: branch, last commit, test result, anything left.
3. **Model / effort per piece:**
   | Piece | Model / effort |
   |---|---|
   | screens, read APIs, serializers, seed scripts, Boomi build specs, deploy config, ordinary tests | sonnet / medium |
   | data model, auth/tenancy, approvals, ERP writes, state machine, AI agent prompts + guardrails, CONTRACTS | opus / medium (high only if the medium attempt failed) |
   | docs, task-file updates, simple renames, formatting | haiku / low |
   Escalate only after a smaller model failed twice on the same piece. Never run bulk implementation on Opus.
4. **While they run** the advisor waits for their notes (no polling). When each finishes: read its report, `git fetch`, `git merge --no-ff origin/<task-branch>--<sub>` into the task branch.
5. **Verify with cheap subagents:** `test-runner` (haiku/low), then `reviewer-lite` (sonnet/medium) - or `reviewer` (opus/high) for auth/tenancy, approvals, ERP writes, AI validation, state machine, security, CONTRACTS. Fixes go back to the nested session that built the piece (message it), not to the advisor.
6. **Then `/done`.** Never archive nested sessions; the member may want to reopen them. Deleting merged `--sub` branches only on request.

**Subagents vs nested sessions:** subagents (`.claude/agents/`) are in-chat helpers whose output comes back into the advisor's context - use them for short read-only jobs (lookups, test runs, reviews, doc updates). Anything that writes code is a nested session.

| Subagent | Model / effort | Use for |
|---|---|---|
| `investigator` | haiku / low | find files, read docs/specs/logs instead of the advisor reading them |
| `test-runner` | haiku / low | run suites / lint / build, report failures tersely |
| `reviewer-lite` | sonnet / medium | default review before `review`/`done` |
| `reviewer` | opus / high | sensitive areas only (see step 5) or when reviewer-lite says ESCALATE |
| `docs-writer` | haiku / low | task-file / Handoff / session-log updates |
| `implementer`, `boomi-builder` | sonnet / medium | **CLI fallback only** - when nested sessions are not available (plain `claude` in a terminal), the same pieces run as these subagents with `isolation: worktree` on the same `--sub` branches |

**Enforced:** `scripts/hooks/advisor_guard.py` blocks the advisor from editing files under `backend/ frontend/ mock-erp/ ai/ data/ boomi/ tests/ scripts/ deploy/`. Nested sessions are allowed because they work on a `--sub` branch; subagents are allowed too. One-off override: start Claude Code with `SG_ALLOW_ADVISOR_EDITS=1` (ask the user first).
**Keep the advisor small:** send `investigator` instead of reading more than 2 files; past ~150k tokens of context, `/handoff` and continue in a fresh advisor session.

Each task's "Model hint" says where the weight is. Full guide: `docs/AI_WORKFLOW.md`.

## 5. Git rules
- Branches: `main` (milestones only, tagged m1..m6, v1.0) <- `collaboration` (integration, always runnable) <- `<member>/<TASK-ID>-<slug>` (one per task) <- `<member>/<TASK-ID>-<slug>--<sub>` one per nested session (section 4), merged back into the task branch by the advisor.
- Never commit directly to `main` or `collaboration` - work lands through a PR from a task branch.
- **Auto-merge (GitHub Actions, no human needed):**
  - `.github/workflows/auto-merge.yml` runs on every push to a task branch. If that task is `review` or `done` in its task file, the merge into `collaboration` is clean (conflicts in `PROGRESS.md` / `progress/SESSION_LOG.md` are resolved automatically) and `scripts/run_checks.py` passes, it merges and pushes `collaboration`. `done` branches (and their `--sub` branches) are then deleted.
  - After every change on `collaboration`, the new `collaboration` is merged into every open task branch on GitHub (`auto-merge.yml` / `branch-sync.yml`), and locally the SessionStart hook pulls it into your branch. So everybody always works on the latest code.
  - A real conflict or failing check stops the auto-merge (red X on the Actions tab, reason in the run summary). The branch owner runs `/sync` (Claude resolves it) and pushes; auto-merge retries on that push. Anyone can also run `/merge-prs`.
  - Claude must therefore always `git pull` its own task branch before pushing (the bot may have added a sync merge) - never force-push.
- **Claude merges PRs itself.** Every member is a repo admin and the team has agreed that no human approval is needed. A branch is mergeable when: latest `collaboration` merged into it, conflicts resolved, all affected tests green, `reviewer` verdict APPROVE (no blockers), task status `review` or `done`. Then: open the PR (if none), merge with a merge commit (`gh pr merge <n> --merge --delete-branch`, add `--admin` if protection blocks it), confirm `collaboration` still passes. `/done` does this for the current task; `/merge-prs` does it for every open PR / ready task branch.
- **Conflict rules:** own stream -> resolve keeping both intents; another member's folder -> take theirs, unless both sides only added lines (keep both); `docs/CONTRACTS.md` -> keep both if they do not contradict and add a changelog line, otherwise stop and ask the user; `PROGRESS.md` -> regenerate with `scripts/progress.py`.
- **Merge, don't rebase:** bring `collaboration` into a task branch with `git merge origin/collaboration`, never rebase + force-push (the bot and teammates push merge commits to the same branches).
- **Never:** force-push `collaboration`, `main` or a task branch; merge with failing tests or a CHANGES REQUIRED verdict; merge a branch that has uncommitted work in another session; push to `main` outside the sprint-end milestone (S-02).
- Commit message format (Conventional Commits + trailers - `scripts/progress.py` reads the trailers):
  ```
  feat(integrations): accept disruption events with dedupe

  What: POST /events/disruptions, API-key auth, event_id dedupe.
  Left: Boomi publish call stubbed; next is BoomiClient retry.

  Task: R-06
  Status: in-progress
  Member: roop
  ```
  `Status:` one of `in-progress | review | done | blocked`.
- See `docs/GIT_WORKFLOW.md` for PR flow, conflict handling and milestones.

## 6. Engineering rules
- Code against `docs/CONTRACTS.md`. Changing it = PR touching that file + changelog line + tell both teammates.
- Tenant comes from JWT claims or API key, never from request body/query. Every tenant-owned query is tenant-filtered.
- No ERP write without a recorded approval. RiskCase state changes only via `apps.workflow.services.transition()`.
- AI output is schema-validated and fact-checked before it is stored as actionable; missing data is stated, never invented.
- No secrets in git or logs. Use `.env` (gitignored) and `.env.example`.
- Tests with every feature (pytest / Vitest). Do not mark a task `done` with failing tests.
- Stack: Python 3.12, Django 5, DRF, PostgreSQL 16, FastAPI (mock ERP), React 18 + Vite + TS + Tailwind, TanStack Query.

## 7. Updating progress (this is how the team knows where we are)
- Task status lives ONLY in `tasks/<MEMBER>.md` (`- **Status:** ...` line) - keep the format exactly.
- `PROGRESS.md` is generated: `python scripts/progress.py all --write`. Never hand-edit it.
- `progress/SESSION_LOG.md` is append-only, one entry per session.
- A dependency counts as satisfied when it is `done` or `review` (review tasks are already merged into `collaboration` and only wait for a final sign-off).
- Asking "what has Nikhil done / what's pending?" -> run `python scripts/progress.py nikhil` and answer from its output plus the Handoff block.
