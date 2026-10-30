// #80 phone Library (phone/09-LIBRARY.md). The grid is rendered by the server; this adds selection and delete.
// - Tap a photo, long-press it (500 ms) or tap Select to start selecting. No preview, no extra requests.
// - In selection mode a tap toggles a photo; Back / Cancel leave the mode (a history entry is pushed for it).
// - Delete (N) opens a bottom sheet (Cancel focused); Delete sends one POST /api/v1/media/delete.
// Plain DOM, no animation other than opacity/transform in CSS (Pi friendly). No inline styles (CSP).
(function(){
    "use strict";
    const $ = (id)=> document.getElementById(id);
    const grid = $("grid");
    if(!grid){ return; }                                  // empty library: nothing to select
    const CSRF = document.querySelector('meta[name="csrf-token"]').content;
    const body = document.body;
    // #102: only ready, shown (not filtered out) items are selectable; waiting/preparing/failed videos are div.vtile
    const tiles = ()=> grid.querySelectorAll("li:not(.filtered) > button.tile");
    const selected = new Set();                            // media ids (strings, from data-id)
    const sheet = $("confirm"), delBtn = $("delBtn"), err = $("delErr");
    const BULK = 1000;                                     // = api.MAX_BULK
    const LONG_MS = 500, MOVE_PX = 10;
    let selecting = false, popGuard = 0, busy = false;   // busy: a delete request is in flight
    const toolbar = $("toolbar");
    function fit(){ document.documentElement.style.setProperty("--toolbar-h", toolbar.offsetHeight + "px"); }   // CSSOM: allowed by the CSP
    window.addEventListener("resize", fit);

    // ----- missing thumbnails (undecodable or video): plain tile, CSS glyph -----
    function plain(img){
        const t = img.closest(".tile");
        const ph = document.createElement("span");
        ph.className = "ph"; ph.setAttribute("aria-hidden", "true");
        t.classList.add("plain"); img.replaceWith(ph);
    }
    grid.querySelectorAll("img").forEach((img)=>{
        if(img.complete && img.naturalWidth === 0 && img.getAttribute("src")){ plain(img); }  // failed before we ran
        else { img.addEventListener("error", ()=> plain(img), {once: true}); }
    });

    // ----- history: Back leaves the sheet, then selection mode -----
    function back(){ popGuard++; history.back(); }        // drop our own entry without acting on the pop
    window.addEventListener("popstate", ()=>{
        if(popGuard){ popGuard--; return; }
        if(!sheet.hidden){ closeSheet(true); }
        else if(selecting){ exitSelect(true); }
    });

    // ----- selection mode -----
    function idOf(tile){ return tile.parentElement.dataset.id; }
    function update(){
        const n = selected.size, all = tiles().length;
        $("selCount").textContent = n + " selected";
        $("selAll").textContent = n && n === all ? "Unselect all" : "Select all";
        delBtn.textContent = "Delete (" + n + ")";
        delBtn.disabled = busy || n === 0;
        fit();
    }
    function setPressed(tile, on){
        tile.setAttribute("aria-pressed", on ? "true" : "false");
        if(on){ selected.add(idOf(tile)); } else { selected.delete(idOf(tile)); }
    }
    function enterSelect(first){
        if(selecting){ return; }
        selecting = true;
        history.pushState({mfSelect: 1}, "");
        body.classList.add("selecting");
        tiles().forEach((t)=> setPressed(t, t === first));
        update();
        if(!first){ $("selAll").focus(); }                 // the Select button just disappeared
    }
    function exitSelect(fromPop){
        if(!selecting){ return; }
        if(!fromPop && history.state && history.state.mfSelect){ back(); }
        selecting = false;
        selected.clear();
        body.classList.remove("selecting");
        tiles().forEach((t)=> t.removeAttribute("aria-pressed"));    // no aria-pressed outside the mode
        err.hidden = true;
        const sel = $("selectBtn"), a = document.activeElement;   // focus was on a control that is now hidden
        if(sel && (!a || a === body || a.closest("#toolbar, #selbar"))){ sel.focus(); }
    }
    function countText(n){
        const c = $("count");
        if(c.dataset.videos === undefined){ return n + " photo" + (n === 1 ? "" : "s") + " on the frame"; }
        const np = grid.querySelectorAll("li:not([data-kind])").length, nv = grid.querySelectorAll('li[data-kind="video"]').length;
        c.dataset.photos = np; c.dataset.videos = nv;
        return videoCount();
    }
    // ----- #102 Photos / Videos filter (session only: nothing stored) -----
    const chipP = $("chipPhotos"), chipV = $("chipVideos"), hint = $("chipHint");
    const plural = (n, w)=> n + " " + w + (n === 1 ? "" : "s");
    function videoCount(){
        const c = $("count"), np = Number(c.dataset.photos), nv = Number(c.dataset.videos);
        if(!chipP || (on(chipP) && on(chipV))){ return plural(np, "photo") + " · " + plural(nv, "video"); }
        return on(chipP) ? "Showing " + plural(np, "photo") : "Showing " + plural(nv, "video");
    }
    function on(chip){ return chip.getAttribute("aria-pressed") === "true"; }
    function applyFilter(){
        const p = on(chipP), v = on(chipV);
        grid.querySelectorAll("li").forEach((li)=>{
            li.classList.toggle("filtered", li.dataset.kind === "video" ? !v : !p);
        });
        [chipP, chipV].forEach((c)=>{
            if(on(c) && !on(c === chipP ? chipV : chipP)){ c.setAttribute("aria-disabled", "true"); }
            else { c.removeAttribute("aria-disabled"); }
        });
        grid.querySelectorAll("li.filtered > button.tile").forEach((t)=>{ if(t.getAttribute("aria-pressed") === "true"){ setPressed(t, false); } });
        $("count").textContent = videoCount();
        if(selecting){ update(); }
    }
    if(chipP){
        [chipP, chipV].forEach((c)=> c.addEventListener("click", ()=>{
            if(c.getAttribute("aria-disabled") === "true"){ hint.textContent = "At least one stays on."; return; }
            hint.textContent = "";
            c.setAttribute("aria-pressed", on(c) ? "false" : "true");
            applyFilter();
        }));
        applyFilter();
    }

    // Tap: start selecting with this photo, or toggle it. Long press does the same sooner (with a buzz).
    let press = null, longFired = false;
    grid.addEventListener("click", (e)=>{
        const act = e.target.closest(".vretry, .vremove");
        if(act){ videoAction(act); return; }
        const t = e.target.closest("button.tile");
        if(!t || busy){ return; }
        if(longFired){ longFired = false; return; }       // the long press already handled this touch
        if(!selecting){ enterSelect(t); return; }
        setPressed(t, t.getAttribute("aria-pressed") !== "true");
        update();
    });
    grid.addEventListener("pointerdown", (e)=>{
        const t = e.target.closest("button.tile");
        if(!t || e.button > 0){ return; }
        longFired = false;
        press = {x: e.clientX, y: e.clientY, timer: setTimeout(()=>{
            press = null;
            longFired = true;
            if(navigator.vibrate){ navigator.vibrate(15); }
            if(!selecting){ enterSelect(t); }
            else if(t.getAttribute("aria-pressed") !== "true"){ setPressed(t, true); update(); }
        }, LONG_MS)};
    });
    function endPress(){ if(press){ clearTimeout(press.timer); press = null; } }
    grid.addEventListener("pointermove", (e)=>{        // moved more than 10 px: it's a scroll, not a press
        if(press && Math.hypot(e.clientX - press.x, e.clientY - press.y) > MOVE_PX){ endPress(); }
    });
    ["pointerup", "pointercancel", "pointerleave"].forEach((ev)=> grid.addEventListener(ev, endPress));
    grid.addEventListener("contextmenu", (e)=>{ if(e.target.closest(".tile")){ e.preventDefault(); } });  // Android image menu

    $("selectBtn").addEventListener("click", ()=> enterSelect(null));
    $("selCancel").addEventListener("click", ()=> exitSelect(false));
    $("selAll").addEventListener("click", ()=>{
        const all = selected.size === tiles().length;
        tiles().forEach((t)=> setPressed(t, !all));
        update();
    });

    // ----- confirm sheet (same pattern as the #77 Wi-Fi sheet) -----
    const behind = ()=> [$("selbar"), grid, $("toolbar")];
    function openSheet(){
        const n = selected.size;
        const nv = [...selected].filter((id)=> grid.querySelector('li[data-id="' + Number(id) + '"][data-kind="video"]')).length;
        const what = nv === 0 ? "photo" : nv === n ? "video" : "item";            // #102
        $("confirmTitle").textContent = n === 1 ? "Delete this " + what + "?" : "Delete " + n + " " + what + "s?";
        $("confirmBody").textContent = n === 1 ? "It'll be removed from the frame. This can't be undone."
                                               : "They'll be removed from the frame. This can't be undone.";
        history.pushState({mfSheet: 1}, "");
        behind().forEach((el)=>{ el.inert = true; });
        body.classList.add("sheet-open");
        sheet.hidden = false;
        $("confirmCancel").focus();                        // the safe choice is the default
    }
    function closeSheet(fromPop){
        if(sheet.hidden){ return; }
        if(!fromPop && history.state && history.state.mfSheet){ back(); }
        sheet.hidden = true;
        body.classList.remove("sheet-open");
        behind().forEach((el)=>{ el.inert = false; });
        if(removing && !busy){ removing = false; selected.clear(); }    // #102: Remove was cancelled
        delBtn.focus();
    }
    delBtn.addEventListener("click", ()=>{ if(selected.size && !busy){ openSheet(); } });
    $("confirmCancel").addEventListener("click", ()=> closeSheet(false));
    sheet.addEventListener("click", (e)=>{ if(e.target === sheet){ closeSheet(false); } });   // scrim tap
    document.addEventListener("keydown", (e)=>{
        if(e.key === "Escape" && !sheet.hidden){ e.preventDefault(); closeSheet(false); }
    });

    // ----- delete -----
    async function post(ids){
        const r = await fetch("/api/v1/media/delete", {method: "POST", credentials: "same-origin",
            headers: {"Content-Type": "application/json", "X-CSRF-Token": CSRF}, body: JSON.stringify({ids: ids})});
        if(r.status === 401){ location.href = "/pair"; }
        if(!r.ok){ throw new Error("HTTP " + r.status); }
        return r.json();
    }
    $("confirmDelete").addEventListener("click", async ()=>{
        if(busy){ return; }
        const ids = [...selected].map(Number);
        closeSheet(false);
        busy = true;                                       // no double send while the request runs
        delBtn.disabled = true; $("selCancel").disabled = true;
        err.hidden = true; fit();
        const gone = [];
        let ok = true, offline = false;
        try{
            for(let i = 0; i < ids.length; i += BULK){     // one request unless more than 1000 are selected
                const res = await post(ids.slice(i, i + BULK));
                gone.push(...res.deleted, ...res.missing); // missing = already deleted elsewhere: gone too
                if(res.failed.length){ ok = false; }
            }
        }catch(e){
            ok = false;
            // fetch rejects with TypeError when the frame can't be reached: show the shared offline view
            // (the selection stays; Try again restores the page). HTTP errors keep the generic message.
            if(e instanceof TypeError && window.memoryFramePhone){ offline = true; window.memoryFramePhone.offline(); }
        }
        const fading = [];
        gone.forEach((id)=>{
            const li = grid.querySelector('li[data-id="' + Number(id) + '"]');
            if(!li){ return; }
            selected.delete(String(id));
            li.classList.add("gone");                       // 150 ms opacity fade, then remove
            fading.push(li);
        });
        setTimeout(()=>{
            fading.forEach((li)=> li.remove());
            const left = tiles().length;
            $("count").textContent = countText(left);
            if(removing){ removing = false; selected.clear(); }
            tiles().forEach((t, i)=> t.setAttribute("aria-label", t.closest('li[data-kind="video"]')
                ? t.getAttribute("aria-label").replace(/^Video \d+ of \d+/, "Video " + (i + 1) + " of " + left)
                : "Photo " + (i + 1) + " of " + left));   // no stale numbers
            if(!left && !grid.querySelector("li")){ $("selectBtn").hidden = true; grid.hidden = true; $("emptyLib").hidden = false; }   // nothing left
            busy = false; $("selCancel").disabled = false;
            if(ok){
                exitSelect(false);
                if(!left){ $("upBtn").focus(); }            // everything is gone: the Select button is hidden, so offer Upload
            }
            else {                                          // the rest stays selected; say what went wrong
                const atBottom = !offline && innerHeight + scrollY >= document.body.scrollHeight - 2;   // polish (Wren N7)
                update(); err.hidden = false; fit();       // the error sits in the fixed toolbar: always on screen
                if(atBottom){ window.scrollTo(0, document.body.scrollHeight); }   // the taller toolbar must not cover the last row
                if(!offline){ delBtn.focus(); }
            }
        }, fading.length ? 160 : 0);
    });

    // ----- #102: videos converting on the frame -----
    // Retry re-queues a failed video; Remove asks on the same sheet as Delete (09c rules) for that one video.
    let removing = false;
    async function videoAction(btn){
        const id = btn.dataset.id;
        if(busy){ return; }
        if(btn.classList.contains("vremove")){
            selected.clear(); selected.add(String(id)); removing = true;
            openSheet();
            return;
        }
        try{
            const r = await fetch("/api/v1/media/" + Number(id) + "/retry", {method: "POST", credentials: "same-origin",
                headers: {"X-CSRF-Token": CSRF}});
            if(r.status === 401){ location.href = "/pair"; return; }
            if(r.ok){ setVideo(btn.closest("li"), {id: Number(id), state: "queued"}); poll(); }
        }catch(e){ if(e instanceof TypeError && window.memoryFramePhone){ window.memoryFramePhone.offline(); } }
    }
    function mmss(ms){ const s = Math.round((ms || 0) / 1000); return Math.floor(s / 60) + ":" + String(s % 60).padStart(2, "0"); }
    function el(tag, cls, text){ const e = document.createElement(tag); if(cls){ e.className = cls; } if(text){ e.textContent = text; } return e; }
    function setVideo(li, m){               // rebuild one video tile for its new state (DOM only, no innerHTML)
        li.dataset.state = m.state;
        let t;
        if(m.state === "ready"){
            t = el("button", "tile"); t.type = "button";
            t.setAttribute("aria-label", "Video, " + mmss(m.duration_ms));
            const img = new Image(240, 240); img.alt = ""; img.decoding = "async"; img.src = "/api/v1/media/" + m.id + "/thumb";
            img.addEventListener("error", ()=> plain(img), {once: true});
            const b = el("span", "badge", "▶ " + mmss(m.duration_ms)); b.setAttribute("aria-hidden", "true");
            t.append(img, b);
            if(selecting){ t.setAttribute("aria-pressed", "false"); }
        } else if(m.state === "failed"){
            t = el("div", "tile vtile");
            const st = el("div", "state fail"); st.append(el("span", "", "Couldn't add"));
            const acts = el("div", "acts");
            const r = el("button", "vretry", "Retry"), x = el("button", "vremove", "Remove");
            r.type = x.type = "button"; r.dataset.id = x.dataset.id = m.id;
            acts.append(r, x); st.append(acts); t.append(st);
        } else {
            t = el("div", "tile vtile"); t.setAttribute("role", "img");
            t.setAttribute("aria-label", m.state === "processing" ? "Video preparing" : "Video waiting");
            const st = el("div", "state");
            if(m.state === "processing"){ const sp = el("span", "spin"); sp.setAttribute("aria-hidden", "true"); st.append(sp); }
            st.append(document.createTextNode(m.state === "processing" ? "Preparing" : "Waiting"));
            t.append(st);
        }
        li.replaceChildren(t);
    }
    // While any video is waiting or preparing, ask the frame every 5 s; a ready video appears without a reload.
    let pollTimer = null;
    function pending(){ return grid.querySelector('li[data-state="queued"], li[data-state="processing"]'); }
    function poll(){
        if(pollTimer || !pending()){ return; }
        pollTimer = setTimeout(async ()=>{
            pollTimer = null;
            try{
                const r = await fetch("/api/v1/media", {credentials: "same-origin"});
                if(r.ok){
                    const list = await r.json();
                    list.forEach((m)=>{
                        if(m.kind !== "video"){ return; }
                        const li = grid.querySelector('li[data-id="' + Number(m.id) + '"]');
                        if(li && li.dataset.state !== m.state){ setVideo(li, m); }
                    });
                    if(selecting){ update(); }
                }
            }catch(e){ /* offline: try again on the next tick */ }
            poll();
        }, 5000);
    }
    poll();
})();
