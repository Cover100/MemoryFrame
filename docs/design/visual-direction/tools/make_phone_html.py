"""Writes the phone mock HTML files (phone/*.html). Open each at 412x915."""
HEAD = '''<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{title}</title>
<!-- Memory Frame visual direction mock ({title}). 412x915. Mocks only, not app code. -->
<link rel="stylesheet" href="phone.css">{style}</head><body>
'''
TAIL = '\n</body></html>\n'
def bar(name="Memory Frame", back=False, icon=""):
    b = '<a class="back" href="#">‹</a>' if back else ''
    return f'<header class="bar">{b}<div class="name"><span class="dot"></span>{name}</div>{icon}</header>'
STATUS = '<div class="status"><span class="ok"></span>Connected to the frame · 21.4 GB free</div>'
M = {}

M["01-pair-code"] = ("Pair this phone (code entry)", '''<style>
.code { display:flex; gap:8px; margin:18px 0 8px; }
.code span { flex:1; height:60px; border:1.5px solid var(--mf-line); border-radius:var(--mf-r-sm); background:#fff; display:grid; place-items:center; font-size:28px; font-weight:700; font-variant-numeric:tabular-nums; }
.code span.gap { flex:0 0 6px; border:0; background:none; }
.code span.cur { outline:3px solid var(--mf-green); outline-offset:1px; border-color:var(--mf-green); }
.code span.cur::after { content:""; width:2px; height:30px; background:var(--mf-green); }
.hero { display:flex; justify-content:center; margin:8px 0 22px; }
.hero div { width:120px; height:76px; border-radius:12px; background:var(--mf-walnut); position:relative; }
.hero div::before { content:""; position:absolute; left:10px; top:14px; width:46px; height:46px; border-radius:50%; background:var(--mf-ink); border:3px solid #2a2a2a; }
.hero div::after { content:"482 913"; position:absolute; right:8px; top:10px; width:50px; height:56px; border-radius:5px; background:var(--mf-forest-900); color:var(--mf-linen); font-size:10px; font-weight:700; display:grid; place-items:center; text-align:center; line-height:1.1; padding:4px; }
</style>''', bar() + '''
<main>
  <div class="hero" aria-hidden="true"><div></div></div>
  <h1>Pair this phone</h1>
  <p class="muted">Enter the 6-digit code shown on the frame.</p>
  <div class="code" aria-label="Pairing code"><span>4</span><span>8</span><span>2</span><span class="gap"></span><span>9</span><span class="cur"></span><span></span></div>
  <p class="small muted">The code is on the frame's screen, under "Enter code".</p>
  <button class="btn btn-primary btn-big block" style="margin-top:22px">Pair</button>
  <div class="card" style="margin-top:28px">
    <h2 style="font-size:17px">Can't see a code?</h2>
    <p class="small muted" style="margin:0">Double-tap the frame's screen and choose <b>Pair new device</b>.</p>
  </div>
</main>''')

thumbs = "".join(f'<li><img src="../assets/thumb-{(i % 6) + 1}.jpg" alt=""></li>' for i in range(9))
M["02-home-library"] = ("Home / library", '''<style>
.grid { list-style:none; margin:0 0 12px; padding:0; display:grid; grid-template-columns:repeat(3,1fr); gap:4px; }
.grid li { aspect-ratio:1; border-radius:6px; overflow:hidden; background:var(--mf-line); position:relative; }
.grid img { width:100%; height:100%; object-fit:cover; display:block; }
.grid li.hid img { opacity:.35; } .grid li.hid::after { content:"Hidden"; position:absolute; left:6px; bottom:6px; background:var(--mf-forest-900); color:var(--mf-linen); font-size:13px; padding:2px 8px; border-radius:999px; }
.sec { display:flex; justify-content:space-between; align-items:baseline; margin:22px 0 10px; }
.sec a { color:var(--mf-green); font-weight:600; text-decoration:none; font-size:var(--mf-p-small); }
</style>''', bar(icon='<a class="icon" href="#" aria-label="Settings">⚙</a>') + STATUS + '''
<main>
  <a class="btn btn-primary btn-big block" href="#">＋&nbsp; Upload photos</a>
  <div class="sec"><h2 style="margin:0">Library</h2><a href="#">See all 128 photos ›</a></div>
  <ul class="grid">''' + thumbs.replace('<li><img src="../assets/thumb-5.jpg"', '<li class="hid"><img src="../assets/thumb-5.jpg"', 1) + '''</ul>
  <ul class="rows" style="margin-top:20px">
    <li><span class="ico">⌁</span><span class="t">Frame Wi-Fi<small>MemoryFrame-4F2A</small></span><span class="chev">›</span></li>
    <li><span class="ico">⚙</span><span class="t">Settings<small>10 s · Fade · Shuffle on</small></span><span class="chev">›</span></li>
    <li><span class="t" style="color:var(--mf-red);font-weight:600">Sign out all phones</span></li>
  </ul>
</main>''')

M["03-upload-progress"] = ("Upload in progress", '''<style>
.files { list-style:none; margin:0; padding:0; display:flex; flex-direction:column; gap:10px; }
.files li { background:#fff; border:1px solid var(--mf-line); border-radius:var(--mf-r-md); padding:10px; display:flex; gap:12px; align-items:center; }
.files img { width:52px; height:52px; border-radius:8px; object-fit:cover; flex:none; }
.files .t { flex:1; min-width:0; }
.files .n { font-weight:600; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; font-size:16px; }
.files .s { font-size:var(--mf-p-small); color:var(--mf-text-2); margin-top:2px; }
.files .s.ok { color:var(--mf-green); font-weight:600; } .files .s.bad { color:var(--mf-red); font-weight:600; }
.files .progress { margin-top:6px; }
.sum { background:var(--mf-green-50); color:var(--mf-green); border-radius:var(--mf-r-md); padding:12px 14px; font-weight:600; margin:14px 0 16px; font-size:16px; }
</style>''', bar("Upload photos", back=True) + '''
<main>
  <p class="muted small" style="margin-bottom:12px">Free space: 21.4 GB (room for about 5,000 photos)</p>
  <a class="btn btn-neutral block" href="#">Choose more photos</a>
  <div class="sum" role="status">3 of 6 done (3 added or already there). They appear on the frame within a few seconds.</div>
  <ul class="files">
    <li><img src="../assets/thumb-1.jpg" alt=""><div class="t"><div class="n">IMG_2041.jpg (3.2 MB)</div><div class="s ok">✓ Added</div></div></li>
    <li><img src="../assets/thumb-2.jpg" alt=""><div class="t"><div class="n">IMG_2042.heic (2.7 MB)</div><div class="s ok">✓ Added</div></div></li>
    <li><img src="../assets/thumb-3.jpg" alt=""><div class="t"><div class="n">Beach day with Nana.jpg (4.1 MB)</div><div class="s">Already on the frame</div></div></li>
    <li><img src="../assets/thumb-4.jpg" alt=""><div class="t"><div class="n">IMG_2047.jpg (5.0 MB)</div><div class="s">Uploading 64%</div><div class="progress"><div style="transform:scaleX(.64)"></div></div></div></li>
    <li><img src="../assets/thumb-6.jpg" alt=""><div class="t"><div class="n">IMG_2050.jpg (3.6 MB)</div><div class="s">Waiting...</div></div></li>
    <li><img src="../assets/thumb-5.jpg" alt=""><div class="t"><div class="n">IMG_2053.jpg (2.9 MB)</div><div class="s">Waiting...</div></div></li>
  </ul>
</main>''')

M["04-settings"] = ("Settings", '''<style>
section.card h2 { display:flex; justify-content:space-between; align-items:baseline; }
.row { display:flex; align-items:center; justify-content:space-between; gap:12px; }
</style>''', bar("Settings", back=True) + '''
<main>
  <section class="card"><h2>Slide time <span class="saved">✓ Saved</span></h2>
    <div class="seg" role="radiogroup"><button aria-checked="false">5 s</button><button aria-checked="true">10 s</button><button aria-checked="false">30 s</button><button aria-checked="false">1 min</button><button aria-checked="false">5 min</button></div>
    <p class="small muted" style="margin:10px 0 0">How long each photo stays on the frame.</p></section>
  <section class="card"><h2>Transition</h2>
    <div class="seg" role="radiogroup"><button aria-checked="true">Fade</button><button aria-checked="false">None</button></div></section>
  <section class="card"><div class="row"><div><h2 style="margin:0 0 2px">Shuffle</h2><div class="small muted">Show photos in a random order</div></div><span class="switch" role="switch" aria-checked="true"></span></div></section>
</main>''')

M["05-wifi-change"] = ("Frame Wi-Fi: change with warning", "", bar("Frame Wi-Fi", back=True) + '''
<main>
  <h1>Frame Wi-Fi</h1>
  <p class="muted">Current Wi-Fi: <b style="color:var(--mf-text)">MemoryFrame-4F2A</b></p>
  <div class="warn" role="note"><span class="i">!</span><div><b>All phones will disconnect.</b><br>Rejoin with the new password within 5 minutes, or the old Wi-Fi comes back.</div></div>
  <label class="f" for="ssid">New name</label>
  <input class="field" id="ssid" value="Garden Frame">
  <label class="f" for="pw">New password</label>
  <div style="display:flex;gap:8px"><input class="field focus" id="pw" type="password" value="new-password-88" style="flex:1"><button class="btn btn-neutral" style="height:52px">Show</button></div>
  <p class="small muted" style="margin-top:6px">8 to 63 letters, numbers or symbols.</p>
  <button class="btn btn-primary btn-big block" style="margin-top:20px">Change Wi-Fi</button>
</main>''')

M["06-wifi-confirm"] = ("Frame Wi-Fi: confirm", '''<style>
.scrim { position:fixed; inset:0; background:rgba(31,42,36,.55); display:flex; align-items:flex-end; }
.sheet { background:#fff; width:100%; border-radius:20px 20px 0 0; padding:24px 20px 28px; }
.sheet .rowb { display:flex; gap:12px; margin-top:20px; } .sheet .rowb .btn { flex:1; }
</style>''', bar("Frame Wi-Fi", back=True) + '''
<main class="dim">
  <h1>Frame Wi-Fi</h1>
  <p class="muted">Current Wi-Fi: <b style="color:var(--mf-text)">MemoryFrame-4F2A</b></p>
  <label class="f">New name</label><input class="field" value="Garden Frame">
  <label class="f">New password</label><input class="field" type="password" value="new-password-88">
</main>
<div class="scrim"><div class="sheet" role="alertdialog">
  <h2 style="font-size:24px">Change Wi-Fi?</h2>
  <div class="warn" style="margin:12px 0"><span class="i">!</span><div>All phones will disconnect. Rejoin with the new password within 5 minutes or the old Wi-Fi comes back.</div></div>
  <p style="margin:0">New Wi-Fi: <b>Garden Frame</b></p>
  <div class="rowb"><button class="btn btn-neutral btn-big" style="outline:3px solid var(--mf-green);outline-offset:2px">Cancel</button><button class="btn btn-primary btn-big">Change Wi-Fi</button></div>
</div></div>''')

M["07-keep-new-wifi"] = ("Keep new Wi-Fi?", '''<style>
.ring { width:120px; height:120px; border-radius:50%; border:8px solid var(--mf-line); display:grid; place-items:center; margin:24px auto 8px; position:relative; }
.ring b { font-size:30px; font-variant-numeric:tabular-nums; color:var(--mf-warn-text); }
.ring::before { content:""; position:absolute; inset:-8px; border-radius:50%; border:8px solid transparent; border-top-color:var(--mf-brass); border-right-color:var(--mf-brass); transform:rotate(20deg); }
</style>''', bar() + '''
<main>
  <h1>Keep new Wi-Fi?</h1>
  <p>You're connected to <b>"Garden Frame"</b>. Tap to keep it. Other phones will need to join it too.</p>
  <button class="btn btn-primary btn-big block" style="margin-top:12px">Keep new Wi-Fi</button>
  <div class="ring" aria-hidden="true"><b>3:42</b></div>
  <p class="muted" style="text-align:center">Old Wi-Fi returns in <b>3:42</b></p>
  <div class="card" style="margin-top:20px"><p class="small muted" style="margin:0">Not ready? Do nothing, and the frame goes back to <b>MemoryFrame-4F2A</b> with the old password.</p></div>
</main>''')

M["08-offline-error"] = ("Can't reach the frame (offline)", '''<style>
.big-ico { width:88px; height:88px; border-radius:50%; background:var(--mf-red-50); color:var(--mf-red); display:grid; place-items:center; font-size:44px; font-weight:800; margin:28px auto 18px; }
ol.steps { padding-left:22px; margin:0 0 18px; } ol.steps li { margin-bottom:8px; }
</style>''', bar() + '''
<div class="status" style="background:var(--mf-red-50);color:var(--mf-red)"><span class="ok" style="background:var(--mf-red)"></span>Not connected to the frame</div>
<main>
  <div class="big-ico" aria-hidden="true">!</div>
  <h1 style="text-align:center">Can't reach the frame</h1>
  <p class="muted" style="text-align:center">Network error. Are you still on the frame's Wi\u2011Fi?</p>
  <div class="card">
    <ol class="steps">
      <li>Open your phone's Wi-Fi settings.</li>
      <li>Join <b>MemoryFrame-4F2A</b> (or scan the Wi-Fi QR on the frame).</li>
      <li>Come back and tap <b>Try again</b>.</li>
    </ol>
    <p class="small muted" style="margin:0">Your phone may say "No internet". That's normal: the frame has its own Wi-Fi.</p>
  </div>
  <button class="btn btn-primary btn-big block">Try again</button>
  <div class="err" role="alert" style="margin-top:16px">IMG_2047.jpg didn't finish uploading. It will need to be sent again.</div>
</main>''')

# ---------------------------------------------------------------------------------------------
# Settings v2 (menu v2 brief, 17 Oct): "Message on the frame" first, then the existing settings, then
# "About this frame", which now holds the network/version info that left the frame's menu.
SET2_CSS = '''<style>
section.card h2 { display:flex; justify-content:space-between; align-items:baseline; }
.row { display:flex; align-items:center; justify-content:space-between; gap:12px; }
main.s2 { padding:12px 16px 12px; }
main.s2 .card { margin-bottom:10px; padding:14px 16px; }
.msg { width:100%; height:122px; resize:none; border:1.5px solid var(--mf-line); border-radius:var(--mf-r-sm); background:var(--mf-card);
       padding:10px 14px; font:inherit; color:var(--mf-text); line-height:1.4; display:block; }
.msg.focus { outline:3px solid var(--mf-green); outline-offset:1px; border-color:var(--mf-green); }
.meta { display:flex; justify-content:space-between; gap:12px; margin:6px 0 10px; font-size:var(--mf-p-small); color:var(--mf-text-2); }
.meta .count { font-variant-numeric:tabular-nums; white-space:nowrap; }
.acts { display:flex; align-items:center; gap:12px; }
.acts .btn { min-width:120px; }
.acts .btn[disabled] { background:var(--mf-green-50); color:var(--mf-green); }
.prev { margin-top:12px; padding-top:12px; border-top:1px solid var(--mf-line); font-size:var(--mf-p-small); color:var(--mf-text-2); }
/* Mini frame note: the frame's note card at 75 % (252 px wide = 336 px at 20 px on the frame), so the
   line breaks and the 5-line cap match what the frame will show. Frame font if installed, else system. */
.prev .mini { margin-top:6px; width:286px; background:var(--mf-walnut-900); border:1px solid var(--mf-walnut); border-radius:12px;
              padding:10px 16px 12px; color:var(--mf-linen); font-family:var(--mf-font-frame); font-size:15px; line-height:1.35; }
.prev .mini::before { content:"“"; display:block; color:var(--mf-brass); font-size:32px; line-height:.6; height:14px; font-weight:700; }
.prev .mini span { display:-webkit-box; -webkit-box-orient:vertical; -webkit-line-clamp:5; overflow:hidden; overflow-wrap:anywhere; }
.seg button { min-height:40px; }
</style>'''
MSG = ("Happy 80th, Nana! Every photo on here was sent by one of us, from wherever we are. "
       "We miss you and we love you. Sam, Priya and the kids xx")
def settings2(saved):
    head = '<h2>Message on the frame' + (' <span class="saved" role="status">✓ Saved</span>' if saved else '') + '</h2>'
    field = '<textarea class="msg' + ('' if saved else ' focus') + '" id="frameMsg" maxlength="140" aria-describedby="msgHelp msgCount">' + MSG + '</textarea>'
    if saved:
        acts = '<div class="acts"><button class="btn" disabled>Saved</button><button class="btn btn-text-danger">Remove message</button></div>'
        prev = '<div class="prev">On the frame now<div class="mini"><span>' + MSG + '</span></div></div>'
    else:
        acts = '<div class="acts"><button class="btn btn-primary">Save</button><span class="small muted">Not saved yet</span></div>'
        prev = '<div class="prev">Preview: how it will look on the frame<div class="mini"><span>' + MSG + '</span></div></div>'
    return bar("Settings", back=True) + '''
<main class="s2">
  <section class="card">''' + head + '''
    <label class="sr" for="frameMsg" style="position:absolute;left:-9999px">Message on the frame</label>
    ''' + field + '''
    <div class="meta"><span id="msgHelp">Shown in the frame's menu</span><span class="count" id="msgCount" aria-live="polite">''' + str(len(MSG)) + ''' / 140</span></div>
    ''' + acts + '''
    ''' + prev + '''
  </section>
  <section class="card"><h2>Slide time</h2>
    <div class="seg" role="radiogroup"><button aria-checked="false">5 s</button><button aria-checked="true">10 s</button><button aria-checked="false">30 s</button><button aria-checked="false">1 min</button><button aria-checked="false">5 min</button></div></section>
  <section class="card"><div class="row"><h2 style="margin:0">Transition</h2>
    <div class="seg" role="radiogroup" style="width:180px"><button aria-checked="true">Fade</button><button aria-checked="false">None</button></div></div></section>
  <section class="card"><div class="row"><div><h2 style="margin:0 0 2px">Shuffle</h2><div class="small muted">Show photos in a random order</div></div><span class="switch" role="switch" aria-checked="true"></span></div></section>
  <ul class="rows"><li><span class="ico">i</span><span class="t">About this frame<small>Wi-Fi, address, phones paired, version</small></span><span class="chev">›</span></li></ul>
</main>'''
M["04-settings-v2"] = ("Settings v2: message on the frame (editing)", SET2_CSS, settings2(False))
M["04b-settings-message-saved"] = ("Settings v2: message saved", SET2_CSS, settings2(True))

for name, (title, style, body) in M.items():
    with open(f"phone/{name}.html", "w") as f:
        f.write(HEAD.format(title=title, style=style) + body.strip("\n") + TAIL)
print(len(M), "phone mocks")
