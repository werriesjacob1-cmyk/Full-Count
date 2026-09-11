#!/usr/bin/env python3
"""No root-level MLB module may import from nfl/. Enforcement 1.

THE PERMITTED DIRECTION, AND ONLY IT:

    nfl/  ->  verified sport-neutral infrastructure

FORBIDDEN, both ways round:

    root MLB implementation  ->  nfl/          (NFL becomes an implicit
                                                dependency of MLB production)
    MLB production           ->  nfl/          (a bug in NFL can break the
                                                board, or MLB science quietly
                                                starts depending on football)

WHY THIS IS WORTH A TEST RATHER THAN A CONVENTION. The direction is invisible
at review time. One `import nfl.something` inside generate_picks.py for a
plausible reason -- sharing a helper, reusing a constant -- and MLB production
now fails when NFL fails, and NFL can no longer be changed freely. The coupling
is trivially easy to add and expensive to remove once anything depends on it.

This scans SOURCE TEXT rather than importing modules, on purpose: importing
generate_picks.py to inspect it would execute it.
"""
import ast
import os
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Directories whose Python is MLB-side or shared infrastructure. nfl/ is
# excluded because nfl/ importing nfl/ is the whole point.
MLB_SEARCH_ROOTS = (".", "backtest", "dashboard", "infra", "ops", "cloudflare-watchdog")


def _mlb_python_files():
    for root in MLB_SEARCH_ROOTS:
        base = os.path.join(REPO, root)
        if not os.path.isdir(base):
            continue
        if root == ".":
            names = [n for n in os.listdir(base)
                     if n.endswith(".py") and os.path.isfile(os.path.join(base, n))]
            for name in names:
                yield os.path.join(base, name)
            continue
        for dirpath, dirs, files in os.walk(base):
            dirs[:] = [d for d in dirs if d not in ("__pycache__", ".git")]
            for name in files:
                if name.endswith(".py"):
                    yield os.path.join(dirpath, name)


def _imports_nfl(path):
    """Return offending (lineno, statement) pairs for imports reaching nfl/."""
    with open(path, encoding="utf-8") as handle:
        source = handle.read()
    try:
        tree = ast.parse(source)
    except SyntaxError:
        # A file that does not parse cannot be certified clean. Fail closed by
        # falling back to a textual scan rather than silently passing it.
        return [
            (n, line.strip()) for n, line in enumerate(source.splitlines(), 1)
            if "import nfl" in line or "from nfl" in line
        ]
    offenders = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "nfl" or alias.name.startswith("nfl."):
                    offenders.append((node.lineno, f"import {alias.name}"))
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module == "nfl" or module.startswith("nfl."):
                offenders.append((node.lineno, f"from {module} import ..."))
    return offenders


class ImportDirection(unittest.TestCase):
    def test_no_mlb_module_imports_from_nfl(self):
        violations = []
        for path in _mlb_python_files():
            rel = os.path.relpath(path, REPO)
            if rel.startswith("nfl" + os.sep) or rel.startswith("nfl/"):
                continue
            for lineno, statement in _imports_nfl(path):
                violations.append(f"{rel}:{lineno}  {statement}")
        self.assertEqual(
            violations, [],
            "MLB-side modules import from nfl/, which inverts the only "
            "permitted dependency direction (nfl -> sport-neutral "
            "infrastructure, never MLB -> nfl):\n  " + "\n  ".join(violations),
        )

    def test_the_scanner_actually_reaches_the_big_mlb_modules(self):
        """A scanner that silently covers nothing would pass forever."""
        scanned = {os.path.relpath(p, REPO) for p in _mlb_python_files()}
        for expected in ("generate_picks.py", "ledger_integrity.py",
                         "dashboard/live_state.py", "backtest/signals.py"):
            self.assertIn(expected, scanned)
        self.assertGreater(len(scanned), 50, f"only {len(scanned)} files scanned")

    def test_nfl_does_not_import_mlb_production_modules(self):
        """The other half of the boundary: NFL must not depend on MLB either.

        Section 7 permits reuse of verified SPORT-NEUTRAL infrastructure by
        import. It does not permit NFL to reach into baseball-specific
        production code -- which is why the FanDuel application key is
        duplicated in nfl/archive/sources/fanduel_nfl.py rather than imported
        from odds_fanduel.py.
        """
        forbidden = {
            "generate_picks", "odds_fanduel", "mlb_daily", "mlb_sources",
            "prop_probability", "recommendation", "grade_results",
            "parlay_builder", "render_board", "prop_snapshot", "final_card",
        }
        violations = []
        for dirpath, dirs, files in os.walk(os.path.join(REPO, "nfl")):
            dirs[:] = [d for d in dirs if d != "__pycache__"]
            for name in files:
                if not name.endswith(".py"):
                    continue
                path = os.path.join(dirpath, name)
                rel = os.path.relpath(path, REPO)
                with open(path, encoding="utf-8") as handle:
                    try:
                        tree = ast.parse(handle.read())
                    except SyntaxError:
                        continue
                for node in ast.walk(tree):
                    names = []
                    if isinstance(node, ast.Import):
                        names = [a.name for a in node.names]
                    elif isinstance(node, ast.ImportFrom):
                        names = [node.module or ""]
                    for module in names:
                        if module.split(".")[0] in forbidden:
                            violations.append(f"{rel}:{node.lineno}  {module}")
        self.assertEqual(
            violations, [],
            "nfl/ imports MLB production modules:\n  " + "\n  ".join(violations),
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
