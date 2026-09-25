import json, urllib.request
API="https://api.github.com/repos/werriesjacob1-cmyk/Full-Count"
def get(path):
    with urllib.request.urlopen(API+path, timeout=60) as r: return json.load(r)
prs=get("/pulls?state=open&per_page=100")
out=[]
for p in sorted(prs,key=lambda p:-p["number"]):
    sha=p["head"]["sha"]
    runs=get(f"/actions/runs?head_sha={sha}&per_page=50")["workflow_runs"]
    ci={}
    for r in sorted(runs,key=lambda r:r["created_at"]):   # latest wins
        ci[r["name"]]={"conclusion":r["conclusion"] or r["status"],"url":r["html_url"],"id":r["id"]}
    out.append({"number":p["number"],"title":p["title"],"draft":p["draft"],"head_ref":p["head"]["ref"],"head_sha":sha[:10],
                "base_ref":p["base"]["ref"],"base_sha":p["base"]["sha"][:10],"ci":ci})
main=get("/actions/runs?branch=main&event=push&per_page=10")["workflow_runs"]
mt=[r for r in main if r["name"]=="Test Suite"][:3]
json.dump({"prs":out,"main_test_suite_recent":[{"sha":r["head_sha"][:10],"conclusion":r["conclusion"] or r["status"],"url":r["html_url"]} for r in mt]},
          open("/tmp/claude-0/pr_collect.json","w"),indent=1)
print(len(out))
for o in out:
    c=o["ci"]; f=lambda n:(c.get(n) or {}).get("conclusion","-")
    print(o["number"],o["head_sha"],o["base_ref"][:32],"root:",f("Test Suite"),"nfl:",f("NFL Test Suite"),"web:",f("NFL Web Integration Tests"))
print("main:",[(m["sha"],m["conclusion"]) for m in json.load(open("/tmp/claude-0/pr_collect.json"))["main_test_suite_recent"]])
