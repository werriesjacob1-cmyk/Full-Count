"""Fetch Baseball Savant pitch-level Statcast CSV per day (regular season), gzip + sha256 manifest."""
import datetime as dt, gzip, hashlib, json, os, sys, time, concurrent.futures as cf, requests
OUT="/tmp/claude-0/mlbdata/statcast"; MAN=os.path.join(OUT,"MANIFEST.jsonl")
URL=("https://baseballsavant.mlb.com/statcast_search/csv?all=true&type=details&hfGT=R%7C"
     "&game_date_gt={d}&game_date_lt={d}")
def days(a,b):
    d=dt.date.fromisoformat(a)
    while d<=dt.date.fromisoformat(b):
        yield d.isoformat(); d+=dt.timedelta(days=1)
def get(d):
    p=os.path.join(OUT,f"{d}.csv.gz")
    if os.path.exists(p): return d,"cached",None
    for i in range(4):
        try:
            r=requests.get(URL.format(d=d),timeout=120); r.raise_for_status(); b=r.content
            if b[:1]==b"<": raise RuntimeError("html response")
            with gzip.open(p,"wb",compresslevel=6) as fh: fh.write(b)
            return d,len(b),hashlib.sha256(b).hexdigest()
        except Exception as e:
            err=e; time.sleep(5*(i+1))
    return d,"FAIL",str(err)
ranges=[("2025-03-18","2025-09-28"),("2026-03-25","2026-09-30")]
todo=[d for a,b in ranges for d in days(a,b)]
with cf.ThreadPoolExecutor(4) as ex, open(MAN,"a") as man:
    for d,n,h in ex.map(get,todo):
        if n!="cached":
            man.write(json.dumps({"date":d,"bytes":n,"sha256":h,"fetched_at":dt.datetime.utcnow().isoformat()+"Z"})+"\n"); man.flush()
print("DONE", len(todo))
