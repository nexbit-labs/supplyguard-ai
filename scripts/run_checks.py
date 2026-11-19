#!/usr/bin/env python3
"""Run every test suite that exists in the repo. Used by auto-merge (CI) and locally.

    python scripts/run_checks.py            # run what exists, using already-installed deps
    python scripts/run_checks.py --install  # CI: install each suite's dependencies first

Suites are detected by files, so new parts of the project join automatically:
    <folder>/tests/         -> pytest for every top-level folder with tests (ai, data, mock-erp, ...);
                               deps from <folder>/requirements*.txt; mock-erp runs from its own folder
    backend/manage.py       -> ruff check + pytest in backend/ (deps: backend/requirements*.txt; DB from
                               DATABASE_URL); ruff is skipped with a notice if not installed (no --install)
    frontend/package.json   -> npm ci/install, then lint, typecheck, test, build (each only if the script exists)
Mirrors .github/workflows/ci.yml so the auto-merge gate matches CI.
Exit code is non-zero if any suite fails.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable
SERVICE_DIRS = {"mock-erp"}


def run(cmd: list[str], cwd: Path) -> bool:
    print(f"$ ({cwd.relative_to(ROOT).as_posix() or '.'}) {' '.join(cmd)}", flush=True)
    return subprocess.run(cmd, cwd=cwd).returncode == 0


def pip_install(*reqs: Path) -> bool:
    files = [r for r in reqs if r.exists()]
    if not files:
        return True
    cmd = [PY, "-m", "pip", "install", "-q"]
    for f in files:
        cmd += ["-r", str(f)]
    return run(cmd, ROOT)


def python_suite(reqs: Path, tests: Path, cwd: Path, install: bool) -> bool | None:
    if not tests.exists():
        return None
    if install and not pip_install(reqs / "requirements.txt", reqs / "requirements-dev.txt"):
        return False
    return run([PY, "-m", "pytest", "-q", str(tests.relative_to(cwd))], cwd)


def backend(install: bool) -> bool | None:
    folder = ROOT / "backend"
    if not (folder / "manage.py").exists():
        return None
    if install and not pip_install(folder / "requirements.txt", folder / "requirements-dev.txt"):
        return False
    ok = True
    if importlib.util.find_spec("ruff") is not None:
        ok = run([PY, "-m", "ruff", "check", "."], folder)
    elif shutil.which("ruff"):
        ok = run([shutil.which("ruff"), "check", "."], folder)
    else:
        print("ruff not installed - backend lint skipped")
    return run([PY, "-m", "pytest", "-q"], folder) and ok


def frontend(install: bool) -> bool | None:
    folder = ROOT / "frontend"
    pkg = folder / "package.json"
    if not pkg.exists():
        return None
    npm = shutil.which("npm")
    if not npm:
        print("npm not found - frontend checks skipped")
        return None
    scripts = json.loads(pkg.read_text(encoding="utf-8")).get("scripts", {})
    if install or not (folder / "node_modules").exists():
        if not run([npm, "ci" if (folder / "package-lock.json").exists() else "install"], folder):
            return False
    ok = True
    for script in ("lint", "typecheck", "test", "build"):
        if script in scripts:
            ok = run([npm, "run", script], folder) and ok
    return ok


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--install", action="store_true")
    args = ap.parse_args()
    os.environ.setdefault("CI", "true")
    results: dict[str, bool | None] = {}
    if args.install:
        run([PY, "-m", "pip", "install", "-q", "pytest"], ROOT)
    # Any top-level folder with a tests/ dir is a Python suite (ai, data, mock-erp, ...).
    # Services run from their own folder; libraries run from the repo root.
    for d in sorted(p for p in ROOT.iterdir() if p.is_dir() and (p / "tests").is_dir()):
        if d.name in {"backend", "frontend", "tests"}:  # handled below / e2e needs a running stack
            continue
        cwd = d if d.name in SERVICE_DIRS else ROOT
        results[d.name] = python_suite(d, d / "tests", cwd, args.install)
    results["backend"] = backend(args.install)
    results["frontend"] = frontend(args.install)
    print("\nCheck results:")
    for name, ok in results.items():
        print(f"  {name:<9} {'skipped (not present)' if ok is None else 'PASS' if ok else 'FAIL'}")
    return 1 if any(ok is False for ok in results.values()) else 0


if __name__ == "__main__":
    sys.exit(main())
