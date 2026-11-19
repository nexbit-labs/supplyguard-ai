# AI Workflow - Advisor (Opus 5.5) + nested subagents

How each of us uses Claude Code on this repo so that work is fast, consistent and token-efficient.

## 1. Setup (once per machine)
```powershell
git clone https://github.com/nexbit-labs/supplyguard-ai.git
cd supplyguard-ai
git checkout collaboration
git config user.name "Roop"     # or Nikhil / Uday - used to detect who you are
claude                          # start Claude Code in the repo root
```
Inside Claude Code: `/model opus` (the advisor). Optional plugins that help: Boomi `bc-integration` (boomi-integration skill), `bc-agentstudio` (boomi-agentstudio skill), `superpowers`.

## 2. The one command you need
```
/resume roop        (or nikhil / uday)
```
This pulls `collaboration`, prints your progress, reads your Handoff block, picks the right task, plans it, delegates to subagents, implements, tests, reviews, updates your task file, commits and pushes. When you stop for the day: `/handoff`.

Other commands:
| Command | What it does |
|---|---|
| `/status nikhil` / `/status all` | done / in progress / pending / overdue / next task, from task files + git trailers |
| `/handoff` | save where you left off (task file + session log + PROGRESS.md), commit, push |
| `/done U-06` | verify acceptance, mark done, merge into `collaboration`, then continue with your next task |
| `/sync` | merge the latest `collaboration` into your task branch, resolve conflicts, run tests |
| `/merge-prs` | verify, fix conflicts and merge every ready PR / task branch of the team into `collaboration` |

Without Claude: `python scripts/progress.py uday` prints the same status.

Automatic (no command needed): when Claude Code starts it pulls your branch and the latest `collaboration` for you; when you push a task that is `review`/`done`, GitHub Actions merges it into `collaboration` if tests pass, then pushes the new `collaboration` into everybody's task branches. Details: `docs/GIT_WORKFLOW.md` -> Automation.

## 3. Advisor pattern
```
                 ┌──────────────── ADVISOR (main session, Opus 5.5, high effort) ────────────────┐
                 │ reads task + CONTRACTS, splits into subtasks, picks agents, reviews, commits │
                 └───────┬──────────────┬──────────────┬──────────────┬──────────────┬──────────┘
                         │              │              │              │              │
                 investigator     implementer    boomi-builder    test-runner      reviewer
                 haiku / low     sonnet / medium sonnet / medium  haiku / low     opus / high
                 (locate, read)  (code + tests)  (Boomi specs)    (run, report)   (gate before done)
```
1. **Understand** - advisor reads the task block and relevant CONTRACTS section. For unfamiliar code it sends `investigator` (cheap) instead of reading many files itself.
2. **Plan** - advisor writes a short subtask list into the task's Notes line (so a teammate can continue it).
3. **Delegate** - independent subtasks go to `implementer` / `boomi-builder` in parallel (one message, several Agent calls). Two implementers on the same app -> `isolation: worktree` and sub-branches `<member>/<TASK-ID>-<slug>--<sub>`.
4. **Verify** - `test-runner` runs the suites; failures go back to an implementer with the exact error.
5. **Review** - `reviewer` checks the diff against acceptance criteria, CONTRACTS and the security rules. Fix findings.
6. **Record** - `docs-writer` (or the advisor) updates the task file, then commit + push (`/handoff` or `/done`).

## 4. Model and effort routing

| Work type | Model | Effort | Why |
|---|---|---|---|
| Architecture, contracts, data model, state machine, security/tenant logic, agent prompt design | Opus 5.5 | high | mistakes here are expensive and spread to other streams |
| Final review before `done`, debugging something that failed twice | Opus 5.5 | high | judgment |
| Feature code with clear spec (views, serializers, React screens, Boomi processes, tests) | Sonnet 5.5 | medium | best speed/quality for implementation |
| Searching code, reading docs, running tests, formatting, task-file/progress updates, changelogs | Haiku 5.5 | low | cheap and fast |

Each task in `tasks/*.md` has a **Model hint** with the default. The advisor may escalate (e.g. implementer fails twice -> advisor or reviewer takes it) but should not run bulk implementation on Opus.

Token hygiene:
- Prefer subagents for wide searches; keep only conclusions in the advisor context.
- Point subagents at files/sections instead of pasting content.
- One task per session where possible; `/handoff` and start a fresh session for the next task.

## 5. Nested sessions for parallel work
For a big task (e.g. R-04 + R-05 screens, or N-05 + N-06 agents) the advisor can start separate Claude Code sessions / worktrees per subtask. Rules:
- Each sub-session works on its own sub-branch `<member>/<TASK-ID>-<slug>--<sub>` cut from the task branch.
- Sub-sessions never touch the same files.
- The advisor merges sub-branches into the task branch, runs tests, then reviewer, then PR to `collaboration`.

## 6. What "done" means for Claude
A task is `done` only when: acceptance boxes ticked with evidence, tests pass (output seen, not assumed), reviewer found no blocking issue, task file + PROGRESS.md updated, merged into `collaboration` (Claude merges it itself). Then Claude moves straight on to the next ready task. If any of this is missing, status is `review` or `in-progress` - never claim `done`.
