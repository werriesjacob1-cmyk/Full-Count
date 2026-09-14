#!/usr/bin/env bash
#
# SuperClaude acceptance test.
#
# Answers one question: could a FRESH Claude Code session, entering this
# repository cold, do the thirteen things the SuperClaude definition requires?
#
#   bash .claude/tests/test_superclaude_acceptance.sh
#
# Returns PASS / WARN / FAIL per check and an overall verdict.
#
# WHAT THIS TEST CANNOT DO, stated up front so nobody reads more into it:
# it validates CONFIGURATION, not runtime enforcement. A permission deny rule
# is checked for presence and correctness, not by proving the running session
# refused a read -- because a session only loads project settings whose file
# sits at ITS project root, and this suite may well be run from elsewhere.
# Claiming otherwise would be exactly the false-success reporting fc-break-it
# exists to hunt. Runtime enforcement checks are marked INFO, never PASS.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT" || exit 1
C="$ROOT/.claude"

P=0; W=0; F=0
pass() { P=$((P+1)); printf '  PASS  %s\n' "$1"; }
warn() { W=$((W+1)); printf '  WARN  %s\n' "$1"; }
fail() { F=$((F+1)); printf '  FAIL  %s\n' "$1"; }
info() { printf '  INFO  %s\n' "$1"; }
chk()  { if [ "$2" = "$3" ]; then pass "$1"; else fail "$1 (want '$3', got '$2')"; fi; }

echo "SuperClaude acceptance — $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "root: $ROOT"
echo

# ─────────────────────────────────────────────────────────────── 1. BOOT ──
echo "== 1. Boot: can a cold session learn the project? =="
[ -f "$ROOT/CLAUDE.md" ] && pass "CLAUDE.md exists" || fail "CLAUDE.md exists"
grep -q '@AGENTS.md' "$ROOT/CLAUDE.md" 2>/dev/null \
  && pass "CLAUDE.md imports AGENTS.md" || fail "CLAUDE.md imports AGENTS.md"
[ -f "$ROOT/AGENTS.md" ] && pass "AGENTS.md exists (import target resolves)" || fail "AGENTS.md exists"
grep -qi 'realized' "$ROOT/CLAUDE.md" && pass "objective (realized hit rate) stated" || fail "objective stated"
grep -qi 'evidence regime\|three evidence' "$ROOT/CLAUDE.md" && pass "evidence regimes stated" || fail "evidence regimes stated"
grep -qi 'freeze' "$ROOT/CLAUDE.md" && pass "production-science freeze stated" || fail "freeze stated"
for r in research live frontend tooling; do
  [ -f "$C/rules/$r.md" ] && pass "rule $r.md exists" || fail "rule $r.md exists"
  head -5 "$C/rules/$r.md" 2>/dev/null | grep -q 'globs:' \
    && pass "rule $r.md is path-scoped" || warn "rule $r.md has no globs: frontmatter"
done
[ -d "$C/skills/fc-context-keeper" ] && pass "context keeper present" || fail "context keeper present"
[ -f "$C/CAPABILITY_MATRIX.md" ] && pass "capability matrix present" || fail "capability matrix present"

# ──────────────────────────────────────────────────────── 2. SETTINGS ──
echo
echo "== 2. Settings: valid, hardened, and honest about enforcement =="
if python3 -c "import json;json.load(open('$C/settings.json'))" 2>/dev/null; then
  pass "settings.json parses"
  chk "bypass permissions disabled" \
    "$(python3 -c "import json;print(json.load(open('$C/settings.json'))['permissions'].get('disableBypassPermissionsMode'))")" "disable"
  D=$(python3 -c "import json;print('\n'.join(json.load(open('$C/settings.json'))['permissions'].get('deny',[])))")
  A=$(python3 -c "import json;print('\n'.join(json.load(open('$C/settings.json'))['permissions'].get('ask',[])))")
  echo "$D" | grep -q '\.env'          && pass "deny: .env"            || fail "deny: .env"
  echo "$D" | grep -q '\.pem'          && pass "deny: private keys"    || fail "deny: private keys"
  echo "$D" | grep -qi 'credential'    && pass "deny: credentials"     || fail "deny: credentials"
  echo "$D" | grep -qi 'token'         && pass "deny: tokens"          || fail "deny: tokens"
  echo "$D" | grep -q 'ssh'            && pass "deny: ~/.ssh"          || fail "deny: ~/.ssh"
  echo "$D" | grep -q 'push --force'   && pass "deny: force push"      || fail "deny: force push"
  echo "$A" | grep -q 'git merge'      && pass "ask: merge"            || fail "ask: merge"
  echo "$A" | grep -q 'git rebase'     && pass "ask: rebase"           || fail "ask: rebase"
  echo "$A" | grep -q 'reset --hard'   && pass "ask: reset --hard"     || fail "ask: reset --hard"
  for f in generate_picks.py recommendation.py prop_probability.py grade_results.py \
           dashboard/settlement_rules.py dashboard/refresh_grades.py backtest/engine.py; do
    echo "$A" | grep -q "$f" && pass "ask-gated: $f" || fail "ask-gated: $f"
  done
  # Deny rules must not block real repo files.
  BLOCKED=$(git ls-files 2>/dev/null | grep -icE 'secret|token|credential|\.key$|\.pem$' || true)
  chk "deny rules block no tracked file" "$BLOCKED" "0"
else
  fail "settings.json parses"
fi

echo
echo "  -- runtime enforcement (INFO only; cannot be PASSed from inside a test) --"
SESSION_ROOT="${CLAUDE_PROJECT_DIR:-$PWD}"
if [ -f "$SESSION_ROOT/.claude/settings.json" ]; then
  info "a settings.json exists at the presumed session root ($SESSION_ROOT)"
else
  info "NO settings.json at $SESSION_ROOT -- project settings load from the SESSION's"
  info "project root, so these rules are inert for a session rooted elsewhere."
  info "They become live after this branch reaches the checked-out root, in a FRESH session."
fi

# ─────────────────────────────────────────────────────────── 3. HOOKS ──
echo
echo "== 3. Hooks: declared targets exist and are executable =="
python3 - "$C" <<'PY'
import json, os, re, sys
C = sys.argv[1]
d = json.load(open(os.path.join(C, "settings.json")))
bad = 0
for ev, groups in d.get("hooks", {}).items():
    for g in groups:
        for h in g.get("hooks", []):
            for t in set(re.findall(r'/\.claude/([A-Za-z0-9_.-]+\.sh)', h["command"])):
                p = os.path.join(C, t)
                ok = os.path.exists(p) and os.access(p, os.X_OK)
                print(f"  {'PASS' if ok else 'FAIL'}  hook {ev} -> {t} {'exists+executable' if ok else 'MISSING/not executable'}")
                bad += 0 if ok else 1
print(f"  {'PASS' if 'Stop' not in d.get('hooks',{}) else 'FAIL'}  no project Stop hook (the harness owns one; a second would re-fire on every blocked stop)")
sys.exit(1 if bad else 0)
PY
[ $? -eq 0 ] && P=$((P+2)) || F=$((F+1))

# ────────────────────────────────────────────────────────── 4. AGENTS ──
echo
echo "== 4. Agents: least privilege, unique, no phantom tools =="
python3 - "$C" <<'PY'
import glob, os, re, sys
C = sys.argv[1]
# CIRCULARITY WARNING, and it is not theoretical.
#
# This set is HARDCODED HERE. It is not read from the live runtime, because a
# shell test has no tool access. So "no phantom tools" below means only "no
# agent declares a tool absent from THIS LIST" -- it can never catch a tool
# that the test author also believed in. Do not read a PASS as runtime proof.
#
# What makes this urgent rather than pedantic: between 2026-08-27 and
# 2026-09-01 the Google Drive MCP server's tool prefix changed from an opaque
# UUID (mcp__61c07106-3393-...__*) to mcp__Google_Drive__*, and Cloudflare MCP
# went available -> absent -> available-as-two-servers. A RENAMED tool fails as
# "unknown tool", not as "server down". Nothing here referenced the old prefix,
# so nothing broke -- this time.
#
# Only MCP-prefixed (mcp__*) declarations are checked against the live runtime
# by a human or an agent with tool access. Verify with mcp__github__get_me and
# a ToolSearch for anything else an agent file promises.
RUNTIME = {"Read","Grep","Glob","Bash","Write","Edit","WebSearch","WebFetch",
           "TaskCreate","TaskUpdate","TaskGet","TaskList","Agent","ToolSearch",
           "NotebookEdit","Monitor"}
READONLY = {"fc-methodology-red-team","fc-release-auditor","fc-canonical-certifier",
            "fc-prospective-ledger-auditor","fc-intelligence-scout"}
names, bad = [], 0
for p in sorted(glob.glob(os.path.join(C, "agents", "*.md"))):
    t = open(p).read()
    m = re.match(r"^---\n(.*?)\n---\n", t, re.S)
    if not m:
        print(f"  FAIL  {p}: no frontmatter"); bad += 1; continue
    fm = {}
    for line in m.group(1).split("\n"):
        if ":" in line and not line.startswith((" ", "\t", "-")):
            k, v = line.split(":", 1); fm[k.strip()] = v.strip()
    n = fm.get("name", ""); names.append(n)
    stem = os.path.basename(p)[:-3]
    if n != stem:
        print(f"  FAIL  {stem}: name '{n}' != filename"); bad += 1
    if not fm.get("description"):
        print(f"  FAIL  {stem}: no description"); bad += 1
    tools = [x.strip() for x in fm.get("tools", "").split(",") if x.strip()]
    phantom = [x for x in tools if not x.startswith("mcp__") and x not in RUNTIME]
    if phantom:
        print(f"  FAIL  {stem}: declares tools absent from this test's hardcoded list: {phantom}"); bad += 1
    if n in READONLY:
        if "Write" in tools or "Edit" in tools:
            print(f"  FAIL  {stem}: READ-ONLY reviewer grants Write/Edit"); bad += 1
        else:
            print(f"  PASS  {stem}: read-only (no Write/Edit)")
    else:
        print(f"  PASS  {stem}: write-capable, {len(tools)} tools")
    if "Agent" in tools:
        print(f"  FAIL  {stem}: declares Agent -- no nested agent trees"); bad += 1
if len(names) != len(set(names)):
    print("  FAIL  duplicate agent names"); bad += 1
else:
    print(f"  PASS  {len(names)} agents, names unique")
sys.exit(1 if bad else 0)
PY
[ $? -eq 0 ] && P=$((P+1)) || F=$((F+1))

# ────────────────────────────────────────────────────────── 5. SKILLS ──
echo
echo "== 5. Skills: frontmatter valid, fork targets real, freeze preserved =="
python3 - "$C" <<'PY'
import glob, os, re, sys
C = sys.argv[1]
agents = {os.path.basename(p)[:-3] for p in glob.glob(os.path.join(C, "agents", "*.md"))}
EXPECT = {"fc-backfill","fc-break-it","fc-canonical-certify","fc-context-keeper",
          "fc-experiment","fc-live-incident","fc-prospective-audit",
          "fc-release-audit","fc-selector-lab","fc-ux-audit"}
found, bad = set(), 0
for p in sorted(glob.glob(os.path.join(C, "skills", "*", "SKILL.md"))):
    d = os.path.basename(os.path.dirname(p)); found.add(d)
    t = open(p).read()
    m = re.match(r"^---\n(.*?)\n---\n", t, re.S)
    if not m:
        print(f"  FAIL  {d}: no frontmatter"); bad += 1; continue
    fm = {}
    for line in m.group(1).split("\n"):
        if ":" in line and not line.startswith((" ", "\t", "-")):
            k, v = line.split(":", 1); fm[k.strip()] = v.strip()
    if fm.get("name") != d:
        print(f"  FAIL  {d}: name mismatch"); bad += 1
    if not fm.get("description"):
        print(f"  FAIL  {d}: no description"); bad += 1
    ag, ctx = fm.get("agent", ""), fm.get("context", "")
    if ctx and ctx != "fork":
        print(f"  FAIL  {d}: unknown context '{ctx}'"); bad += 1
    if ag and ag not in agents:
        print(f"  FAIL  {d}: forks to nonexistent agent '{ag}'"); bad += 1
    tools = [x.strip() for x in fm.get("allowed-tools", "").split(",") if x.strip()]
    if ag in {"fc-methodology-red-team","fc-release-auditor","fc-canonical-certifier",
              "fc-prospective-ledger-auditor"} and ("Write" in tools or "Edit" in tools):
        print(f"  FAIL  {d}: grants Write/Edit while forking to a read-only reviewer"); bad += 1
    # merge authority must never be implied
    if re.search(r"\b(merge it|go ahead and merge|approve the merge)\b", t, re.I):
        print(f"  FAIL  {d}: implies merge authority"); bad += 1
    print(f"  PASS  {d}{' (fork -> ' + ag + ')' if ctx else ''}")
missing = EXPECT - found
extra = found - EXPECT
if missing:
    print(f"  FAIL  missing skills: {sorted(missing)}"); bad += 1
else:
    print(f"  PASS  all {len(EXPECT)} expected skills present")
if extra:
    print(f"  WARN  unexpected skills: {sorted(extra)}")
sys.exit(1 if bad else 0)
PY
[ $? -eq 0 ] && P=$((P+1)) || F=$((F+1))

# ───────────────────────────────────────────────────────── 6. PAIRING ──
echo
echo "== 6. Pairing: every agent and skill appears in the capability matrix =="
MX="$C/CAPABILITY_MATRIX.md"
if [ -f "$MX" ]; then
  miss=0
  for a in "$C"/agents/*.md; do
    n=$(basename "$a" .md)
    grep -q "$n" "$MX" || { fail "agent $n absent from matrix"; miss=1; }
  done
  for s in "$C"/skills/*/; do
    n=$(basename "$s")
    grep -q "$n" "$MX" || { fail "skill $n absent from matrix"; miss=1; }
  done
  [ $miss -eq 0 ] && pass "no orphan agents or skills"
  grep -q 'project-gridiron' "$MX" && pass "matrix records the MCP access name" || fail "matrix records MCP access name"
  grep -q 'Full-Count' "$MX" && pass "matrix records the provenance name" || fail "matrix records provenance name"
  grep -qi 'fallback' "$MX" && pass "matrix defines fallbacks" || fail "matrix defines fallbacks"
else
  fail "capability matrix present"
fi

# ────────────────────────────────────────────────────── 7. DURABILITY ──
echo
echo "== 7. Durability =="
if [ -x "$C/tests/test_worktree_autosave.sh" ]; then
  if bash "$C/tests/test_worktree_autosave.sh" >/tmp/fc_autosave_out.txt 2>&1; then
    pass "autosave suite ($(grep -c '^  ok' /tmp/fc_autosave_out.txt) checks)"
  else
    fail "autosave suite -- see /tmp/fc_autosave_out.txt"
  fi
else
  fail "autosave suite present"
fi
if [ -f "$ROOT/backtest/canonical_run.py" ]; then
  grep -q 'enabled=bool(durability)' "$ROOT/backtest/canonical_run.py" 2>/dev/null \
    && pass "canonical durability is opt-in (tests cannot push scientific state)" \
    || warn "backtest/canonical_run.py present but the opt-in marker did not match"
else
  # Do not report this as "backtest/ is not on this branch" -- backtest/ IS
  # here. The runner itself is not, and that is the finding worth surfacing.
  # VERIFIED 2026-08-29: backtest/canonical_run.py and canonical_durability.py
  # exist at NO branch tip in this repository -- not main, not the recovery
  # branch, not the prereg branch. They exist only at the pinned canonical SHA
  # fc589447ec157bff9a96071edc3ceb6c7dc734eb, reachable by `git fetch origin
  # <sha>`. Canonical recovery therefore depends on that commit staying
  # fetchable; nothing on a branch keeps it alive.
  warn "canonical runner absent from this checkout: backtest/canonical_run.py lives only at the pinned SHA, not at any branch tip"
fi

# ───────────────────────────────────────────────────────── 8. BROWSER ──
echo
echo "== 8. Browser =="
if [ -x /opt/pw-browsers/chromium/chrome ] || [ -x /opt/pw-browsers/chromium ]; then
  V=$(/opt/pw-browsers/chromium/chrome --version 2>/dev/null || /opt/pw-browsers/chromium --version 2>/dev/null)
  pass "Chromium available (${V:-version unknown})"
else
  if grep -rqi 'chromium\|playwright' "$C/skills/fc-ux-audit/SKILL.md" 2>/dev/null; then
    fail "fc-ux-audit promises Chromium but no binary found"
  else
    warn "no Chromium (no skill promises it)"
  fi
fi
[ -f "$ROOT/test_browser_e2e.py" ] && pass "deterministic browser suite discoverable" \
  || warn "test_browser_e2e.py not on this branch"

# ────────────────────────────────────────────────────────── 9. GITHUB ──
echo
echo "== 9. GitHub read path =="
git ls-remote origin HEAD >/dev/null 2>&1 && pass "git remote readable" || fail "git remote readable"
command -v gh >/dev/null 2>&1 && pass "gh CLI present" \
  || info "gh CLI absent -- documented fallback is GitHub MCP + git (see matrix)"
grep -q 'mcp__github__' "$C/agents/fc-release-auditor.md" 2>/dev/null \
  && pass "release auditor declares GitHub MCP" || fail "release auditor declares GitHub MCP"
info "live MCP calls are not made here: a shell test has no tool access."
info "Verify with mcp__github__get_me + list_branches(owner=werriesjacob1-cmyk, repo=project-gridiron)."

# ─────────────────────────────────────────────────────── 10. NAVIGATION ──

section "11. Context keeper does not dirty the tree"
# Regression guard. Three files in .claude/ assert .claude/context/ is
# gitignored; until 2026-08-29 none of them was true and no test checked.
# Consequences were concrete: an untracked file wedges session end (see the
# comment at .claude/worktree-autosave.sh:44-46) and gets swept into an
# autosave snapshot and PUSHED to origin, carrying worktree paths, PIDs, boot
# ids and run identifiers. Found by independent audit, not by this suite.
if git -C "$ROOT" check-ignore -q .claude/context/probe.md 2>/dev/null; then
  pass ".claude/context/ is gitignored (keeper cannot dirty the tree or reach origin)"
else
  fail ".claude/context/ is NOT gitignored -- keeper output would be committed/pushed"
fi

echo
echo "== 10. Navigation =="
if grep -q 'Serena' "$MX" 2>/dev/null && grep -q 'REJECTED' "$MX" 2>/dev/null; then
  pass "Serena explicitly rejected with reasons (no unused dependency)"
else
  warn "Serena status not recorded in the matrix"
fi
command -v pyright >/dev/null 2>&1 && pass "pyright available for symbol resolution" || warn "pyright absent"

# ───────────────────────────────────────────────────────────── VERDICT ──
echo
echo "=============================================="
echo "PASS: $P   WARN: $W   FAIL: $F"
if [ "$F" -gt 0 ]; then
  echo "VERDICT: FAIL"; exit 1
elif [ "$W" -gt 0 ]; then
  echo "VERDICT: PASS WITH WARNINGS"; exit 0
else
  echo "VERDICT: PASS"; exit 0
fi
