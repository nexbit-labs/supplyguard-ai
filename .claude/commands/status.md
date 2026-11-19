---
description: Show a member's (or the whole team's) progress - done, in progress, pending, overdue, next task, where they left off
argument-hint: [roop|nikhil|uday|shared|all]
model: haiku
effort: low
---
Show progress for: **$ARGUMENTS** (default: all)

1. `git fetch --all --quiet` then `git pull --ff-only` if on `collaboration` (skip pull on other branches).
2. Run `python scripts/progress.py $ARGUMENTS` (use `all` if no argument; for `all` also run it for roop, nikhil and uday).
3. Answer in this shape, per member:
   - **Done:** task IDs + titles (with commit)
   - **In progress / review:** task, checkbox count, branch
   - **Pending:** count, and the next 3 in order with what they wait on
   - **Overdue:** list, or "none"
   - **Where they left off:** the Handoff "Where I left off" + "Next step" + "Blockers"
   - **Recent commits:** last 3
4. End with one line: overall team % done and days left until 31 Dec 2026.
Do not modify any files.
