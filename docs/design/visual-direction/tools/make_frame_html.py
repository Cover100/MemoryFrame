"""Writes the frame mock HTML files (frame/*.html). Each file is standalone: open it at 800x480."""
import os
HEAD = '''<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><title>{title}</title>
<!-- Memory Frame visual direction mock ({title}). 800x480. Mocks only, not app code. -->
<link rel="stylesheet" href="frame.css">{style}</head>
<body><div id="frame">
'''
TAIL = '\n</div></body></html>\n'
PHOTO = '<img class="media" src="../assets/photo-landscape.jpg" alt="">'
M = {}

M["01-slideshow-hint"] = ("Slideshow with menu hint", "", PHOTO + '''
<div class="pill hint"><span class="tap"></span>Double-tap for menu to pair a phone</div>''')

M["02-brightness"] = ("Brightness overlay", "", PHOTO + '''
<div class="pill brightness" role="status"><span class="sun">☀</span>
  <div class="track"><div class="fill" style="transform:scaleX(.72)"></div></div><span class="val">72%</span></div>''')

PAIR_CSS = '''<style>
.pair { position:absolute; inset:0; background:var(--mf-ink); padding:22px 28px; display:flex; flex-direction:column; }
.pair h1 { font-size:var(--mf-f-title); margin:0 0 16px; display:flex; justify-content:space-between; align-items:center; }
.cols { display:flex; gap:20px; flex:1; }
.col { flex:1; background:var(--mf-forest-900); border:1px solid var(--mf-forest-700); border-radius:var(--mf-r-lg);
       padding:16px; display:flex; flex-direction:column; align-items:center; gap:10px; min-width:0; }
.col h2 { margin:0; font-size:var(--mf-f-body); display:flex; gap:10px; align-items:center; align-self:flex-start; }
.col .qr { width:184px; }
.cap { font-size:var(--mf-f-small); color:var(--mf-oat); text-align:center; }
.cap b { color:var(--mf-linen); font-weight:700; }
.code { font-size:56px; white-space:nowrap; font-weight:700; letter-spacing:2px; font-variant-numeric:tabular-nums; color:var(--mf-linen); line-height:1; }
.close { min-height:56px; }
.col.codecol { justify-content:center; }
.col.codecol { flex:1.25; } .wide .col.site { flex:1.1; } .wide .col.codecol { flex:1; } .wide .code { font-size:var(--mf-f-code); }
.wide .col.site .qr { width:236px; }
</style>'''
M["03-pairing-wifi-site"] = ("Pairing: Wi-Fi QR, then site QR + code", PAIR_CSS, '''
<div class="pair">
  <h1>Pair a phone <button class="btn btn-neutral close">Close</button></h1>
  <div class="cols">
    <div class="col"><h2><span class="step-badge">1</span>Join Wi-Fi</h2>
      <div class="qr"><img src="../assets/qr-wifi.svg" alt=""></div>
      <div class="cap">Wi-Fi name<br><b>MemoryFrame-4F2A</b></div></div>
    <div class="col"><h2><span class="step-badge">2</span>Open site</h2>
      <div class="qr"><img src="../assets/qr-site.svg" alt=""></div>
      <div class="cap">or open<br><b>http://10.42.0.1/</b></div></div>
    <div class="col codecol"><h2><span class="step-badge">3</span>Enter code</h2>
      <div style="flex:1;display:flex;flex-direction:column;justify-content:center;align-items:center;gap:12px">
        <div class="code">482 913</div>
        <div class="cap">Type this on your phone if it asks for a code</div></div></div>
  </div>
</div>''')

M["04-pairing-site-only"] = ("Pairing: phone already on the frame's Wi-Fi (site QR + code)", PAIR_CSS, '''
<div class="pair wide">
  <h1>Pair your phone <button class="btn btn-neutral close">Close</button></h1>
  <div class="cols">
    <div class="col site"><h2><span class="step-badge">1</span>Scan with your phone</h2>
      <div class="qr"><img src="../assets/qr-site.svg" alt=""></div>
      <div class="cap">or open <b>http://10.42.0.1/</b></div></div>
    <div class="col codecol"><h2><span class="step-badge">2</span>Enter code</h2>
      <div style="flex:1;display:flex;flex-direction:column;justify-content:center;align-items:center;gap:14px">
        <div class="code">482 913</div>
        <div class="cap">Type this on your phone<br>if it asks for a code</div></div></div>
  </div>
</div>''')

M["05-paired-toast"] = ("Paired toast", "", PHOTO + '''
<div class="pill toast" role="status"><span class="tick">✓</span><div><b>Phone paired</b><span>Send photos from your phone.</span></div></div>''')

M["06-hotspot-error"] = ("Hotspot error screen", '''<style>
.err { position:absolute; inset:0; background:var(--mf-ink); padding:22px 28px; display:grid; grid-template-columns: 270px 1fr; gap:22px; padding:20px 22px 20px 26px; }
.err .left { display:flex; flex-direction:column; }
.err .icon { width:56px; height:56px; border-radius:50%; background:var(--mf-brick); color:#fff; display:grid; place-items:center; font-size:34px; font-weight:700; margin-bottom:16px; }
.err h1 { font-size:28px; margin:0 0 12px; line-height:1.15; }
.err p { color:var(--mf-oat); margin:0 0 12px; font-size:20px; }
.err .left .btn { margin-top:auto; align-self:flex-start; }
.err ol { margin:0; padding:0; list-style:none; counter-reset:s; display:flex; flex-direction:column; gap:6px; }
.err li { counter-increment:s; display:flex; gap:10px; align-items:center; background:var(--mf-forest-900); border:1px solid var(--mf-forest-700); border-radius:var(--mf-r-md); padding:6px 10px; font-size:18px; line-height:1.2; }
.err li::before { content:counter(s); flex:none; width:28px; height:28px; border-radius:50%; background:var(--mf-walnut-900); border:2px solid var(--mf-brass); color:var(--mf-brass); font-weight:700; font-size:16px; display:grid; place-items:center; }
.err code { font-family:inherit; font-weight:700; color:var(--mf-brass); }
.err .foot { font-size:18px; color:var(--mf-oat); margin-top:8px; line-height:1.2; }
.err h2 { font-size:var(--mf-f-label); margin:0 0 10px; color:var(--mf-linen); }
</style>''', '''
<div class="err" role="alert">
  <div class="left">
    <div class="icon">!</div>
    <h1>Memory Frame Wi‑Fi didn't start</h1>
    <p>Phones can't connect right now. Photos already on the frame will keep showing.</p>
    <button class="btn btn-neutral">Close</button>
  </div>
  <div>
    <h2>To fix it</h2>
    <ol>
      <li><span>Turn the frame off and take out the SD card.</span></li>
      <li><span>Put the card in a computer and open the drive called <code>bootfs</code>.</span></li>
      <li><span>Create an empty file named <code>memoryframe-reset</code> (no extension).</span></li>
      <li><span>If there's a file named <code>memoryframe-mode.txt</code>, make sure it only says: <code>product</code></span></li>
      <li><span>Put the card back and turn the frame on. Wait 1 minute.</span></li>
    </ol>
    <div class="foot">Still not working? Restart the frame once. If this screen comes back, ask for help.</div>
  </div>
</div>''')

M["07-menu"] = ("Double-tap menu (#66)", '''<style>
.menu { width:720px; padding:22px 24px; display:grid; grid-template-columns: 1fr 260px; grid-template-rows:auto 1fr auto; gap:0 24px; }
.menu .title { grid-column:1 / -1; display:flex; align-items:center; gap:12px; }
.menu .title .mark { width:14px; height:14px; border-radius:50%; background:var(--mf-brass); }
table { border-collapse:collapse; font-size:var(--mf-f-label); align-self:start; width:100%; }
th { text-align:left; color:var(--mf-oat); font-weight:400; padding:5px 0; white-space:nowrap; padding-right:16px; }
td { text-align:right; white-space:nowrap; padding:5px 0; font-weight:700; }
tr + tr th, tr + tr td { border-top:1px solid var(--mf-forest-700); }
.side { display:flex; flex-direction:column; gap:12px; }
.side .btn { width:100%; }
.bottom { grid-column:1 / -1; display:flex; justify-content:space-between; margin-top:18px; padding-top:18px; border-top:1px solid var(--mf-forest-700); }
.bottom .btn { width:200px; }
</style>''', PHOTO + '''
<div class="scrim"><div class="card menu" role="dialog" aria-label="Menu">
  <div class="title"><span class="mark"></span>Memory Frame</div>
  <table>
    <tr><th>IP</th><td>10.42.0.1</td></tr>
    <tr><th>Wi-Fi</th><td>MemoryFrame-4F2A (hotspot)</td></tr>
    <tr><th>Site</th><td>10.42.0.1</td></tr>
    <tr><th>Free storage</th><td>21.4 GB of 28.6 GB</td></tr>
    <tr><th>Phones paired</th><td>2</td></tr>
    <tr><th>Version</th><td>0.9.3</td></tr>
  </table>
  <div class="side">
    <button class="btn btn-primary">Pair new device</button>
    <button class="btn btn-neutral">Sign out all phones</button>
  </div>
  <div class="bottom">
    <button class="btn btn-neutral">Close</button>
    <button class="btn btn-danger">Shut down</button>
  </div>
</div></div>''')

def confirm(title, body, cancel, ok, okcls):
    return PHOTO + f'''
<div class="scrim"><div class="card dialog" role="alertdialog">
  <h1 class="title">{title}</h1>
  <p class="body">{body}</p>
  <div class="row"><button class="btn btn-neutral btn-default" autofocus>{cancel}</button><button class="btn {okcls}">{ok}</button></div>
</div></div>'''
M["08-shutdown-confirm"] = ("Shut down confirm (#66)", "", confirm("Shut down the frame?",
    "Photos stay on the frame. To turn it on again, unplug it and plug it back in.", "Cancel", "Shut down", "btn-danger"))
M["09-dev-mode-confirm"] = ("Switch to dev mode? (#67)", "", confirm("Switch to dev mode?",
    "Phones on the frame's Wi-Fi will disconnect until you restart the frame.", "Cancel", "Switch", "btn-accent"))

SD_CSS = '''<style>
.sd { position:absolute; inset:0; background:var(--mf-ink); display:flex; flex-direction:column; align-items:center; justify-content:center; gap:22px; text-align:center; }
.sd h1 { font-size:var(--mf-f-display); margin:0; line-height:1.1; }
.sd .wait { font-size:30px; font-weight:700; color:var(--mf-brass); margin:0; }
.sd .sub { font-size:var(--mf-f-small); color:var(--mf-oat); margin:0; }
%s
</style>'''
SD = '''
<div class="sd" role="status">
  <div class="spinner"></div>
  <h1>Shutting down</h1>
  <p class="wait">Wait 10 seconds, then unplug</p>
  <p class="sub">Photos are saved on the frame</p>
</div>'''
M["10-shutting-down"] = ("#65 option B: Shutting down (live)", SD_CSS % "", SD)
# frozen: the panel keeps the last image after power-off. Spinner stopped mid-turn; text never animates.
M["11-shutting-down-frozen"] = ("#65 option B: frozen last frame after power-off", SD_CSS %
    ".spinner{animation:none; transform:rotate(-137deg);}", SD)

M["12-dev-banner"] = ("Dev-mode banner", "", PHOTO + '''
<div class="pill banner" role="status"><span class="dot"></span>Developer Wi-Fi · 192.168.50.124 · restart to exit</div>
<div class="pill hint"><span class="tap"></span>Double-tap for menu</div>''')

M["13-wifi-rejoin"] = ("Wi-Fi changed: scan to rejoin (MF-18)", '''<style>
.rj { position:absolute; inset:0; background:var(--mf-ink); padding:24px 32px; display:grid; grid-template-columns: 268px 1fr; gap:32px; align-items:center; }
.rj .qr { width:268px; }
.rj h1 { font-size:var(--mf-f-title); margin:0 0 16px; }
.rj p { margin:0 0 10px; color:var(--mf-oat); font-size:var(--mf-f-label); }
.rj p b { color:var(--mf-linen); }
.rj .count { margin-top:22px; display:flex; align-items:baseline; gap:12px; color:var(--mf-oat); font-size:var(--mf-f-label); }
.rj .count b { font-size:48px; color:var(--mf-brass); font-variant-numeric:tabular-nums; }
.rj .bar { height:8px; background:var(--mf-forest-700); border-radius:4px; overflow:hidden; margin-top:10px; }
.rj .bar div { height:100%; width:100%; background:var(--mf-brass); transform-origin:left; transform:scaleX(.83); }
</style>''', '''
<div class="rj" role="status">
  <div class="qr"><img src="../assets/qr-wifi-new.svg" alt=""></div>
  <div>
    <h1>Wi-Fi changed: scan to rejoin</h1>
    <p>Wi-Fi: <b>Garden Frame</b></p>
    <p>Then open <b>http://10.42.0.1/</b> and tap <b style="white-space:nowrap">"Keep new Wi-Fi"</b></p>
    <div class="count">Old Wi-Fi returns in <b>4:09</b></div>
    <div class="bar"><div></div></div>
  </div>
</div>''')

for name, (title, style, body) in M.items():
    with open(f"frame/{name}.html", "w") as f:
        f.write(HEAD.format(title=title, style=style) + body.strip("\n") + TAIL)
print(len(M), "frame mocks")
