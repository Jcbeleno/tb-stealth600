import re,sys,collections
S=sys.argv[1]
m={l.split()[0]:l.split()[1] for l in open(S+'/resmap.txt')}
fn=None;body=[]
out=[]
def flush():
    if not fn: return
    txt=''.join(body)
    ids=sorted({m[d] for d in re.findall(r'DAT_([0-9a-f]+)',txt) if d in m})
    if not ids or re.search(r'QString::QString\(\(QString \*\)&DAT_[0-9a-f]+,"[0-9a-fA-F]{4}"\)',txt): return
    strs=[s for s in re.findall(r'"([^"]{6,120})"',txt) if not re.fullmatch(r'[0-9a-fA-F]{4}',s)]
    out.append((fn,ids,strs[:12]))
for line in open(S+'/s600.c',errors='replace'):
    if line.startswith('// ==== '):
        flush(); fn=line[8:].split()[0]; body=[]
    else: body.append(line)
flush()
for f,ids,strs in out: print(f,' '.join(ids),'|',' ;; '.join(strs))
