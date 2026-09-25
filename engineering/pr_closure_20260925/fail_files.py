import json, re, sys, urllib.request
API="https://api.github.com/repos/werriesjacob1-cmyk/Full-Count"
def get(p, raw=False):
    with urllib.request.urlopen(API+p, timeout=120) as r:
        return r.read().decode("utf-8","replace") if raw else json.load(r)
def failing(run_id):
    res={}
    for j in get(f"/actions/runs/{run_id}/jobs")["jobs"]:
        if j["conclusion"]!="failure": continue
        cur=None
        for line in get(f"/actions/jobs/{j['id']}/logs",raw=True).splitlines():
            body=line.split("Z ",1)[-1]
            m=re.search(r"##\[group\](\S+\.py)",body)
            if m: cur=m.group(1); continue
            if "##[endgroup]" in body: cur=None; continue
            if cur and (re.match(r"\s*(AssertionError|ModuleNotFoundError|[A-Za-z]+Error)\b",body) or re.match(r"\s*FAILED: ",body) or "[FAIL]" in body):
                res.setdefault(cur,[]).append(body.strip()[:120])
    return {k:v[:2] for k,v in res.items()}
src=json.load(open(sys.argv[1]))
items = src.items() if isinstance(src,dict) and "prs" not in src else [(f"#{p['number']}",{"ci":p["ci"]}) for p in src["prs"]]
out={}
for k,v in items:
    for wf,c in v["ci"].items():
        if c["conclusion"]=="failure":
            out[f"{k} {wf}"]={"url":c["url"],"failing":failing(c["id"])}
            print(k,wf,json.dumps(out[f"{k} {wf}"]["failing"])[:300])
json.dump(out,open(sys.argv[2],"w"),indent=1)
