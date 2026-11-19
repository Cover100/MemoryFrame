// #162 error screen served by the app (/frame/error?code=E1|E3). A tap does nothing. A 3 s hold anywhere (same
// constants as pairing.js: nothing for 1 s, then the brass ring fills over 2 s; lifting or moving >= 20 px cancels)
// opens Wren's Frame tools with only "Join developer Wi-Fi…" and Close (the default). Join posts /frame/dev-mode once
// with the frame token and shows the plain #167 title and sentence if it fails.
// Auto-retry: /healthz every 5 s; back to the slideshow (/?boot=1) when the app comes back after being down, and a
// forced retry every 30 s while it answers (not while Frame tools is open or a switch is running). No reboot.
(function(){
    "use strict";
    const $ = (id)=>document.getElementById(id);
    const HOLD_MS = 3000, RING_DELAY = 1000, MOVE = 20;
    const q = new URLSearchParams(location.search);
    const num = (k, d)=>{ const v = Number(q.get(k)); return Number.isFinite(v) && v > 0 ? v : d; };
    const POLL = num("poll", 5000), RETRY = num("retry", 30000);           // test overrides (ms)
    const ring = $("devRing"), tools = $("toolsOverlay");
    let hold = null, busy = false, posts = 0;
    window.mfErrorPage = {posts: ()=>posts};

    function stop(){
        if(!hold){ return; }
        clearTimeout(hold.ringT); clearTimeout(hold.doneT);
        ring.hidden = true; ring.classList.remove("filling");
        hold = null;
    }
    function openTools(){
        $("toolsMenu").hidden = false; $("toolsStatus").hidden = true;
        tools.classList.add("visible");
        $("toolsClose").focus();
    }
    function closeTools(){ if(!busy){ tools.classList.remove("visible"); } }
    window.addEventListener("pointerdown", (e)=>{
        if(tools.classList.contains("visible")){ return; }
        stop();
        const h = hold = {x: e.clientX, y: e.clientY};
        h.ringT = setTimeout(()=>{
            ring.style.left = h.x + "px"; ring.style.top = h.y + "px";
            ring.classList.remove("filling"); ring.hidden = false;
            void ring.offsetWidth; ring.classList.add("filling");
        }, RING_DELAY);
        h.doneT = setTimeout(()=>{ stop(); openTools(); }, HOLD_MS);
    }, true);
    window.addEventListener("pointermove", (e)=>{
        if(hold && Math.hypot(e.clientX - hold.x, e.clientY - hold.y) >= MOVE){ stop(); }
    }, true);
    for(const t of ["pointerup", "pointercancel"]){ window.addEventListener(t, stop, true); }
    window.addEventListener("contextmenu", (e)=>e.preventDefault());

    function button(id, fn){
        $(id).addEventListener("click", (e)=>{ e.stopPropagation(); fn(); });
        $(id).addEventListener("pointerdown", (e)=>e.stopPropagation());
    }
    button("toolsClose", closeTools);
    button("devModeOk", closeTools);
    button("toolsDev", async ()=>{
        if(busy){ return; }
        busy = true;
        $("toolsMenu").hidden = true; $("toolsStatus").hidden = false; $("devModeAfter").hidden = true;
        $("devModeStatus").textContent = "Switching to dev mode\u2026";
        $("devModeDetail").textContent = "This can take up to 30 seconds.";
        let res = null;
        try{
            const meta = document.querySelector('meta[name="mf-frame-token"]');
            posts += 1;
            const r = await fetch("/frame/dev-mode", {method: "POST",
                headers: {"X-Frame-Token": meta ? meta.content : ""}});
            try{ res = await r.json(); }catch(err){ console.warn("dev mode: HTTP " + r.status); }
        }catch(err){ console.warn(err); }
        busy = false;
        if(res && res.ok === true){                  // as in the app: the menu closes; the screen stays until recovery
            tools.classList.remove("visible");
            return;
        }
        // #167 (Wren): the server's plain title and sentence (memoryframe/display.py DEV_WIFI_FAIL, as in
        // static/pairing.js); with no usable answer, its "didn't connect" text. Never a raw reason.
        const plain = (v)=> typeof v === "string" && /^[A-Z]/.test(v) ? v : "";
        $("devModeStatus").textContent = plain(res && res.title) || "Developer Wi-Fi didn't connect";
        $("devModeDetail").textContent = plain(res && res.reason) ||
            "The frame is back on its own Wi-Fi. Phones can connect as usual.";
        $("devModeAfter").hidden = false;
        $("devModeOk").focus();
    });

    // ---- auto-retry ----
    let wasDown = false, lastTry = Date.now(), leaving = false;
    function back(){ leaving = true; location.replace("/?boot=1"); }
    async function poll(){
        if(leaving){ return; }
        let up = false;
        const ac = new AbortController(), t = setTimeout(()=>ac.abort(), 3000);
        try{ up = (await fetch("/healthz", {cache: "no-store", signal: ac.signal})).ok; }catch(err){ up = false; }
        clearTimeout(t);
        const quiet = !tools.classList.contains("visible") && !busy && !hold;
        if(up && quiet && (wasDown || Date.now() - lastTry >= RETRY)){ back(); return; }
        if(!up){ wasDown = true; }
        setTimeout(poll, POLL);
    }
    setTimeout(poll, POLL);
})();
