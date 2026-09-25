import json, subprocess
def g(*a):
    r=subprocess.run(["git",*a],capture_output=True,text=True); return r.returncode, r.stdout.strip()
d=json.load(open("/tmp/claude-0/pr_collect.json"))
out={}
for p in d["prs"]:
    if p["number"]>85: continue
    head="origin/"+p["head_ref"]; base=p["base_sha"]
    rc,full_base=g("rev-parse",base)
    rc,files=g("diff","--name-only",full_base,head)
    files=[f for f in files.split("\n") if f]
    absent,differs,same=[],[],[]
    for f in files:
        rc,_=g("cat-file","-e",f"origin/main:{f}")
        rc2,_=g("cat-file","-e",f"{head}:{f}")
        if rc2: continue            # deleted by the PR
        if rc: absent.append(f)
        elif g("diff","--quiet",head,"origin/main","--",f)[0]: differs.append(f)
        else: same.append(f)
    out[p["number"]]={"head":p["head_sha"],"base_ref":p["base_ref"],"pr_own_files":len(files),"absent_on_main":absent,"differs_on_main":differs,"identical_on_main":same}
    print(f"#{p['number']} own={len(files)} absent={len(absent)} differs={len(differs)} identical={len(same)} :: absent={absent[:6]} differs={differs[:4]}")
json.dump(out,open("/tmp/claude-0/old_prs.json","w"),indent=1)
