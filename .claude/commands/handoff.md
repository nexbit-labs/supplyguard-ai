---
description: Save exactly where you left off - task file, session log, PROGRESS.md - then commit and push
argument-hint: [optional note]
model: haiku
effort: low
---
End-of-session handoff. Extra note from the user: $ARGUMENTS

1. Determine the member (current branch prefix `roop/`, `nikhil/`, `uday/`, else `git config user.name`) and current task ID (from branch name).
2. Run `test-runner` on the parts touched this session; note the result honestly.
3. Use the `docs-writer` subagent (or do it directly) to update `tasks/<MEMBER>.md`:
   - task `Status` (in-progress / review / blocked / done - done only if the /done criteria are met),
   - tick acceptance boxes that are proven,
   - `Notes:` = remaining subtasks,
   - overwrite the **Handoff** block: today's date, current task, branch, where I left off (concrete: file/function/state), next step (one concrete action), blockers (who/what).
4. Append one entry to `progress/SESSION_LOG.md` using its template.
5. `python scripts/progress.py all --write`.
6. Commit everything on the task branch:
   ```
   chore(tasks): handoff <TASK-ID> - <short state>

   What: <done this session>
   Left: <what remains>

   Task: <TASK-ID>
   Status: <status>
   Member: <member>
   ```
   then `git pull --no-rebase origin <branch>` (only if it exists on origin - the sync bot may have added a merge) and `git push -u origin <branch>`. If the task is `review`/`done`, the auto-merge Action merges it into `collaboration` on this push.
7. If the task is in `review`/`done` and no PR exists, open one to `collaboration` (`gh pr create --base collaboration`); if `gh` is not logged in, print the compare URL instead.
8. Print a 5-line summary: task, status, tests, pushed branch, next step.
Never leave work uncommitted or unpushed at the end of this command.
