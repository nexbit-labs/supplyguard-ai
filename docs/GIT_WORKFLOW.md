# Git Workflow

## Branch tree
```
main                                   milestone releases only (tags m1..m6, v1.0)
 └── collaboration                     integration branch, always runnable, default for daily work
      ├── uday/U-06-event-intake       one branch per task
      │    └── uday/U-06-event-intake--boomi-client     optional sub-branch (parallel subagent / worktree)
      ├── nikhil/N-04-impact-engine
      └── roop/R-01-frontend-scaffold
```
Git cannot have both `a/b` and `a/b/c` as branch names, so sub-branches use `--<sub>` suffix, not a slash.

## Daily flow
```powershell
git checkout collaboration
git pull
git checkout -b uday/U-03-core-models        # or: git checkout uday/U-03-core-models && python scripts/gitflow.py local-sync
# ... work, commit with trailers ...
git push -u origin uday/U-03-core-models
gh pr create --base collaboration --title "U-03: core data model + seed loader"
```
Or simply `/resume uday` and `/done U-03` in Claude Code.

## Commit message
```
<type>(<scope>): <summary in imperative, <= 72 chars>

What: what changed.
Left: what is not done yet / next step (required when Status is in-progress).

Task: U-03
Status: in-progress | review | done | blocked
Member: uday
```
Types: feat, fix, test, docs, refactor, chore, ci. Scope = app or folder (`integrations`, `frontend`, `boomi`, `tasks`).

## Pull requests and merging (Claude merges)
- Base: `collaboration`. Title: `<TASK-ID>: <title>`. Template in `.github/pull_request_template.md`.
- Every member is a repo admin; the team agreed that **Claude verifies and merges PRs itself** - no waiting for a human approval.
- Merge gate: latest `collaboration` merged in, conflicts resolved, tests green (CI once `ci.yml` exists), `reviewer` subagent verdict APPROVE in the PR, task status `review` or `done`.
- `/done <ID>` merges the current task and continues with the next one. `/merge-prs` verifies and merges every ready PR/branch for the whole team (run it any time, e.g. start of day).
- Merge method: "Create a merge commit" (`gh pr merge --merge --delete-branch`). Squash is NOT used - keep commits so trailers stay in history.
- After merge, the task branch and its `--<sub>` branches are deleted.

## Conflicts
`/sync` merges the latest `collaboration` into your branch (merge, never rebase). Rules (Claude applies them automatically):
- your own folders -> resolve, keeping both intents;
- another member's folders -> take theirs, unless both sides only added lines (keep both), and note it in the Handoff Blockers line for them;
- `docs/CONTRACTS.md` -> keep both if they do not contradict + changelog line; if they contradict, stop and ask;
- `PROGRESS.md` -> regenerate.

## Automation (runs by itself)
| What | Where | When | Does |
|---|---|---|---|
| Auto-merge | `.github/workflows/auto-merge.yml` -> `scripts/gitflow.py automerge` | every push to `roop/**`, `nikhil/**`, `uday/**` (not `--sub` branches) | if the task is `review`/`done` in its task file, merges into `collaboration` (clean merge or only PROGRESS/SESSION_LOG conflicts), runs `scripts/run_checks.py`, pushes, regenerates `PROGRESS.md`, deletes `done` branches |
| Sync task branches | same workflow after a merge, and `.github/workflows/branch-sync.yml` on any push to `collaboration` -> `scripts/gitflow.py sync-branches` | after `collaboration` changes | merges the new `collaboration` into every open task branch on GitHub; conflicts are listed in the run summary |
| Auto-pull (local) | `.claude/settings.json` SessionStart hook -> `scripts/gitflow.py local-sync` | every Claude Code start/resume | updates local `collaboration`, pulls your task branch, merges latest `collaboration` into it; skipped when you have uncommitted changes |
| Progress board | `.github/workflows/progress.yml` | push to `collaboration`/`main` touching `tasks/**` | regenerates `PROGRESS.md` |
| Checks | `scripts/run_checks.py` | auto-merge, and locally any time | runs every suite that exists: ai, mock-erp, backend (Postgres via `DATABASE_URL`), frontend lint/typecheck/test/build (backend also ruff) |
| CI | `.github/workflows/ci.yml` | task-branch pushes (`roop/**`, `nikhil/**`, `uday/**`), PRs to `collaboration`/`main`, pushes to `collaboration`/`main`, nightly, manual | same checks as `run_checks.py` (backend on Postgres 16, like the auto-merge gate); new top-level test folders must also be added to the CI matrix; `ci-ok` ("CI OK") is the aggregate check |

Pushes made with the `GITHUB_TOKEN` (bot merges) do not trigger CI, so `collaboration` is verified by the nightly/manual CI run, while the auto-merge gate runs `run_checks.py`.

If auto-merge shows a red X: open the run summary on the Actions tab - it says conflict (owner runs `/sync`, pushes) or failing checks (fix, push). The next push retries automatically.
Run it by hand: `python scripts/gitflow.py local-sync` (your machine), Actions tab -> auto-merge -> Run workflow (any branch).

## Milestones (S-02)
At the end of each sprint that sprint's S-02 driver (rotating, see `tasks/SHARED.md`) merges `collaboration` -> `main` via PR and tags `m1`..`m6`. Final: `v1.0` on 31 Dec.

## Recommended GitHub settings (any repo admin, once)
- Default branch: `collaboration`.
- `collaboration`: block force pushes and deletion only. Do NOT enable "require a pull request" - the auto-merge and sync Actions push to it directly with the workflow token.
- `main`: block force pushes and deletion; require a PR (milestone merges by the S-02 driver).
- Settings -> Actions -> General -> Workflow permissions: "Read and write" (already working: the progress board pushes).
- Allow "Rebase and merge" and "Create a merge commit".
