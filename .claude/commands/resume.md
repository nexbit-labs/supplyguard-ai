---
description: Continue a team member's pending/ongoing work from where they left off (e.g. /resume roop)
argument-hint: <roop|nikhil|uday> [task-id]
model: opus
effort: medium
---
Resume work for team member: **$ARGUMENTS**

You are the ADVISOR (Opus 5.5). Follow CLAUDE.md exactly.

1. **Identify member + task.** First word of the arguments is the member (roop/nikhil/uday). If missing, use `git config user.name`; if still unclear, ask. An optional second word is a task ID to force.
2. **Sync.** If there are uncommitted changes, stop and ask what to do with them. Otherwise `git checkout collaboration` (only when not already on a task branch) and run `python scripts/gitflow.py local-sync` - it updates `collaboration` and merges it into the current task branch.
3. **Load state.** Run `python scripts/progress.py <member>`. Read `tasks/<MEMBER>.md` (Handoff block first), the chosen task block, the CONTRACTS sections it touches, and the last 3 entries of `progress/SESSION_LOG.md` for that member.
4. **Pick the task.** Forced ID > the `in-progress` task > first `todo` task whose Depends are all `done`. If nothing is ready, report what blocks the member and suggest the most useful unblocking work (often a dependency owned by someone else - say who), then stop.
5. **Branch.** If the task branch exists (local or `origin/`), check it out and run `python scripts/gitflow.py local-sync` (merges latest `collaboration` in, never rebase); otherwise create it from `collaboration` with the name in the task block. Set the task `Status: in-progress` if it was `todo`.
6. **Explain + plan.** First give the member a short "what this task is and why" in their own terms (CLAUDE.md section 2: React analogies for Roop, Python/data-pipeline analogies for Nikhil and Uday). Then write the plan table (CLAUDE.md section 4 step 1: `piece | runs as | model / effort | branch`) into the task's `Notes:` line and show it.
7. **Execute with nested sessions.** Push the task branch, then start one nested session per code piece in ONE message (CLAUDE.md section 4 step 2: model/effort per piece, own worktree, `source_branch` = task branch, `wake_when_done: true`, brief from the template). Short read-only jobs go to subagents (`investigator`, `test-runner`). Do not write code yourself - the guard hook blocks it. Wait for the sessions' notes; merge each `--sub` branch into the task branch as it finishes. In a terminal without the Desktop app, use `implementer` / `boomi-builder` subagents on the same `--sub` branches instead.
8. **Review + merge.** Merge every nested branch into the task branch, run `test-runner`, and when the acceptance criteria look met run `reviewer-lite` (or `reviewer` for auth/tenancy, approvals, ERP writes, AI validation, state machine, security, CONTRACTS). Fix blockers. If everything is met, run the `/done <task-id>` procedure - it merges into `collaboration` itself and then continues with the next ready task (back to step 3). Otherwise keep `in-progress`.
9. **Always finish with the `/handoff` procedure** (update Handoff block, session log, PROGRESS.md, commit, push) - even if you stop early, hit a blocker, or the user interrupts.

Report at the end: task, what was completed, tests result, what is left, next step.
