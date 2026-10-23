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
    const tiles = ()=> grid.querySelectorAll(".tile");
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
    function countText(n){ return n + " photo" + (n === 1 ? "" : "s") + " on the frame"; }

    // Tap: start selecting with this photo, or toggle it. Long press does the same sooner (with a buzz).
    let press = null, longFired = false;
    grid.addEventListener("click", (e)=>{
        const t = e.target.closest(".tile");
        if(!t || busy){ return; }
        if(longFired){ longFired = false; return; }       // the long press already handled this touch
        if(!selecting){ enterSelect(t); return; }
        setPressed(t, t.getAttribute("aria-pressed") !== "true");
        update();
    });
    grid.addEventListener("pointerdown", (e)=>{
        const t = e.target.closest(".tile");
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
        $("confirmTitle").textContent = n === 1 ? "Delete this photo?" : "Delete " + n + " photos?";
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
            tiles().forEach((t, i)=> t.setAttribute("aria-label", "Photo " + (i + 1) + " of " + left));   // no stale numbers
            if(!left){ $("selectBtn").hidden = true; grid.hidden = true; $("emptyLib").hidden = false; }   // nothing left
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
})();
