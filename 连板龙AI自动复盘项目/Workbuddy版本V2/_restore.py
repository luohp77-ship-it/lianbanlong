# -*- coding: utf-8 -*-
import shutil, struct, os

src = r'C:\new_tdx\T0002\diycol.dat.bak2'
dst = r'C:\new_tdx\T0002\diycol.dat'
shutil.copy2(src, dst)
print('Restored diycol.dat from backup')

d = open(dst, 'rb').read()
REC = 150
print('Records: %d' % (len(d)//REC))
for i in range(len(d)//REC):
    off = i*REC
    did = struct.unpack_from('<I', d, off+4)[0]
    name = d[off+8:off+24].split(b'\x00')[0].decode('gbk', errors='replace')
    b0 = d[off]
    print('  [%d] byte0=0x%02X did=%d name="%s"' % (i, b0, did, name))
print('\nbyte0=%d, total_records=%d -> MATCH: %s' % (d[0], len(d)//REC, d[0] == len(d)//REC))
