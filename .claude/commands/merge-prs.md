---
description: Verify, fix conflicts and merge every open PR / ready task branch into collaboration
argument-hint: [optional: member or PR number to limit the run]
model: opus
effort: medium
---
Merge everything that is ready into `collaboration`. Scope: $ARGUMENTS (default: all members).

Team policy (CLAUDE.md section 5): every member is an admin and no human approval is required - Claude verifies and merges. The auto-merge Action already merges clean, green, `review`/`done` branches on push; this command handles what it could not (conflicts, failing checks, missed runs) and also runs the `reviewer`.

1. `git fetch --all --prune`. Do the work in a separate worktree (`git worktree add <scratch>/merge-<date> origin/collaboration`) so you never switch branches under another running session.
2. **Find candidates:** open PRs to `collaboration` (`gh pr list --base collaboration --state open`), plus remote task branches `origin/<member>/<ID>-*` (not `--<sub>`) that are ahead of `collaboration` and whose task status in `tasks/*.md` on that branch is `review` or `done`. Skip `in-progress` / `blocked` / `todo` and list them as "not ready".
3. **Order:** dependencies first (a task whose Depends include another candidate merges after it); otherwise oldest first.
4. **For each candidate**, one at a time:
   a. Check it out in the worktree and bring it up to date with `git merge origin/collaboration` (never rebase). Resolve conflicts with the CLAUDE.md conflict rules. A contradiction in `docs/CONTRACTS.md` -> skip and report.
   b. `test-runner` on every suite the branch touches (backend pytest, frontend test + build, mock-erp, ai schema tests).
   c. `reviewer-lite` on `origin/collaboration...HEAD` (`reviewer` for the sensitive areas in CLAUDE.md section 4). CHANGES REQUIRED -> if the fix is small and inside that member's stream, delegate it to `implementer`, commit with that task's trailers, re-test, re-review; otherwise skip and report.
   d. Push the branch (plain push, never force), then merge: `gh pr create` if no PR, `gh pr merge <n> --merge --delete-branch` (`--admin` if protection blocks). Without `gh`: `git merge --no-ff` into `collaboration` with message `merge: <ID> <title>` + trailers, test, `git push origin collaboration`, delete the remote branch and its `--<sub>` branches.
   e. Re-run the quick suites on the new `collaboration` before the next candidate.
5. Run `python scripts/gitflow.py sync-branches` so every open task branch gets the new `collaboration` (the branch-sync Action also does this on push).
6. Append one entry to `progress/SESSION_LOG.md` (merged / skipped / not ready, with reasons), run `python scripts/progress.py all --write`, and land both through a small branch `<you>/merge-log-<date>` merged the same way.
7. Remove the worktree. Report a table: PR/branch, task, result (merged / skipped / not ready), reason, merge commit.
