# -*- coding: utf-8 -*-
import struct, os

# 1. Check diycol.dat
f = r'C:\new_tdx\T0002\diycol.dat'
d = open(f, 'rb').read()
REC = 150
print('=== diycol.dat ===')
for i in range(len(d)//REC):
    off = i*REC
    did = struct.unpack_from('<I', d, off+4)[0]
    name = d[off+8:off+24].split(b'\x00')[0].decode('gbk', errors='replace')
    color = struct.unpack_from('<I', d, off+0x1c)[0]
    b0 = d[off]
    print('[%d] byte0=0x%02X did=%d name="%s" color=0x%06X' % (i, b0, did, name, color))
print('byte0(%d) == records(%d): %s' % (d[0], len(d)//REC, d[0]==len(d)//REC))

# 2. Check extern_user.txt
ext = r'C:\new_tdx\T0002\Signals\extern_user.txt'
lines = open(ext, 'r', encoding='gbk').readlines()
counts = {}
markets = set()
for l in lines:
    p = l.strip().split('|')
    if len(p) >= 4:
        counts[int(p[2])] = counts.get(int(p[2]), 0) + 1
        markets.add(p[0])
print('\n=== extern_user.txt: %d lines ===' % len(lines))
[print('  DataID=%d: %d lines' % (k, counts[k])) for k in sorted(counts)]
print('  Markets: %s' % sorted(markets))
illegal = [l for l in lines if l.split('|')[0] not in ('0','1')]
print('  Illegal markets: %d' % len(illegal))

# 3. Sample data
for l in lines:
    if '|3|' in l:
        p = l.split('|')
        print('\nSample DataID=3: %s|%s|%s' % (p[0], p[1], p[3][:70]))
        break

print('\n=== VERDICT ===')
ok = all([
    counts.get(1,0) > 0,
    counts.get(2,0) > 0,
    counts.get(3,0) > 0,
    len(illegal) == 0,
    d[0] == len(d)//REC,
])
print('ALL OK' if ok else 'HAS ISSUES')
