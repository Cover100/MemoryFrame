// #103 (phone/13-VIDEO-TRIM.md): pick the part of a long video to keep, before it is uploaded, and show how far
// the frame has got preparing it. Only two whole-millisecond numbers travel (trim_start_ms / trim_end_ms); the
// frame checks them again and cuts the clip in its one ffmpeg run. The top-level helpers are pure (tested in Node).

const MF_TRIM_MAX = 30000, MF_TRIM_MIN = 1000;

function mfMmss(ms){
    const s = Math.max(0, Math.round(ms / 1000));
    return Math.floor(s / 60) + ":" + String(s % 60).padStart(2, "0");
}

function mfTrimDefault(dur){                        // the first 30 s (or the whole clip)
    return {start: 0, end: Math.max(Math.min(dur, MF_TRIM_MAX), Math.min(dur, MF_TRIM_MIN))};
}

// Move one handle to ms: kept inside the clip and >= 1 s from the other handle (it pushes against it, never crosses).
function mfTrimMove(sel, which, ms, dur){
    ms = Math.round(Math.max(0, Math.min(dur, ms)));
    const gap = Math.min(MF_TRIM_MIN, dur);
    if(which === "start"){ return {start: Math.max(0, Math.min(ms, sel.end - gap)), end: sel.end}; }
    return {start: sel.start, end: Math.min(dur, Math.max(ms, sel.start + gap))};
}

// Keyboard: arrows 0.5 s, PageUp/PageDown 5 s, Home/End to the limits. Returns null for other keys.
function mfTrimKey(sel, which, key, dur){
    const step = {ArrowLeft: -500, ArrowDown: -500, ArrowRight: 500, ArrowUp: 500, PageDown: -5000, PageUp: 5000}[key];
    const at = sel[which];
    if(step !== undefined){ return mfTrimMove(sel, which, at + step, dur); }
    if(key === "Home"){ return mfTrimMove(sel, which, 0, dur); }
    if(key === "End"){ return mfTrimMove(sel, which, dur, dur); }
    return null;
}

function mfTrimError(sel){                         // same words as the frame's 400 answers
    const len = sel.end - sel.start;
    if(len > MF_TRIM_MAX){ return "That part is " + mfMmss(len) + ". Choose 30 seconds or less."; }
    if(len < MF_TRIM_MIN){ return "That part is under 1 second. Choose at least 1 second."; }
    return "";
}

// Screen readers hear 25 / 50 / 75 % once each (not every poll). Returns the milestone just passed, or 0.
function mfMilestone(prev, pct){
    for(const m of [75, 50, 25]){ if(prev < m && pct >= m){ return m; } }
    return 0;
}

// ---------------- the trim screen (upload.html #trim) ----------------
// Resolves {start, end} for "Use this part", or null for Cancel / back (the video is dropped from the batch).
function mfTrimPick(file, dur){
    const $ = (id)=> document.getElementById(id);
    const box = $("trim"), vid = $("trimVideo"), play = $("trimPlay"), tc = $("trimTime"), len = $("trimLen"),
          of = $("trimOf"), track = $("trimTrack"), fill = $("trimFill"), err = $("trimErr"), use = $("trimUse"),
          hs = {start: $("trimStart"), end: $("trimEnd")};
    const url = URL.createObjectURL(file);
    let sel = mfTrimDefault(dur);
    vid.src = url;
    $("trimTotal").textContent = mfMmss(dur);
    of.textContent = "of " + mfMmss(dur) + " · up to 0:30";
    function draw(){
        const pct = (ms)=> (ms / dur * 100).toFixed(3) + "%";
        hs.start.style.left = pct(sel.start); hs.end.style.left = pct(sel.end);
        fill.style.left = pct(sel.start); fill.style.width = ((sel.end - sel.start) / dur * 100).toFixed(3) + "%";
        len.textContent = mfMmss(sel.end - sel.start) + " selected";
        ["start", "end"].forEach((w)=>{
            const h = hs[w];
            h.setAttribute("aria-valuemin", "0"); h.setAttribute("aria-valuemax", String(Math.round(dur / 1000)));
            h.setAttribute("aria-valuenow", String(Math.round(sel[w] / 1000)));
            h.setAttribute("aria-valuetext", (w === "start" ? "Start " : "End ") + mfMmss(sel[w]));
        });
        const msg = mfTrimError(sel);
        err.textContent = msg; err.hidden = !msg;
        track.classList.toggle("bad", !!msg);
        use.disabled = !!msg;
        if(msg){ use.setAttribute("aria-describedby", "trimErr"); }else{ use.removeAttribute("aria-describedby"); }
    }
    function seek(ms){ if(vid.paused){ try{ vid.currentTime = ms / 1000; }catch(e){ /* not loaded yet */ } } }
    function set(next, which){ if(next){ sel = next; draw(); seek(sel[which]); } }
    const offs = [];
    function on(el, ev, fn){ el.addEventListener(ev, fn); offs.push(()=> el.removeEventListener(ev, fn)); }
    ["start", "end"].forEach((w)=>{
        const h = hs[w];
        on(h, "keydown", (e)=>{ const n = mfTrimKey(sel, w, e.key, dur); if(n){ e.preventDefault(); set(n, w); } });
        on(h, "pointerdown", (e)=>{ e.preventDefault(); h.setPointerCapture(e.pointerId); h.focus(); });
        on(h, "pointermove", (e)=>{
            if(!h.hasPointerCapture(e.pointerId)){ return; }
            const r = track.getBoundingClientRect();
            set(mfTrimMove(sel, w, Math.round((e.clientX - r.left) / r.width * dur / 100) * 100, dur), w);
        });
    });
    // The preview plays only the selection, silently, then stops at its start again.
    on(play, "click", ()=>{
        if(vid.paused){ vid.currentTime = sel.start / 1000; vid.play().catch(()=>{}); }else{ vid.pause(); }
    });
    on(vid, "play", ()=>{ play.setAttribute("aria-label", "Pause"); play.classList.add("on"); });
    on(vid, "pause", ()=>{ play.setAttribute("aria-label", "Play selection"); play.classList.remove("on"); });
    on(vid, "timeupdate", ()=>{
        if(!vid.paused && vid.currentTime * 1000 >= sel.end){ vid.pause(); vid.currentTime = sel.start / 1000; }
        tc.textContent = mfMmss(vid.currentTime * 1000);
    });
    draw();
    box.hidden = false;
    document.body.classList.add("trimming");
    const back = document.activeElement;
    $("trimTitle").focus();
    return new Promise((resolve)=>{
        function done(v){
            offs.forEach((f)=> f());
            vid.pause(); vid.removeAttribute("src"); vid.load(); URL.revokeObjectURL(url);
            box.hidden = true;
            document.body.classList.remove("trimming");
            if(back && back.focus){ back.focus(); }
            resolve(v);
        }
        on(use, "click", ()=>{ if(!mfTrimError(sel)){ done({start: sel.start, end: sel.end}); } });
        on($("trimCancel"), "click", ()=> done(null));
        on($("trimBack"), "click", ()=> done(null));
    });
}

// Duration of a picked video, read by the phone's own player (no upload). null when it can't be read.
function mfVideoDuration(file){
    return new Promise((resolve)=>{
        const v = document.createElement("video"), url = URL.createObjectURL(file);
        const end = (d)=>{ clearTimeout(t); URL.revokeObjectURL(url); v.removeAttribute("src"); resolve(d); };
        const t = setTimeout(()=> end(null), 10000);
        v.preload = "metadata"; v.muted = true;
        v.onloadedmetadata = ()=> end(isFinite(v.duration) && v.duration > 0 ? Math.round(v.duration * 1000) : null);
        v.onerror = ()=> end(null);
        v.src = url;
    });
}
