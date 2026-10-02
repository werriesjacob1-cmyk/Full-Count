"""Read-only Claude Code transcript usage analyzer (FULL COUNT efficiency benchmark).

Usage: python3 transcript_usage.py <session.jsonl> [label]
Reads a local ~/.claude/projects/<proj>/<session>.jsonl (or a subagents/*.jsonl) and prints
token totals (cache read/write, uncached, output), context-per-call distribution, tool-call
counts, repeat file reads and tool-result volume. Sends nothing anywhere; writes nothing.
"""
import json,sys,collections,re,os,glob
def analyze(path,label):
    U=collections.Counter(); tools=collections.Counter(); reads=collections.Counter(); bash_kinds=collections.Counter()
    nmsg=0; ts=[]; compacts=0; models=collections.Counter(); per_turn=[]; result_bytes=collections.Counter(); usage_by_model=collections.defaultdict(collections.Counter)
    tool_id_name={}; userturns=0
    seen_ids=set()
    for line in open(path):
        try: o=json.loads(line)
        except: continue
        t=o.get('type')
        if o.get('timestamp'): ts.append(o['timestamp'])
        if t=='system' and 'compact' in json.dumps(o)[:400].lower(): compacts+=1
        if o.get('isCompactSummary'): compacts+=0
        m=o.get('message') or {}
        if t=='assistant':
            mid=m.get('id')
            u=m.get('usage') or {}
            if mid and mid in seen_ids: 
                pass
            else:
                if mid: seen_ids.add(mid)
                nmsg+=1
                for k in ('input_tokens','cache_read_input_tokens','cache_creation_input_tokens','output_tokens'):
                    U[k]+=u.get(k,0) or 0
                    usage_by_model[m.get('model')][k]+=u.get(k,0) or 0
                models[m.get('model')]+=1
                per_turn.append((u.get('input_tokens',0) or 0)+(u.get('cache_read_input_tokens',0) or 0)+(u.get('cache_creation_input_tokens',0) or 0))
            for c in m.get('content') or []:
                if isinstance(c,dict) and c.get('type')=='tool_use':
                    n=c['name']; tools[n]+=1; tool_id_name[c['id']]=n
                    inp=c.get('input') or {}
                    if n=='Read': reads[inp.get('file_path')]+=1
                    if n=='Bash':
                        cmd=inp.get('command','')
                        k='git' if re.match(r'\s*(cd [^&]*&&\s*)?git\b',cmd) else 'grep/rg/find' if re.search(r'\b(grep|rg|find)\b',cmd[:200]) else 'python' if 'python' in cmd[:200] else 'cat/sed/head' if re.search(r'\b(cat|sed|head|tail|wc)\b',cmd[:120]) else 'other'
                        bash_kinds[k]+=1
        if t=='user':
            c=m.get('content')
            if isinstance(c,str): userturns+=1
            elif isinstance(c,list):
                for x in c:
                    if isinstance(x,dict) and x.get('type')=='tool_result':
                        cc=x.get('content'); s=len(json.dumps(cc)) if not isinstance(cc,str) else len(cc)
                        result_bytes[tool_id_name.get(x.get('tool_use_id'),'?')]+=s
                    elif isinstance(x,dict) and x.get('type')=='text': userturns+=1
    rep=[f for f,n in reads.items() if n>1]
    print(f"### {label}")
    print("assistant msgs",nmsg,"user text turns",userturns, "span",ts[0] if ts else None,"->",ts[-1] if ts else None)
    tot=sum(U.values()); print("usage",dict(U),"TOTAL",tot)
    if tot: print(" shares: cache_read %.1f%% cache_create %.1f%% uncached_in %.1f%% output %.1f%%"%(100*U['cache_read_input_tokens']/tot,100*U['cache_creation_input_tokens']/tot,100*U['input_tokens']/tot,100*U['output_tokens']/tot))
    if per_turn:
        s=sorted(per_turn); print(" context per call: median %d p90 %d max %d mean %d"%(s[len(s)//2],s[int(.9*len(s))],s[-1],sum(s)/len(s)))
    print("models",dict(models))
    print("tools",tools.most_common(25))
    print("bash kinds",dict(bash_kinds))
    print("Read calls",sum(reads.values()),"unique",len(reads),"files read >1x",len(rep),"repeat reads",sum(n-1 for n in reads.values()))
    print(" top reread",sorted(reads.items(),key=lambda x:-x[1])[:12])
    print("tool_result bytes by tool",[(k,v//1000) for k,v in result_bytes.most_common(12)],"KB")
    return U,tools,reads
if __name__=='__main__':
    analyze(sys.argv[1],sys.argv[2] if len(sys.argv)>2 else sys.argv[1])
