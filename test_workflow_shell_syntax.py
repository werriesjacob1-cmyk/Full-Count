#!/usr/bin/env python3
"""test_workflow_shell_syntax.py — every `run:` shell step in every
.github/workflows/*.yml file must be valid bash, checked the same way
GitHub Actions actually executes it (a YAML block scalar's own dedent
rule, not the raw indented YAML text) -- and every Python heredoc such a
step feeds to `python3` must actually be valid, correctly-indented Python
once that same dedent is applied, not just syntactically parseable bash.

Exists because of two real production defects found on the same file in
the same day. .github/workflows/mlb-grading-catchup.yml's first live run
(2026-09-22, run 35773890129) failed in under a second with "here-document
... delimited by end-of-file" -- a `<<'PYEOF'` heredoc whose closing
PYEOF line had 2 stray leading spaces (it was indented to visually match
the Python code's own nesting inside a shell `for` loop, rather than the
run-block's true base indentation). `bash -e {0}` requires an unquoted-or-
quoted heredoc's terminator to match with zero leading/trailing
characters. Section 1 below catches that class: it reproduces the exact
runtime script (via PyYAML, which resolves `|` block scalars the same way
GitHub Actions receives them) and syntax-checks it with `bash -n`.

Fixing that terminator bug then exposed a SECOND, previously-hidden
defect on the very next real run (2026-09-22, run 35790968232): with the
terminator now correctly recognized, bash passed the heredoc's BODY
verbatim to `python3`'s stdin -- and that body still carried 2 residual
leading spaces on every line (a `<<'WORD'` heredoc, unlike `<<-'WORD'`,
strips no leading whitespace from body lines at all). Python does not
allow a top-level module's first statement to be indented, so it failed
immediately with `IndentationError: unexpected indent`. `bash -n` cannot
catch this -- to bash, a heredoc body is just an opaque string; it has no
idea the string will later be fed to `python3` and must itself be valid
Python at column 0. Section 3 below closes exactly this gap: it extracts
every `python3 ... <<'DELIM' ... DELIM` heredoc body (post-YAML-dedent,
identical to section 1's method) and `compile()`s it as Python, so a
residual-indentation heredoc fails fast in CI instead of on a live run.

    python3 test_workflow_shell_syntax.py
"""
from __future__ import annotations

import glob
import re
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


def iter_python_heredoc_bodies(script):
    """Yield (delimiter, body) for every `python3 ... <<[-]'DELIM' ... DELIM`
    heredoc in an already-YAML-dedented run script -- the exact text bash
    passes to python3's stdin, whitespace included."""
    pattern = re.compile(
        r"[^\n]*python3[^\n]*<<-?'(?P<delim>\w+)'\n(?P<body>.*?)\n[ \t]*(?P=delim)\b",
        re.S,
    )
    for m in pattern.finditer(script):
        yield m.group("delim"), m.group("body")


head("3. every python3 heredoc embedded in a run: step compiles as valid Python "
     "at the exact indentation bash actually delivers it")
checked_heredocs = 0
for path in workflow_files:
    for step_label, script in iter_run_steps(path):
        for delim, body in iter_python_heredoc_bodies(script):
            checked_heredocs += 1
            try:
                compile(body, f"<{path}::{step_label}::{delim}>", "exec")
                ok, detail = True, ""
            except SyntaxError as exc:
                ok, detail = False, f"{type(exc).__name__}: {exc}"
            check(ok, f"{path} :: {step_label} :: <<{delim} heredoc body is valid Python", detail)
check(checked_heredocs > 0,
      f"at least one python3 heredoc was actually checked (found {checked_heredocs})")

head("4. regression fixture: the exact defect that broke the second live run stays caught")
# The real bug: a `<<'PYEOF'` heredoc's BODY (not its terminator) carried 2
# residual leading spaces on every line after YAML's own dedent -- `<<'WORD'`
# (unlike `<<-'WORD'`) strips no whitespace from body lines, so Python saw an
# indented first statement and raised IndentationError.
residually_indented_script = (
    "python3 - <<'PYEOF'\n"
    "  import json\n"
    "  print(json.dumps({}))\n"
    "PYEOF\n"
)
found = list(iter_python_heredoc_bodies(residually_indented_script))
check(len(found) == 1, "the regression fixture's heredoc is itself detected")
if found:
    _, bad_body = found[0]
    try:
        compile(bad_body, "<fixture>", "exec")
        caught = False
    except SyntaxError:
        caught = True
    check(caught,
          "a heredoc body with residual leading whitespace on every line is "
          "correctly detected as broken (IndentationError), not silently passed")

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
