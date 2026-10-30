"""Render every mock HTML to PNG with headless Chromium (Playwright).
Frame: 800x480 @1x. Phone: 412x915 CSS viewport @1x (Galaxy S22 Ultra). Animations are paused at t=0.4 s
so the spinner shows mid-turn; the "frozen" mock pins its own angle."""
import glob, os, sys
from playwright.sync_api import sync_playwright
CHROME = os.environ.get("CHROME", "/usr/bin/google-chrome")
root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
jobs = [("frame", 800, 480), ("phone", 412, 915)]
only = sys.argv[1:]
with sync_playwright() as p:
    b = p.chromium.launch(executable_path=CHROME)
    for d, w, h in jobs:
        pg = b.new_page(viewport={"width": w, "height": h}, device_scale_factor=1)
        for f in sorted(glob.glob(os.path.join(root, d, "[0-9]*.html"))):
            if only and not any(o in f for o in only): continue
            pg.goto("file://" + f); pg.wait_for_load_state("networkidle")
            pg.evaluate("document.fonts.ready")
            pg.evaluate("document.getAnimations().forEach(a=>{a.pause(); a.currentTime=400;})")
            # overflow check: anything wider/taller than the viewport, or text clipped inside its box
            bad = pg.evaluate("""()=>{const out=[];const W=innerWidth,H=innerHeight;
              document.querySelectorAll('body *').forEach(e=>{const r=e.getBoundingClientRect();
                if(r.width&&(r.right>W+0.5||r.bottom>H+0.5||r.left<-0.5||r.top<-0.5)&&getComputedStyle(e).position!=='absolute'||(e.scrollWidth>e.clientWidth+1&&getComputedStyle(e).overflow!=='visible'&&e.clientWidth>0))
                  out.push(e.tagName+'.'+e.className+' '+Math.round(r.right)+'x'+Math.round(r.bottom));});
              return out.slice(0,6);}""")
            pg.screenshot(path=f[:-5] + ".png")
            print(os.path.relpath(f, root), "WARN " + "; ".join(bad) if bad else "ok")
    b.close()
