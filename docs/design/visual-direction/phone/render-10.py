# Renders 09 (updated) and 10 theme comparison. Run from phone/ with the mfvenv python.
from playwright.sync_api import sync_playwright
from PIL import Image, ImageDraw, ImageFont
import os
d=os.getcwd()
shots=[('09-library-grid',412),('09b-library-select',412),('09c-library-delete-confirm',412),('09-library-360-130',360),
       ('10a-home-light',412),('10a-library-light',412),('10b-home-dark',412),('10b-library-dark',412)]
with sync_playwright() as p:
    b=p.chromium.launch(executable_path='/usr/bin/google-chrome')
    for n,w in shots:
        pg=b.new_page(viewport={'width':w,'height':915}); pg.goto(f'file://{d}/{n}.html'); pg.wait_for_timeout(400)
        pg.screenshot(path=f'{n}.png'); pg.close()
    b.close()
# 09 contact sheet
ns=['09-library-grid','09b-library-select','09c-library-delete-confirm','09-library-360-130']
ims=[Image.open(n+'.png').convert('RGB') for n in ns]
s=Image.new('RGB',(sum(i.width for i in ims)+80,975),'#E2D8CA'); dr=ImageDraw.Draw(s); x=16
for n,i in zip(ns,ims):
    s.paste(i,(x,16)); dr.rectangle([x-1,15,x+i.width,16+i.height],outline='#5E5146'); dr.text((x,940),n+'.png',fill='#1F2A24'); x+=i.width+16
s.save('09-library-sheet.png')
# 10 compare, ~1600 wide
F=lambda z,b=False: ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans%s.ttf'%('-Bold' if b else ''),z)
sc=0.86; tw=int(412*sc); th=int(915*sc)
W=1600; s=Image.new('RGB',(W,th+1000),'#2B2A27'); dr=ImageDraw.Draw(s)
dr.text((24,18),'Phone theme: which looks like the frame?',font=F(34,True),fill='#F3EDE4')
cols=[('A  Light (paper + Atkinson)',['10a-home-light','10a-library-light']),('B  Dark (frame theme + Atkinson)',['10b-home-dark','10b-library-dark'])]
x=24
for title,ns in cols:
    dr.text((x,74),title,font=F(28,True),fill='#D6B36A')
    for n in ns:
        s.paste(Image.open(n+'.png').convert('RGB').resize((tw,th),Image.LANCZOS),(x,118)); x+=tw+14
    x+=20
fr=Image.open('../frame/07-menu-final.png').convert('RGB'); fw=W-48; fh=int(fr.height*fw/fr.width)
fw2=int(fw*0.5); fh2=int(fr.height*fw2/fr.width)
y=118+th+24
dr.text((24,y),'The frame (07 menu, 800x480, scaled)',font=F(28,True),fill='#D6B36A')
s.paste(fr.resize((fw2,fh2),Image.LANCZOS),(24,y+44))
dr.multiline_text((24+fw2+30,y+50),'Home and Library (selection) per variant.\nB uses the frame tokens: ink / forest-900\nbackground, forest-800 cards, linen + oat text,\nforest-600 primary, brick delete, brass focus.\nBoth use Atkinson Hyperlegible,\nself-hosted from the frame.',font=F(24),fill='#F3EDE4',spacing=10)
s=s.crop((0,0,W,y+44+fh2+24)); s.save('10-theme-compare.png')
