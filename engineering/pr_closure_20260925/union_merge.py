import subprocess, sys
def g(*a, check=True):
    r = subprocess.run(["git", *a], capture_output=True, text=True)
    if check and r.returncode: raise SystemExit(f"git {' '.join(a)} failed: {r.stderr}")
    return r
H = "engineering/ENGINEERING_HANDOFF.md"
br = sys.argv[1]
g("checkout", "-q", "-B", br, "origin/" + br)
mb = g("merge-base", "origin/main", "HEAD").stdout.strip()
base = g("show", f"{mb}:{H}").stdout
pr = g("show", f"HEAD:{H}").stdout
main = g("show", f"origin/main:{H}").stdout
if not pr.startswith(base.rstrip("\n")):
    raise SystemExit(f"{br}: PR handoff is not a pure append to its base -- manual review")
added = pr[len(base.rstrip("\n")):].lstrip("\n")
r = g("merge", "--no-commit", "--no-ff", "origin/main", check=False)
conf = g("diff", "--name-only", "--diff-filter=U").stdout.split()
if conf and conf != [H]:
    g("merge", "--abort"); raise SystemExit(f"{br}: unexpected conflicts {conf}")
new = main.rstrip("\n") + "\n\n" + added
open(H, "w").write(new)
assert new.startswith(main.rstrip("\n")) and added.strip() in new
g("add", H)
g("commit", "-q", "-m", f"Merge main {g('rev-parse','--short','origin/main').stdout.strip()}: union-resolve append-only ENGINEERING_HANDOFF.md\n\nMain's handoff kept byte-for-byte as prefix; this PR's own section appended.\n\nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>\nClaude-Session: https://claude.ai/code/session_01Dbq4tPo8hamS4Mr7ohF43y")
left = g("diff", "--stat", "origin/main", "HEAD").stdout.strip().splitlines()[-1]
print(f"{br}: OK {g('rev-parse','--short','HEAD').stdout.strip()} | vs main: {left}")
