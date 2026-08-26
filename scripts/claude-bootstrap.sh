#!/usr/bin/env bash
# claude-bootstrap.sh -- verify/install the FULL COUNT Claude tooling
# foundation on a fresh container/clone. Portable, idempotent, read-mostly.
#
# WHAT THIS DOES:
#   - Verifies the project Python venv exists and matches requirements-dev.txt
#     (installs/upgrades only those pinned packages -- nothing else).
#   - Verifies Serena is installed (the only configured MCP; see .mcp.json)
#     and reports its version. Does NOT install Serena itself -- that's a
#     one-time `uv tool install` a human runs, not something a script should
#     silently do on every container boot.
#   - Reports presence/absence/version of every OTHER tool from the approved
#     evaluation list (jq, a type checker, pip-audit, Bandit/Semgrep,
#     coverage, pytest-timeout, pytest-xdist, pre-commit, mutmut, Playwright,
#     Lighthouse, node/npm) WITHOUT installing any of them. Several of these
#     are deliberately "evaluate/install on demand" per project decision --
#     this script's job is to make their status visible, not to install them
#     preemptively.
#
# WHAT THIS DELIBERATELY DOES NOT DO:
#   - Does not activate any experimental connector (n8n, Context7, or
#     anything else not already in .mcp.json).
#   - Does not start any background Claude/LLM process. Nothing here is a
#     daemon, a scheduler, or a runtime dependency of production -- Full
#     Count's live pipeline must keep working with this script never having
#     been run again.
#   - Does not write or read any secret. Every credential this script checks
#     for is checked via presence-of-env-var only; no value is ever printed,
#     logged, or written to a file.
#   - Does not touch git state, hooks, or any worktree beyond the one it's
#     run from.
#
# USAGE:
#   bash scripts/claude-bootstrap.sh
#
# Exit code is always 0 (this is a report, not a gate) unless the venv step
# itself fails, in which case it's the pip failure's exit code.
set -uo pipefail

VENV=/tmp/mlbvenv
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
cd "$REPO_ROOT" || exit 1

bold() { printf '\033[1m%s\033[0m\n' "$1"; }
ok()   { printf '  [ok]      %s\n' "$1"; }
miss() { printf '  [missing] %s\n' "$1"; }
info() { printf '  [info]    %s\n' "$1"; }

have() { command -v "$1" >/dev/null 2>&1; }

bold "== FULL COUNT Claude tooling bootstrap =="
info "repo root: $REPO_ROOT"

# --- 1. Project Python venv + pinned dev deps ------------------------------
bold "-- Python venv (project) --"
if [ ! -x "$VENV/bin/python3" ]; then
  info "creating venv at $VENV"
  python3 -m venv "$VENV" || { echo "FATAL: could not create venv"; exit 1; }
fi
"$VENV/bin/python3" --version | sed 's/^/  /'

if [ -f "$REPO_ROOT/requirements-dev.txt" ]; then
  "$VENV/bin/python3" -m pip install -q -r "$REPO_ROOT/requirements-dev.txt" \
    && ok "requirements-dev.txt installed/verified" \
    || { echo "FATAL: pip install -r requirements-dev.txt failed"; exit 1; }
  "$VENV/bin/python3" -m pip list 2>/dev/null | grep -iE '^(hypothesis|ruff|pytest) ' | sed 's/^/  /'
else
  miss "requirements-dev.txt not found at repo root -- nothing to install"
fi

# --- 2. Serena (the only configured MCP) ------------------------------------
bold "-- Serena (configured MCP, .mcp.json) --"
if have serena; then
  ver="$(serena --version 2>&1 | head -1)"
  ok "serena installed: $ver"
else
  miss "serena not installed -- .mcp.json expects it on PATH."
  info "install (one-time, human-run, not automated here):"
  info "  uv tool install --from git+https://github.com/oraios/serena serena-agent"
fi

# --- 3. Report-only tools (approved evaluation list, section J/I/E/F/K) ----
bold "-- Report-only: evaluation-list tools (NOT auto-installed) --"
check_report() {
  local name="$1" cmd="$2" versionflag="${3:---version}"
  if have "$cmd"; then
    ok "$name: $("$cmd" $versionflag 2>&1 | head -1)"
  else
    miss "$name (command '$cmd' not on PATH)"
  fi
}
check_report "jq" jq
check_report "ruff" ruff
check_report "mypy" mypy
check_report "pyright" pyright
check_report "black" black
check_report "flake8" flake8
check_report "pytest" pytest
check_report "pre-commit" pre-commit
check_report "pip-audit" pip-audit
check_report "bandit" bandit
check_report "semgrep" semgrep
check_report "mutmut" mutmut
check_report "node" node
check_report "npm" npm
if have npx; then
  pw_ver="$(npx --no-install playwright --version 2>&1 | head -1)"
  if [ $? -eq 0 ]; then ok "playwright (npx): $pw_ver"; else miss "playwright (npx playwright not resolvable without install)"; fi
else
  miss "npx not found"
fi
"$VENV/bin/python3" -m pip show pytest-timeout >/dev/null 2>&1 \
  && ok "pytest-timeout: installed in project venv" \
  || miss "pytest-timeout (not in project venv)"
"$VENV/bin/python3" -m pip show pytest-xdist >/dev/null 2>&1 \
  && ok "pytest-xdist: installed in project venv" \
  || miss "pytest-xdist (not in project venv)"
"$VENV/bin/python3" -m pip show coverage >/dev/null 2>&1 \
  && ok "coverage: installed in project venv" \
  || miss "coverage (not in project venv)"

# --- 4. Optional credentials -- presence only, never printed --------------
bold "-- Optional credentials (presence check only; no value ever read/printed) --"
check_cred() {
  local name="$1" var="$2"
  if [ -n "${!var:-}" ]; then ok "$name: $var is set"; else miss "$name: $var not set"; fi
}
check_cred "n8n API" N8N_API_KEY
check_cred "Context7" CONTEXT7_API_KEY
check_cred "GitHub token (if a script needs one outside the MCP proxy)" GITHUB_TOKEN

bold "== bootstrap complete (report only -- nothing above was force-installed) =="
