# SupplyGuard AI

**AI-powered Supplier Risk Intelligence & Supply Chain Recovery** - Boomi World 2027 Hackathon POC and Boomi Marketplace submission candidate.

> Predict -> Understand -> Recover -> Verify. SupplyGuard AI detects supplier disruptions, finds affected purchase orders, explains the risk with evidence, recommends recovery options, routes them for human approval and executes the approved change through Boomi - with a full audit trail.

**Deadline:** 31 Dec 2026 · **Team:** Roop (frontend), Nikhil (data & AI), Uday (backend & integration) · **Live progress:** [PROGRESS.md](PROGRESS.md)

## Start here (every team member)
1. Clone and switch to the integration branch:
   ```powershell
   git clone https://github.com/nexbit-labs/supplyguard-ai.git
   cd supplyguard-ai
   git checkout collaboration
   git config user.name "Nikhil"   # your name: Roop / Nikhil / Uday
   ```
2. Open Claude Code in the repo root (`claude`), switch to the advisor model: `/model opus`.
3. Type **`/resume <your-name>`** - Claude picks up your pending/ongoing task from where you (or anyone) left off and works through it with subagents.
4. Before you stop: **`/handoff`** - saves where you left off and pushes.

Automatic: Claude Code pulls the latest `collaboration` into your branch when it starts; a pushed task marked `review`/`done` is merged into `collaboration` by GitHub Actions when tests pass, and the new `collaboration` is pushed into every open task branch. See [docs/GIT_WORKFLOW.md](docs/GIT_WORKFLOW.md#automation-runs-by-itself).

Check anyone's progress: `/status nikhil` in Claude Code, or `python scripts/progress.py nikhil` in a terminal.

## Documents
| File | What |
|---|---|
| [CLAUDE.md](CLAUDE.md) | Rules Claude follows in this repo (ownership, session protocol, advisor + subagents, git) |
| [docs/PROJECT_PLAN.md](docs/PROJECT_PLAN.md) | 45-day plan, sprints, milestones, critical path, definition of done |
| [docs/CONTRACTS.md](docs/CONTRACTS.md) | Shared interfaces: events, REST API, mock ERP, agent schemas, state machine |
| [docs/AI_WORKFLOW.md](docs/AI_WORKFLOW.md) | How to use Claude: advisor (Opus 5.5), subagents, model/effort routing |
| [docs/GIT_WORKFLOW.md](docs/GIT_WORKFLOW.md) | Branches, commit trailers, PRs, milestones |
| [docs/BRD_HLD_LLD.md](docs/BRD_HLD_LLD.md) | Business requirements, high- and low-level design (source: `docs/source/*.docx`) |
| [docs/PRODUCT_STRATEGY.md](docs/PRODUCT_STRATEGY.md) | Why this product, competition, demo story |
| [tasks/ROOP.md](tasks/ROOP.md) · [tasks/NIKHIL.md](tasks/NIKHIL.md) · [tasks/UDAY.md](tasks/UDAY.md) · [tasks/SHARED.md](tasks/SHARED.md) | Task lists with status, acceptance criteria and handoff notes |
| [PROGRESS.md](PROGRESS.md) | Generated team board (do not edit) |
| [progress/SESSION_LOG.md](progress/SESSION_LOG.md) | Append-only log of every work session |

## Repository layout
```
backend/      Django REST API (Uday: core + apps/integrations, Nikhil: apps/intelligence + apps/workflow, Roop: apps/portal)
frontend/     React + Vite + TS + Tailwind SaaS UI (Roop)
mock-erp/     FastAPI demo ERP that Boomi reads/writes (Nikhil)
boomi/        Boomi assets: processes/ + api/ (Uday), processes/correlation/ + agents/ (Nikhil), flow/ + marketplace/ (Roop)
ai/           Agent JSON schemas, fixtures, prompts (Nikhil)
data/         Synthetic seed data + test scenarios (Nikhil)
scripts/      progress.py and helper scripts
tasks/        Per-member task files (single source of task status)
progress/     Session log
.claude/      Subagents and slash commands shared by the team
```

## Architecture (logical)
```
React SaaS UI ──HTTPS──> Django REST + PostgreSQL ──> Boomi API Management ──> Boomi Integration ──> ERP / WMS / TMS (mock ERP for POC)
                                   ^                                              │      ^
                                   │ callbacks                                    v      │ approved write
                                   └──────────── Agentstudio agents <── Event Streams    │
                                                         │                               │
                                                         └──> Boomi Flow (human approval)┘
```
