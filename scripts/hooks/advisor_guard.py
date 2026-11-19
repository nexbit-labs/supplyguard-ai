#!/usr/bin/env python3
"""PreToolUse guard: the advisor (main session, Opus) must delegate code changes.

Blocks Write / Edit / NotebookEdit from the MAIN session on source and test files,
so the expensive advisor model plans and reviews while cheaper subagents
(implementer / boomi-builder on Sonnet) write the code in their own nested worktree branches.

Allowed for the advisor: task files, docs, progress files, CLAUDE.md, .claude/ config,
markdown anywhere. Subagent calls (hook input has `agent_id`) are never blocked.
Escape hatch for a one-off: set SG_ALLOW_ADVISOR_EDITS=1 before starting Claude Code.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import PurePosixPath

CODE_ROOTS = ("backend/", "frontend/", "mock-erp/", "ai/", "data/", "boomi/", "tests/", "scripts/", "deploy/")
ALWAYS_OK_SUFFIXES = (".md",)


def main() -> int:
    if os.environ.get("SG_ALLOW_ADVISOR_EDITS") == "1":
        return 0
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0
    if data.get("agent_id"):  # a subagent - always allowed
        return 0
    path = (data.get("tool_input") or {}).get("file_path") or (data.get("tool_input") or {}).get("notebook_path") or ""
    if not path:
        return 0
    cwd = data.get("cwd") or os.getcwd()
    norm = path.replace("\\", "/")
    root = cwd.replace("\\", "/").rstrip("/") + "/"
    rel = norm[len(root):] if norm.lower().startswith(root.lower()) else norm
    rel = str(PurePosixPath(rel))
    if rel.endswith(ALWAYS_OK_SUFFIXES) or not rel.startswith(CODE_ROOTS):
        return 0
    print(
        f"Advisor rule (CLAUDE.md section 4): the main Opus session does not edit code ({rel}). "
        "Delegate it: Agent tool with subagent_type `implementer` (or `boomi-builder`), which runs on Sonnet "
        "in its own nested worktree branch `<task-branch>--<sub>`. Even a one-line fix goes to "
        "`implementer` (cheap). Only if delegation is impossible, ask the user to restart with SG_ALLOW_ADVISOR_EDITS=1.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
