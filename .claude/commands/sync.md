---
description: Bring the current task branch up to date with collaboration and re-run tests
model: sonnet
effort: low
---
1. Commit or stash nothing silently - if the tree is dirty, commit it as WIP with trailers (`Status: in-progress`).
2. `python scripts/gitflow.py local-sync`. If it reports a conflict, do it by hand: `git pull --no-rebase origin <branch>` then `git merge origin/collaboration` (merge, never rebase).
3. Conflicts:
   - in your own stream's folders -> resolve, keeping both intents;
   - in another member's folders -> take theirs (`collaboration`) and note it in the Handoff Blockers line for them;
   - in `docs/CONTRACTS.md` -> stop, show both versions, ask the user.
4. Run `test-runner` on affected suites. Report results.
5. `git push` (plain push - never force; the auto-merge Action re-checks the branch on this push).
6. Print what came in from `collaboration` (`git log --oneline` of the merge, max 10 lines).
