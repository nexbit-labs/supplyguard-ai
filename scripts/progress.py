#!/usr/bin/env python3
"""SupplyGuard AI progress tracker.

Reads task status from tasks/*.md and commit trailers (Task:/Status:/Member:) from git,
then prints a per-member report or regenerates PROGRESS.md.

Usage:
    python scripts/progress.py                 # whole team, short
    python scripts/progress.py nikhil          # one member: done / in progress / pending / next / handoff
    python scripts/progress.py all --write     # regenerate PROGRESS.md
    python scripts/progress.py --brief         # one line per member (used by the SessionStart hook)
    python scripts/progress.py roop --ready    # exit 0 + READY line if a task can start,
                                               # exit 3 + what it is waiting on (task, owner, status) if not
"""
from __future__ import annotations

import argparse
import datetime as dt
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TASK_DIR = ROOT / "tasks"
MEMBERS = {"roop": "ROOP.md", "nikhil": "NIKHIL.md", "uday": "UDAY.md", "shared": "SHARED.md"}
STATUSES = ("todo", "in-progress", "blocked", "review", "done")
YEAR = 2026
# Sprint end dates (inclusive) - keep in sync with docs/PROJECT_PLAN.md
SPRINT_END = {
    "S0": dt.date(2026, 11, 23),
    "S1": dt.date(2026, 11, 30),
    "S2": dt.date(2026, 12, 7),
    "S3": dt.date(2026, 12, 14),
    "S4": dt.date(2026, 12, 20),
    "S5": dt.date(2026, 12, 26),
    "S6": dt.date(2026, 12, 31),
}

HEAD_RE = re.compile(r"^###\s+([A-Z]-\d{2}):\s*(.+?)\s*$")
FIELD_RE = re.compile(r"^-\s+\*\*(Status|Sprint|Branch|Done in|Driver):\*\*\s*(.*)$")
DEPENDS_RE = re.compile(r"\*\*Depends:\*\*\s*(.*)$")
TASK_ID_RE = re.compile(r"\b([RNUS]-\d{2})\b")


@dataclass
class Task:
    id: str
    title: str
    owner: str
    status: str = "todo"
    sprint: str = ""
    depends: list[str] = field(default_factory=list)
    branch: str = ""
    done_in: str = ""
    boxes_done: int = 0
    boxes_total: int = 0

    @property
    def sprint_code(self) -> str:
        m = re.match(r"(S\d)", self.sprint)
        return m.group(1) if m else ""

    def overdue(self, today: dt.date) -> bool:
        end = SPRINT_END.get(self.sprint_code)
        # tasks spanning sprints ("S3-S4") are judged by the last sprint mentioned
        last = re.findall(r"S\d", self.sprint)
        if last:
            end = SPRINT_END.get(last[-1], end)
        return bool(end) and self.status != "done" and today > end


def parse_file(owner: str, path: Path) -> tuple[list[Task], str]:
    return parse_text(owner, path.read_text(encoding="utf-8"))


def parse_text(owner: str, text: str) -> tuple[list[Task], str]:
    tasks: list[Task] = []
    handoff_lines: list[str] = []
    in_handoff = False
    cur: Task | None = None
    for raw in text.splitlines():
        line = raw.rstrip()
        if line.startswith("## "):
            in_handoff = line.strip().lower() == "## handoff"
            continue
        if in_handoff:
            if line.startswith("---"):
                in_handoff = False
            elif line.startswith("- "):
                handoff_lines.append(line)
            continue
        m = HEAD_RE.match(line)
        if m:
            cur = Task(id=m.group(1), title=m.group(2), owner=owner)
            tasks.append(cur)
            continue
        if not cur:
            continue
        f = FIELD_RE.match(line)
        if f:
            key, val = f.group(1), f.group(2).strip()
            if key == "Status":
                cur.status = val.split()[0].lower() if val else "todo"
            elif key == "Sprint":
                cur.sprint = val.split("|")[0].strip()
                d = DEPENDS_RE.search(val)
                if d:
                    cur.depends = TASK_ID_RE.findall(d.group(1))
            elif key == "Branch":
                cur.branch = val.strip("` ")
            elif key == "Done in":
                cur.done_in = "" if val in ("-", "") else val
        for box in re.findall(r"\[( |x|X)\]", line):
            cur.boxes_total += 1
            cur.boxes_done += box.lower() == "x"
    return tasks, "\n".join(handoff_lines)


def load() -> tuple[dict[str, Task], dict[str, str]]:
    all_tasks: dict[str, Task] = {}
    handoffs: dict[str, str] = {}
    for owner, fname in MEMBERS.items():
        p = TASK_DIR / fname
        if not p.exists():
            continue
        tasks, handoff = parse_file(owner, p)
        handoffs[owner] = handoff
        for t in tasks:
            all_tasks[t.id] = t
    return all_tasks, handoffs


def _trailer(body: str, key: str) -> str:
    # Parsed from the whole body (not git's trailer parser) so a blank line before
    # Co-Authored-By or similar does not hide our Task/Status/Member lines.
    vals = re.findall(rf"^{key}:\s*(.+?)\s*$", body, flags=re.MULTILINE | re.IGNORECASE)
    return ",".join(vals)


def git_commits(limit: int = 300) -> list[dict[str, str]]:
    fmt = "%h%x1f%ad%x1f%an%x1f%s%x1f%B%x1e"
    try:
        out = subprocess.run(
            ["git", "log", "--all", f"-n{limit}", "--date=short", f"--format={fmt}"],
            cwd=ROOT, capture_output=True, text=True, encoding="utf-8", check=True,
        ).stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        return []
    rows = []
    for rec in out.split("\x1e"):
        parts = rec.strip().split("\x1f")
        if len(parts) < 5:
            continue
        task = _trailer(parts[4], "Task")
        if not task:
            continue
        rows.append(dict(hash=parts[0].strip(), date=parts[1], author=parts[2], subject=parts[3],
                         task=task, status=_trailer(parts[4], "Status"),
                         member=_trailer(parts[4], "Member").lower()))
    return rows


# A dependency is satisfied when it is `done`, or `review`: review tasks are already merged into
# `collaboration` by auto-merge and only wait for a final sign-off (e.g. the S-01 contracts call),
# so the code a dependent task needs is there.
SATISFIED = {"done", "review"}


def dep_ok(d: str, tasks: dict[str, Task]) -> bool:
    return d not in tasks or tasks[d].status in SATISFIED


def ready(t: Task, tasks: dict[str, Task]) -> bool:
    return all(dep_ok(d, tasks) for d in t.depends)


def next_task(owner: str, tasks: dict[str, Task]) -> Task | None:
    mine = [t for t in tasks.values() if t.owner == owner]
    for t in mine:
        if t.status == "in-progress":
            return t
    for t in mine:
        if t.status == "todo" and ready(t, tasks):
            return t
    return None


def ready_report(owner: str, tasks: dict[str, Task]) -> tuple[str, int]:
    nxt = next_task(owner, tasks)
    if nxt:
        state = "continue" if nxt.status == "in-progress" else "start"
        return f"READY ({state}): {nxt.id} {nxt.title} (branch {nxt.branch})", 0
    mine = [t for t in tasks.values() if t.owner == owner]
    # only the next few pending tasks matter - show what each is waiting on
    upcoming = [t for t in mine if t.status == "todo"][:3]
    lines = [f"WAITING: no task of {owner} can start yet"]
    for t in upcoming:
        waits = [f"{d} ({tasks[d].owner}, {tasks[d].status})" for d in t.depends if not dep_ok(d, tasks)]
        lines.append(f"  {t.id} {t.title} waits on: {', '.join(waits)}")
    for t in mine:
        if t.status in ("review", "blocked"):
            lines.append(f"  own task {t.id} is {t.status}")
    if not any(t.status != "done" for t in mine):
        lines = [f"ALL DONE: every task of {owner} is done"]
    return "\n".join(lines), 3


def pct(ts: list[Task]) -> int:
    return round(100 * sum(t.status == "done" for t in ts) / len(ts)) if ts else 0


def fmt_task(t: Task, today: dt.date, tasks: dict[str, Task]) -> str:
    flags = []
    if t.overdue(today):
        flags.append("OVERDUE")
    if t.status == "todo" and not ready(t, tasks):
        waiting = [d for d in t.depends if not dep_ok(d, tasks)]
        flags.append("waiting on " + ", ".join(waiting))
    extra = f" ({'; '.join(flags)})" if flags else ""
    boxes = f" [{t.boxes_done}/{t.boxes_total}]" if t.boxes_total else ""
    done = f" @ {t.done_in}" if t.done_in else ""
    return f"  {t.id}  {t.title}  - {t.sprint}{boxes}{done}{extra}"


def member_report(owner: str, tasks: dict[str, Task], handoffs: dict[str, str],
                  commits: list[dict[str, str]], today: dt.date) -> str:
    mine = [t for t in tasks.values() if t.owner == owner]
    lines = [f"=== {owner.upper()} - {pct(mine)}% done ({sum(t.status == 'done' for t in mine)}/{len(mine)} tasks) ==="]
    for status, label in (("done", "DONE"), ("review", "IN REVIEW"), ("in-progress", "IN PROGRESS"),
                          ("blocked", "BLOCKED"), ("todo", "PENDING")):
        group = [t for t in mine if t.status == status]
        if group:
            lines.append(f"{label}:")
            lines += [fmt_task(t, today, tasks) for t in group]
    overdue = [t for t in mine if t.overdue(today)]
    if overdue:
        lines.append(f"OVERDUE: {', '.join(t.id for t in overdue)}")
    nxt = next_task(owner, tasks)
    lines.append(f"NEXT: {nxt.id} {nxt.title} (branch {nxt.branch})" if nxt else "NEXT: nothing ready - check blockers/dependencies")
    if handoffs.get(owner):
        lines.append("HANDOFF:")
        lines.append(handoffs[owner])
    ids = {t.id for t in mine}
    recent = [c for c in commits if c["member"] == owner or any(i.strip() in ids for i in c["task"].split(","))][:8]
    if recent:
        lines.append("RECENT COMMITS:")
        lines += [f"  {c['date']} {c['hash']} [{c['task']} {c['status']}] {c['subject']}" for c in recent]
    return "\n".join(lines)


def brief(tasks: dict[str, Task], today: dt.date) -> str:
    out = [f"SupplyGuard AI progress ({today.isoformat()}, deadline 2026-12-31, {(dt.date(2026, 12, 31) - today).days} days left)"]
    for owner in ("roop", "nikhil", "uday", "shared"):
        mine = [t for t in tasks.values() if t.owner == owner]
        if not mine:
            continue
        nxt = next_task(owner, tasks)
        od = sum(t.overdue(today) for t in mine)
        out.append(f"  {owner:<7} {pct(mine):>3}% done | in-progress: "
                   f"{', '.join(t.id for t in mine if t.status == 'in-progress') or '-'} | next: {nxt.id if nxt else '-'}"
                   f"{f' | {od} OVERDUE' if od else ''}")
    out.append("Run /resume <member> to continue, /status <member> for details.")
    return "\n".join(out)


def progress_md(tasks: dict[str, Task], handoffs: dict[str, str], commits: list[dict[str, str]], today: dt.date) -> str:
    md = ["# PROGRESS - SupplyGuard AI", "",
          "> Generated by `python scripts/progress.py all --write`. Do not edit by hand - edit `tasks/*.md`.", "",
          f"**As of:** {today.isoformat()} · **Deadline:** 2026-12-31 · **Days left:** {(dt.date(2026, 12, 31) - today).days}", "",
          "| Member | Done | In progress | Review | Blocked | Pending | Overdue | % | Next |",
          "|---|---|---|---|---|---|---|---|---|"]
    for owner in ("roop", "nikhil", "uday", "shared"):
        mine = [t for t in tasks.values() if t.owner == owner]
        c = {s: sum(t.status == s for t in mine) for s in STATUSES}
        nxt = next_task(owner, tasks)
        md.append(f"| {owner.capitalize()} | {c['done']} | {c['in-progress']} | {c['review']} | {c['blocked']} | {c['todo']} | "
                  f"{sum(t.overdue(today) for t in mine)} | {pct(mine)}% | {nxt.id if nxt else '-'} |")
    md += ["", "## Task board", "", "| ID | Task | Owner | Sprint | Status | Checks | Done in |", "|---|---|---|---|---|---|---|"]
    for t in sorted(tasks.values(), key=lambda t: (t.sprint_code, t.id)):
        st = t.status + (" ⚠ overdue" if t.overdue(today) else "")
        md.append(f"| {t.id} | {t.title} | {t.owner.capitalize()} | {t.sprint} | {st} | {t.boxes_done}/{t.boxes_total} | {t.done_in or '-'} |")
    md += ["", "## Where each member left off", ""]
    for owner in ("roop", "nikhil", "uday"):
        md += [f"### {owner.capitalize()}", handoffs.get(owner) or "- (no handoff yet)", ""]
    if commits:
        md += ["## Recent tracked commits", "", "| Date | Commit | Member | Task | Status | Subject |", "|---|---|---|---|---|---|"]
        md += [f"| {c['date']} | {c['hash']} | {c['member'] or c['author']} | {c['task']} | {c['status']} | {c['subject'].replace('|', '/')} |"
               for c in commits[:20]]
    return "\n".join(md) + "\n"


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("member", nargs="?", default="all", help="roop | nikhil | uday | shared | all")
    ap.add_argument("--write", action="store_true", help="regenerate PROGRESS.md")
    ap.add_argument("--brief", action="store_true", help="one line per member")
    ap.add_argument("--today", help="override date (YYYY-MM-DD) for testing")
    ap.add_argument("--ready", action="store_true", help="can this member start a task? (exit 0 ready, 3 waiting)")
    args = ap.parse_args()

    today = dt.date.fromisoformat(args.today) if args.today else dt.date.today()
    tasks, handoffs = load()
    if not tasks:
        print("No tasks found in tasks/*.md")
        return 1
    if args.ready:
        who = args.member.lower()
        if who not in MEMBERS or who == "all":
            print("--ready needs a member: roop | nikhil | uday | shared")
            return 2
        text, code = ready_report(who, tasks)
        print(text)
        return code
    commits = git_commits()

    if args.brief:
        print(brief(tasks, today))
    elif args.member.lower() == "all":
        print(brief(tasks, today))
    else:
        who = args.member.lower()
        if who not in MEMBERS:
            print(f"Unknown member '{args.member}'. Use one of: {', '.join(MEMBERS)}")
            return 2
        print(member_report(who, tasks, handoffs, commits, today))

    if args.write:
        (ROOT / "PROGRESS.md").write_text(progress_md(tasks, handoffs, commits, today), encoding="utf-8")
        print("PROGRESS.md updated")
    return 0


if __name__ == "__main__":
    sys.exit(main())
