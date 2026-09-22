#!/usr/bin/env python3
"""test_workflow_shell_syntax.py — every `run:` shell step in every
.github/workflows/*.yml file must be valid bash, checked the same way
GitHub Actions actually executes it (a YAML block scalar's own dedent
rule, not the raw indented YAML text).

Exists because of a real production defect: .github/workflows/
mlb-grading-catchup.yml's first live run (2026-09-22, run 35773890129)
failed in under a second with "here-document ... delimited by end-of-file"
-- a `<<'PYEOF'` heredoc whose closing PYEOF line had 2 stray leading
spaces (it was indented to visually match the Python code's own nesting
inside a shell `for` loop, rather than the run-block's true base
indentation). `bash -e {0}` requires an unquoted-or-quoted heredoc's
terminator to match with zero leading/trailing characters. Neither YAML
syntax validation nor "the workflow parses" nor exact-head CI running
unrelated tests caught this -- it only surfaced on the first real
execution. This test reproduces the exact runtime script (via PyYAML,
which resolves `|` block scalars the same way GitHub Actions receives
them) and syntax-checks it with `bash -n`, so this exact class of bug
fails fast in CI instead of on a live production run.

    python3 test_workflow_shell_syntax.py
"""
from __future__ import annotations

import glob
import subprocess
import sys

import yaml

VERBOSE = "-v" in sys.argv or "--verbose" in sys.argv
_results = []


def check(cond, msg, detail=""):
    _results.append((bool(cond), msg, detail))
    if VERBOSE or not cond:
        tag = "PASS" if cond else "FAIL"
        line = "  [%s] %s" % (tag, msg)
        if detail and (VERBOSE or not cond):
            line += "\n         " + detail
        print(line)


def head(t):
    if VERBOSE:
        print()
    print("-- %s" % t)


def iter_run_steps(workflow_path):
    with open(workflow_path, encoding="utf-8") as f:
        doc = yaml.safe_load(f)
    jobs = (doc or {}).get("jobs") or {}
    for job_name, job in jobs.items():
        for i, step in enumerate(job.get("steps") or []):
            script = step.get("run")
            if script is None:
                continue
            label = step.get("name") or step.get("id") or f"step[{i}]"
            yield f"{job_name}::{label}", script


head("1. every run: shell step across every workflow file is syntactically valid bash")
workflow_files = sorted(glob.glob(".github/workflows/*.yml") + glob.glob(".github/workflows/*.yaml"))
checked = 0
for path in workflow_files:
    for step_label, script in iter_run_steps(path):
        checked += 1
        result = subprocess.run(
            ["bash", "-n"],
            input=script,
            capture_output=True,
            text=True,
        )
        check(result.returncode == 0,
              f"{path} :: {step_label} is valid bash",
              result.stderr.strip())
check(checked > 0, f"at least one run: step was actually checked (found {checked})")

head("2. regression fixture: the exact defect that broke the first live run stays caught")
broken_script = (
    "for attempt in 1 2 3; do\n"
    "  python3 - <<'PYEOF'\n"
    "  print('hi')\n"
    "  PYEOF\n"
    "done\n"
)
result = subprocess.run(["bash", "-n"], input=broken_script, capture_output=True, text=True)
check(result.returncode != 0,
      "an indented heredoc terminator inside a shell for-loop is correctly detected as broken",
      result.stderr.strip())

n_pass = sum(1 for ok, _, _ in _results if ok)
n_total = len(_results)
print("\n" + "=" * 78)
print(f"RESULT: {n_pass}/{n_total} checks passed")
if n_pass < n_total:
    print()
    for ok, msg, detail in _results:
        if not ok:
            print(f"  FAILED: {msg}")
            if detail:
                print(f"          {detail}")
print("=" * 78)
sys.exit(0 if n_pass == n_total else 1)
