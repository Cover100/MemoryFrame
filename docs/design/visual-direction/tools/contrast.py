def lum(h):
    h=h.lstrip('#'); c=[int(h[i:i+2],16)/255 for i in (0,2,4)]
    c=[x/12.92 if x<=0.03928 else ((x+0.055)/1.055)**2.4 for x in c]
    return 0.2126*c[0]+0.7152*c[1]+0.0722*c[2]
def cr(a,b):
    la,lb=sorted([lum(a),lum(b)],reverse=True); return (la+0.05)/(lb+0.05)
import sys
pairs=[l.split() for l in sys.stdin if l.strip()]
for p in pairs:
    r=cr(p[0],p[1]); print(f"{p[0]} on {p[1]}: {r:.2f} {'AAA' if r>=7 else 'AA' if r>=4.5 else 'AA-large' if r>=3 else 'FAIL'}  {' '.join(p[2:])}")
