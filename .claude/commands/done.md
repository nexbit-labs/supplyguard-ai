---
description: Verify a task meets its acceptance criteria, mark it done, push and open a PR to collaboration
argument-hint: <task-id e.g. R-06>
model: opus
effort: medium
---
Complete task **$ARGUMENTS**.

1. Find the task block in `tasks/*.md`. Make sure you are on its branch and it is up to date: `python scripts/gitflow.py local-sync` (pulls the branch + merges latest `collaboration`); on a conflict resolve it with the CLAUDE.md conflict rules and commit.
2. `test-runner`: run all suites affected (backend pytest, frontend test+build, mock-erp tests, as relevant). All must pass.
3. `reviewer-lite` on the branch (`reviewer` instead for auth/tenancy, approvals, ERP writes, AI validation, state machine, security, CONTRACTS, or if reviewer-lite says ESCALATE). Any blocker -> send the fix to the nested session that built that piece (SendMessage to its session id; `implementer` subagent only in a terminal without the Desktop app) and repeat steps 2-3. Do not continue with open blockers.
4. Walk the acceptance criteria one by one; tick each box only with evidence (test name, file:line, Boomi TEST_LOG line, screenshot path). If any criterion is not met, set `Status: review` (or keep `in-progress`), explain what's missing, run `/handoff` and stop.
5. If all met: set `Status: done`; commit; then set `Done in:` to that commit's short hash in a follow-up edit; update the Handoff block (next task = what `python scripts/progress.py <member>` now reports as NEXT); append to `progress/SESSION_LOG.md`; `python scripts/progress.py all --write`.
6. Commit with trailers `Task: <ID>` / `Status: done` / `Member: <member>`; push.
7. **Merge it yourself** (team policy, CLAUDE.md section 5):
   - `python scripts/gitflow.py local-sync` once more (merge, never rebase); resolve conflicts with the CLAUDE.md conflict rules; re-run `test-runner` if anything changed. `git push` the task branch - the auto-merge Action also merges it on this push, so if it is already in `collaboration` when you get to the next step, just confirm and skip the merge.
   - With `gh` logged in: `gh pr create --base collaboration --title "<ID>: <title>" --body-file <filled PR template>` (skip if a PR exists), then `gh pr merge <n> --merge --delete-branch` (add `--admin` if branch protection blocks it).
   - Without `gh`: `git checkout collaboration && git pull --ff-only && git merge --no-ff <branch>` with message `merge: <ID> <title>` + the trailers (`Task:`, `Status: done`, `Member:`), run `test-runner` on the result, `git push origin collaboration`, `git push origin --delete <branch>` (and its `--<sub>` sub-branches).
   - If the post-merge tests fail: fix forward at once on `<member>/<ID>-fix`; never leave `collaboration` red.
8. If the contract file changed, say so loudly and list who must be notified.
9. **Continue automatically:** run `python scripts/progress.py <member>`, print "Next: <ID> <title>", and start the `/resume` procedure from step 5 for that task. Stop only if nothing is ready (report what blocks it and who owns the blocker), the user said stop, or the context is long - then run `/handoff`.
Report per finished task: merge commit, tests, next task.
