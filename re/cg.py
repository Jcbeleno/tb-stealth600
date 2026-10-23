import re,sys,collections
S=sys.argv[1]; targets=sys.argv[2].split(',')
funcs={};fn=None;buf=[]
for line in open(S+'/s600.c',errors='replace'):
    if line.startswith('// ==== '):
        if fn: funcs[fn]=''.join(buf)
        fn=line[8:].split()[0]; buf=[]
    else: buf.append(line)
funcs[fn]=''.join(buf)
callers=collections.defaultdict(set)
for f,b in funcs.items():
    for c in set(re.findall(r'(FUN_[0-9a-f]+)\(',b)):
        if c!=f: callers[c].add(f)
def strs(f): return [s for s in re.findall(r'"([^"]{4,80})"',funcs.get(f,'')) if not re.fullmatch(r'[0-9a-fA-F]{1,4}',s)][:8]
for t in targets:
    print('##',t)
    for c in sorted(callers[t]):
        print('  <-',c,strs(c))
        for c2 in sorted(callers[c])[:4]:
            print('      <-',c2,strs(c2)[:5])
