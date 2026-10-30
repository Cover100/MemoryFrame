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

# ---------------------------------------------------------------------------------------------
# Menu option B (user brief, 17 Oct): info-only left column (title, gift message, free storage at the foot), one vertical button stack on the right.
# Right column top to bottom: Shut down, Pair new device, Sign out of all phones, Close (default ring).
# The stack is bottom-aligned so Close sits nearest the thumb; Shut down is ~124 px below the
# screen's top edge and 48 px from its right edge, with a wider 28 px gap under it.
MENU2_CSS = '''<style>
.menu2 { position:absolute; inset:20px; padding:28px; display:grid; grid-template-columns: 1fr 304px; gap:0 24px; }
.menu2 .info { display:flex; flex-direction:column; min-width:0; }
.menu2 .title { display:flex; align-items:center; gap:12px; margin:0 0 22px; }
.menu2 .title .mark { width:14px; height:14px; border-radius:50%; background:var(--mf-brass); flex:none; }
.store { margin-top:auto; }
.store .lab { font-size:var(--mf-f-small); color:var(--mf-oat); margin:0 0 4px; }
.store .val { font-size:var(--mf-f-body); font-weight:700; margin:0 0 10px; font-variant-numeric:tabular-nums; }
.store .val span { font-weight:400; color:var(--mf-oat); }
.store .track { height:8px; border-radius:4px; background:var(--mf-forest-700); overflow:hidden; }
.store .used { height:100%; width:100%; background:var(--mf-leaf); transform-origin:left; } /* scaleX(used fraction) */
.note { margin:0; background:var(--mf-walnut-900); border:1px solid var(--mf-walnut); border-radius:var(--mf-r-lg);
        padding:14px 16px 16px; }
.note .q { display:block; color:var(--mf-brass); font-size:44px; line-height:.6; height:22px; font-weight:700; }
.note p { margin:0; font-size:var(--mf-f-label); line-height:1.35; color:var(--mf-linen);
          display:-webkit-box; -webkit-box-orient:vertical; -webkit-line-clamp:5; overflow:hidden; overflow-wrap:anywhere; } /* 140 chars of mixed case = 4 lines; 5 is the safety cap (all caps) */
.note.empty { background:transparent; border:1px dashed var(--mf-walnut); padding:16px; }
.note.empty b { display:block; font-size:var(--mf-f-label); color:var(--mf-oat); margin:0 0 4px; }
.note.empty p { color:var(--mf-oat); font-size:var(--mf-f-small); display:block; }
.stack { display:flex; flex-direction:column; justify-content:flex-end; gap:12px; }
.stack .btn { width:100%; min-height:64px; padding:0 16px; white-space:nowrap; }
.stack .btn-danger { margin-bottom:16px; } /* 12 + 16 = 28 px between Shut down and the rest */
</style>'''
STORE = '''<div class="store">
      <div class="lab">Free storage</div>
      <div class="val">21.4 GB free <span>of 28.8 GB</span></div>
      <div class="track" role="img" aria-label="26% used"><div class="used" style="transform:scaleX(.26)"></div></div>
    </div>'''
STACK = '''<div class="stack">
    <button class="btn btn-danger">Shut down</button>
    <button class="btn btn-primary">Pair new device</button>
    <button class="btn btn-neutral">Sign out of all phones</button>
    <button class="btn btn-neutral btn-default" autofocus>Close</button>
  </div>'''
def menu2(note):
    return PHOTO + '''
<div class="scrim"><div class="card menu2" role="dialog" aria-label="Menu">
  <div class="info">
    <h1 class="title"><span class="mark"></span>Memory Frame</h1>
    ''' + note + '''
    ''' + STORE + '''
  </div>
  ''' + STACK + '''
</div></div>'''
GIFT_MSG = ("Happy 80th, Nana! Every photo on here was sent by one of us, from wherever we are. "
            "We miss you and we love you. Sam, Priya and the kids xx")   # 138 chars (max 140)
M["07-menu-optB"] = ("Double-tap menu option B: info left, buttons right", MENU2_CSS, menu2(
    '<figure class="note"><span class="q" aria-hidden="true">\u201c</span><p>' + GIFT_MSG + '</p></figure>'))
M["07-menu-optB-empty"] = ("Double-tap menu option B: no message set", MENU2_CSS, menu2(
    '<div class="note empty"><b>No message yet</b><p>Add one from your phone in Settings, under \u201cMessage on the frame\u201d.</p></div>'))

# Menu option A (user's reference, 17 Oct): the #70 07-menu layout kept (info table left, Pair / Sign out
# right, Close bottom-left with the default ring, Shut down bottom-right), widened so the right buttons sit
# inside the card, plus the gift message as a 2-line strip under the table.
MENUA_CSS = '''<style>
.menuA { position:absolute; inset:16px; padding:22px 24px 20px; display:grid; grid-template-columns: 1fr 260px;
         grid-template-rows:auto auto 1fr auto; gap:0 28px; }
.menuA .title { grid-column:1 / -1; display:flex; align-items:center; gap:12px; margin:0 0 10px; }
.menuA .title .mark { width:14px; height:14px; border-radius:50%; background:var(--mf-brass); flex:none; }
.menuA table { border-collapse:collapse; font-size:var(--mf-f-label); align-self:start; width:100%; }
.menuA th { text-align:left; color:var(--mf-oat); font-weight:400; padding:3px 16px 3px 0; white-space:nowrap; }
.menuA td { text-align:right; white-space:nowrap; padding:3px 0; font-weight:700; }
.menuA tr + tr th, .menuA tr + tr td { border-top:1px solid var(--mf-forest-700); }
.menuA .side { display:flex; flex-direction:column; gap:12px; }
.menuA .side .btn { width:100%; }
.strip { grid-column:1 / -1; align-self:start; margin-top:12px; display:flex; gap:12px; align-items:flex-start;
         background:var(--mf-walnut-900); border:1px solid var(--mf-walnut); border-radius:var(--mf-r-md); padding:8px 14px; }
.strip .q { color:var(--mf-brass); font-size:36px; line-height:1; height:24px; font-weight:700; flex:none; }
.strip p { margin:0; font-size:var(--mf-f-small); line-height:1.3; color:var(--mf-linen);
           display:-webkit-box; -webkit-box-orient:vertical; -webkit-line-clamp:2; overflow:hidden; overflow-wrap:anywhere; }
.strip.empty { background:transparent; border:1px dashed var(--mf-walnut); padding:8px 14px; }
.strip.empty p { color:var(--mf-oat); -webkit-line-clamp:1; }
.menuA .bottom { grid-column:1 / -1; display:flex; justify-content:space-between; padding-top:14px; border-top:1px solid var(--mf-forest-700); }
.menuA .bottom .btn { width:220px; }
</style>'''
def menuA(strip):
    return PHOTO + '''
<div class="scrim"><div class="card menuA" role="dialog" aria-label="Menu">
  <h1 class="title"><span class="mark"></span>Memory Frame</h1>
  <table>
    <tr><th>IP</th><td>10.42.0.1</td></tr>
    <tr><th>Wi-Fi</th><td>MemoryFrame-4F2A (hotspot)</td></tr>
    <tr><th>Site</th><td>10.42.0.1</td></tr>
    <tr><th>Free storage</th><td>21.4 GB of 28.8 GB</td></tr>
    <tr><th>Phones paired</th><td>2</td></tr>
    <tr><th>Version</th><td>0.9.3</td></tr>
  </table>
  <div class="side">
    <button class="btn btn-primary">Pair new device</button>
    <button class="btn btn-neutral">Sign out all phones</button>
  </div>
  ''' + strip + '''
  <div class="bottom">
    <button class="btn btn-neutral btn-default" autofocus>Close</button>
    <button class="btn btn-danger">Shut down</button>
  </div>
</div></div>'''
M["07-menu-optA"] = ("Double-tap menu option A: info table (reference layout) + message strip", MENUA_CSS, menuA(
    '<figure class="strip" style="margin:12px 0 0"><span class="q" aria-hidden="true">\u201c</span><p>' + GIFT_MSG + '</p></figure>'))
M["07-menu-optA-empty"] = ("Double-tap menu option A: no message set", MENUA_CSS, menuA(
    '<div class="strip empty"><p>No message yet. Add one from your phone in Settings, under \u201cMessage on the frame\u201d.</p></div>'))

# Menu FINAL (user's hybrid choice, 17 Oct): B's left column (message card + storage meter, no IP/Site/
# Version) with A's buttons (Pair / Sign out on the right, Close bottom-left with the default ring,
# Shut down bottom-right). All buttons 64 px. Details live on the phone under "About this frame".
MENUF_CSS = MENU2_CSS + '''<style>
.menuF { position:absolute; inset:16px; padding:22px 24px 20px; display:grid; grid-template-columns: 1fr 280px;
         grid-template-rows:auto 1fr auto; gap:0 28px; }
.menuF .title { grid-column:1 / -1; display:flex; align-items:center; gap:12px; margin:0 0 14px; }
.menuF .title .mark { width:14px; height:14px; border-radius:50%; background:var(--mf-brass); flex:none; }
.menuF .info { display:flex; flex-direction:column; min-width:0; }
.menuF .store { margin-top:auto; padding-top:14px; }
.menuF .store .val { margin:0 0 8px; display:flex; justify-content:space-between; align-items:baseline; gap:12px; }
.menuF .store .val .lab { font-size:var(--mf-f-small); color:var(--mf-oat); margin:0; font-weight:400; }
.menuF .side { display:flex; flex-direction:column; gap:12px; }
.menuF .side .btn, .menuF .bottom .btn { min-height:64px; white-space:nowrap; }
.menuF .side .btn { width:100%; }
.menuF .bottom { grid-column:1 / -1; display:flex; justify-content:space-between; margin-top:16px; padding-top:16px; border-top:1px solid var(--mf-forest-700); }
.menuF .bottom .btn { width:220px; }
</style>'''
STORE_F = '''<div class="store">
      <div class="val"><span class="lab">Free storage</span><b>21.4 GB free <span>of 28.8 GB</span></b></div>
      <div class="track" role="meter" aria-label="Storage used" aria-valuemin="0" aria-valuemax="28.8" aria-valuenow="7.4"><div class="used" style="transform:scaleX(.26)"></div></div>
    </div>'''
def menuF(note):
    return PHOTO + '''
<div class="scrim"><div class="card menuF" role="dialog" aria-label="Menu">
  <h1 class="title"><span class="mark"></span>Memory Frame</h1>
  <div class="info">
    ''' + note + '''
    ''' + STORE_F + '''
  </div>
  <div class="side">
    <button class="btn btn-primary">Pair new device</button>
    <button class="btn btn-neutral">Sign out all phones</button>
  </div>
  <div class="bottom">
    <button class="btn btn-neutral btn-default" autofocus>Close</button>
    <button class="btn btn-danger">Shut down</button>
  </div>
</div></div>'''
M["07-menu-final"] = ("Double-tap menu FINAL (hybrid): message + storage left, Pair / Sign out right, Close / Shut down bottom", MENUF_CSS, menuF(
    '<figure class="note"><span class="q" aria-hidden="true">\u201c</span><p>' + GIFT_MSG + '</p></figure>'))
M["07-menu-final-empty"] = ("Double-tap menu FINAL (hybrid): no message set", MENUF_CSS, menuF(
    '<div class="note empty"><b>No message yet</b><p>Add one from your phone in Settings, under \u201cMessage on the frame\u201d.</p></div>'))

# Confirm v2: card vertically centred, a minimum height, and the buttons pinned to the bottom with the
# same 28 px padding on every side (fixes the app's buttons sitting high with dead space under them).
CONFIRM2_CSS = '''<style>
.dialog2 { width:600px; min-height:272px; padding:28px; display:flex; flex-direction:column; }
.dialog2 .title { margin:0 0 12px; }
.dialog2 .body { margin:0 0 32px; }
.dialog2 .row { margin-top:auto; display:flex; gap:var(--mf-s-4); }
.dialog2 .row .btn { flex:1; min-height:64px; }
</style>'''
def confirm2(title, body, cancel, ok, okcls):
    return PHOTO + f'''
<div class="scrim"><div class="card dialog2" role="alertdialog">
  <h1 class="title">{title}</h1>
  <p class="body">{body}</p>
  <div class="row"><button class="btn btn-neutral btn-default" autofocus>{cancel}</button><button class="btn {okcls}">{ok}</button></div>
</div></div>'''
M["08-shutdown-confirm-v2"] = ("Shut down confirm v2 (buttons pinned to the bottom)", CONFIRM2_CSS, confirm2("Shut down the frame?",
    "Photos stay on the frame. To turn it on again, unplug it and plug it back in.", "Cancel", "Shut down", "btn-danger"))
M["09-dev-mode-confirm-v2"] = ("Switch to dev mode? v2 (buttons pinned to the bottom)", CONFIRM2_CSS, confirm2("Switch to dev mode?",
    "Phones on the frame's Wi-Fi will disconnect until you restart the frame.", "Cancel", "Switch", "btn-accent"))

for name, (title, style, body) in M.items():
    with open(f"frame/{name}.html", "w") as f:
        f.write(HEAD.format(title=title, style=style) + body.strip("\n") + TAIL)
print(len(M), "frame mocks")
