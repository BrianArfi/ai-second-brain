"""Make Jira ADF descriptions survive a Linear round trip, without changing words.
1) paragraphs between a literal ```lang line and a closing ``` become one real codeBlock
2) a literal '+' in prose gets an inline code mark (Linear otherwise turns ++ pairs into <ins>)
usage: sanitize.py <archive issues.jsonl> <check|apply> [KEY,...]"""
import sys, json, re, copy, time, requests
sys.path.insert(0, r"C:/Users/you/.gemini/antigravity/scratch/product-second-brain/.agent/skills/jira-connector/scripts")
import jira_client as J
D="examplevendor.atlassian.net"
def ptext(p): return "".join(c.get("text","") for c in p.get("content",[]) or [] if c.get("type")=="text")
def fix_fences(content):
    out=[]; i=0; changed=False
    while i<len(content):
        n=content[i]
        if n.get("type")=="paragraph" and ptext(n).lstrip().startswith("```"):
            buf=[]; first=ptext(n).lstrip()[3:]
            m=re.match(r"([A-Za-z0-9_+-]*)\s*(.*)", first, re.S); lang, rest = m.group(1), m.group(2)
            j=i; closed=False
            while j<len(content):
                t=ptext(content[j]) if j>i else rest
                if j>i and content[j].get("type")!="paragraph": break
                if t.rstrip().endswith("```"):
                    buf.append(t.rstrip()[:-3].rstrip()); closed=True; break
                buf.append(t); j+=1
            if closed:
                code="\n".join(b for b in buf if b is not None).strip("\n")
                node={"type":"codeBlock","content":[{"type":"text","text":code}]}
                if lang: node["attrs"]={"language":lang}
                out.append(node); i=j+1; changed=True; continue
        if isinstance(n,dict) and n.get("content") and n.get("type") not in ("codeBlock",):
            n2=copy.deepcopy(n); n2["content"],c=fix_fences(n2["content"]) if n.get("type") in ("listItem","tableCell","tableHeader","panel","blockquote","expand") else (n2["content"],False)
            changed|=c; out.append(n2)
        else: out.append(n)
        i+=1
    return out, changed
def fix_plus(n):
    ch=False
    if isinstance(n,dict):
        if n.get("type")=="codeBlock": return n,False
        if "content" in n:
            new=[]
            for c in n["content"]:
                if c.get("type")=="text" and "+" in c.get("text","") and not any(m.get("type")=="code" for m in c.get("marks",[]) or []):
                    parts=re.split(r"(\+)", c["text"])
                    for p in parts:
                        if not p: continue
                        t=copy.deepcopy(c); t["text"]=p
                        if p=="+": t["marks"]=[m for m in (t.get("marks") or []) if m.get("type")=="link"]+[{"type":"code"}]
                        new.append(t)
                    ch=True
                else:
                    c2,cc=fix_plus(c); new.append(c2); ch|=cc
            n=dict(n); n["content"]=new
    return n,ch
src, mode = sys.argv[1], sys.argv[2]
only=set(sys.argv[3].split(",")) if len(sys.argv)>3 else None
todo=[]
for line in open(src, encoding="utf-8"):
    i=json.loads(line); f=i["fields"]
    if (f.get("status") or {}).get("statusCategory",{}).get("key")=="done" or (f.get("issuetype") or {}).get("name")=="Epic": continue
    if only and i["key"] not in only: continue
    d=f.get("description")
    if not d: continue
    nd=copy.deepcopy(d); nd["content"],c1=fix_fences(nd["content"]); nd,c2=fix_plus(nd)
    if c1 or c2:
        plus_count=sum(ptext(x).count("+") for x in d["content"] if x.get("type")=="paragraph")
        if not c2 or "+" in json.dumps(d):
            todo.append((i["key"],c1,c2,nd))
print(mode, "issues to change:", len(todo), [(k,"fence" if a else "","plus" if b else "") for k,a,b,_ in todo])
if mode=="apply":
    for k,a,b,nd in todo:
        r=requests.put(f"https://{D}/rest/api/3/issue/{k}?notifyUsers=false", json={"fields":{"description":nd}}, headers=J.HEADERS, auth=J.AUTH, timeout=30)
        print(k, r.status_code, r.text[:150] if r.status_code!=204 else ""); time.sleep(0.4)
