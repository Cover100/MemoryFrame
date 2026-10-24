// MF-12 + MF-19 + MF-34: on-device onboarding and the menu (double tap).
// - No photos and no phone paired: full onboarding (Wi-Fi join QR on the hotspot -> site QR + code).
// - Photos but no phone paired: the slideshow runs; only a small "Double-tap for menu" hint.
// - Paired but no photos: "No photos yet" screen.
// - Pairing is always available from the menu ("Pair new device").
// The site URL/QR follow the frame's current network (hotspot 10.42.0.1 or home Wi-Fi .local + IP).
// - Double tap opens the menu (IP, SSID, free storage, version, pair new device, sign out all).
//   A tap: down->up < 250 ms, moved < 20 px. Double: 2nd down <= 300 ms after the 1st up, within
//   40 px. Primary pointer only; multi-touch resets. Ignored while an overlay is open.
//   A still 3 s hold no longer opens the menu: it is the hidden Developer Wi-Fi confirm (MF-51).
// Only localhost routes are used (/pairing/*, /frame/status).
(function(){
    const POLL = 2000;
    const $ = (id)=>document.getElementById(id);

    const pairOverlay = $("pairOverlay"), emptyOverlay = $("emptyOverlay"), menuOverlay = $("menuOverlay");

    let onDemand = false;     // "Pair new device" chosen in the menu
    let onDemandRound = false;  // its one code has been started (no auto-renew)
    const SUCCESS_MS = 3000;
    let successUntil = 0;
    // Close dismisses the onboarding / empty-library screens for this browser session
    // (kiosk restarts and reboots start a new session). Pairing stays in the menu.
    let dismissed = sessionStorage.getItem("mfOverlayDismissed") === "1";

    function dismiss(){
        dismissed = true;
        onDemand = false;
        sessionStorage.setItem("mfOverlayDismissed", "1");
        tick();
    }
    let paired = 0;
    let starting = false;

    async function getJSON(url, opts){
        const r = await fetch(url, Object.assign({cache: "no-store"}, opts || {}));
        if(!r.ok){ throw new Error(url + " " + r.status); }
        return r.json();
    }

    async function startRound(){
        if(starting){ return null; }
        starting = true;
        try{ return await getJSON("/pairing/start", {method: "POST"}); }
        catch(e){ return null; }
        finally{ starting = false; }
    }

    // MF-24: never show "null"/"undefined" as an address; fall back per network.
    function okUrl(u){
        return typeof u === "string" && /^https?:\/\/[^\/\s]+/.test(u)
            && !/^https?:\/\/(null|none|undefined|nan)?([:\/]|$)/i.test(u);
    }
    function fallbackBase(st){
        return st && st.network === "hotspot" ? "http://10.42.0.1/" : "http://memoryframe.local/";
    }

    function render(st){
        paired = st.paired;
        const devTemp = st.dev_temp === true;            // MF-55 G3: no QR, code or address
        $("pairDevTemp").hidden = !devTemp;
        $("pairBox").classList.toggle("mf-devtemp", devTemp);
        if(devTemp){
            for(const id of ["pairUrl", "pairUrlAlt", "pairSsid", "pairCode"]){ $(id).textContent = ""; }
            $("pairQr").innerHTML = ""; $("wifiQr").innerHTML = "";
            return;
        }
        const base = fallbackBase(st);
        $("pairUrl").textContent = okUrl(st.url) ? st.url : base + "pair";
        $("pairUrlAlt").textContent = okUrl(st.url_fallback) ? "or " + st.url_fallback : "";
        $("emptyUrl").textContent = okUrl(st.site_url) ? st.site_url : base;
        $("pairSsid").textContent = st.ssid || "";
        const qr = !!st.wifi_qr_svg;
        const join = !qr && typeof st.wifi_join_name === "string" && st.wifi_join_name !== "";   // #89: no known password
        const wifi = qr || join;
        $("wifiStep").hidden = !wifi;
        $("wifiQr").hidden = !qr; $("wifiNameLine").hidden = !qr; $("wifiJoin").hidden = !join;
        $("wifiJoinName").textContent = join ? st.wifi_join_name : "";
        if(qr && typeof st.wifi_qr_svg === "string"){ $("wifiQr").innerHTML = st.wifi_qr_svg; }     // SVG from our own server
        else { $("wifiQr").innerHTML = ""; }
        $("siteBadge").textContent = wifi ? "2" : "1";      // #70 03/04: numbered brass badges
        $("codeBadge").textContent = wifi ? "3" : "2";
        $("pairTitle").textContent = wifi ? "Pair a phone" : "Pair your phone";            // mock 03 / mock 04
        $("siteTitle").textContent = wifi ? "Open site" : "Scan with your phone";
        if(st.round && typeof st.round.qr_svg === "string" && typeof st.round.code === "string"){
            $("pairCode").textContent = st.round.code.slice(0, 3) + " " + st.round.code.slice(3);
            $("pairQr").innerHTML = st.round.qr_svg;
        }
        $("pairClose").hidden = false;              // always dismissable
    }

    // ---------- MF-36: network mode (dev banner, fallback toast, hotspot error) ----------
    const ERROR_SNOOZE = 10 * 60 * 1000;
    const TOAST_MS = 30 * 1000;
    let errorSnoozedUntil = 0;
    let toastTimer = null;

    function hideToast(){
        const t = $("devToast");
        if(t.hidden){ return; }
        t.classList.add("fading");
        setTimeout(()=>{ t.hidden = true; t.classList.remove("fading"); }, 600);
    }

    function renderNetmode(nm){
        nm = nm || {};
        const banner = $("devBanner");
        banner.hidden = !nm.dev;
        if(nm.dev){
            const ip = typeof nm.lan_ip === "string" && /^[0-9.]+$/.test(nm.lan_ip) ? nm.lan_ip : null;
            banner.textContent = nm.temp ? "Developer Wi-Fi \u00b7 " + (ip || "no network") + " \u00b7 restart to exit"
                                         : "Developer mode \u00b7 " + (ip || "no network");
        }
        if(nm.dev_fallback){
            const key = "mf-devtoast-" + (nm.boot_id || "boot");
            let seen = false;
            try{ seen = localStorage.getItem(key) === "1"; }catch(e){}
            if(!seen){                                       // once per boot
                try{ localStorage.setItem(key, "1"); }catch(e){}
                $("devToast").hidden = false;
                clearTimeout(toastTimer);
                toastTimer = setTimeout(hideToast, TOAST_MS);
            }
        }
        $("hotspotError").hidden = !(nm.hotspot_error && Date.now() >= errorSnoozedUntil);
    }

    $("devToast").addEventListener("click", (e)=>{ e.stopPropagation(); hideToast(); });
    $("devToast").addEventListener("pointerdown", (e)=> e.stopPropagation());
    $("hotspotError").addEventListener("pointerdown", (e)=> e.stopPropagation());
    $("hotspotErrorClose").addEventListener("click", (e)=>{
        e.stopPropagation();
        errorSnoozedUntil = Date.now() + ERROR_SNOOZE;      // reappears while the failure persists
        $("hotspotError").hidden = true;
    });

    // ---------- MF-18: hotspot change ----------
    const CANCELLED_TIMEOUT = 60 * 1000;
    let wifiDeadline = 0, cancelledClosedId = null, cancelledShownAt = 0, cancelledId = null;
    function mmss(ms){
        const s = Math.max(0, Math.ceil(ms / 1000));
        return Math.floor(s / 60) + ":" + String(s % 60).padStart(2, "0");
    }
    function wifiRejoinActive(){ return !$("wifiRejoin").hidden; }
    function renderWifi(wc){
        const pending = !!(wc && wc.state === "pending");
        $("wifiRejoin").hidden = !pending;
        if(pending){
            $("wifiRejoinName").textContent = wc.new_ssid || "";
            if(typeof wc.qr_svg === "string"){ $("wifiRejoinQr").innerHTML = wc.qr_svg; }
            wifiDeadline = Date.now() + (Number(wc.remaining) || 0) * 1000;    // server deadline
            tickWifi();
            closeMenu();
        }
        $("wifiUpdated").hidden = !(wc && wc.state === "committed");
        if(wc && wc.state === "committed"){ $("wifiUpdatedName").textContent = wc.new_ssid || ""; }
        const rolled = !!(wc && wc.state === "rolled_back" && wc.id !== cancelledClosedId);
        if(rolled && cancelledId !== wc.id){ cancelledId = wc.id; cancelledShownAt = Date.now(); }
        const showCancelled = rolled && Date.now() - cancelledShownAt < CANCELLED_TIMEOUT;
        $("wifiCancelled").hidden = !showCancelled;
        if(showCancelled){
            $("wifiCancelledName").textContent = wc.old_ssid || "";
            if(typeof wc.qr_svg === "string"){ $("wifiCancelledQr").innerHTML = wc.qr_svg; }
        }
    }
    function tickWifi(){
        const left = wifiDeadline - Date.now();
        $("wifiCountdown").textContent = mmss(left);
        $("wifiBar").style.transform = "scaleX(" + Math.min(1, Math.max(0, left / 300000)).toFixed(3) + ")";   // 5 min window
    }
    setInterval(()=>{ if(wifiRejoinActive()){ tickWifi(); } }, 250);
    function closeCancelled(){ cancelledClosedId = cancelledId; $("wifiCancelled").hidden = true; }
    for(const id of ["wifiRejoin", "wifiUpdated"]){
        $(id).addEventListener("pointerdown", (e)=> e.stopPropagation());     // not dismissable, no menu
        $(id).addEventListener("click", (e)=> e.stopPropagation());
    }
    $("wifiCancelled").addEventListener("pointerdown", (e)=> e.stopPropagation());
    $("wifiCancelled").addEventListener("click", (e)=>{
        e.stopPropagation();
        if(e.target.id === "wifiCancelled" || e.target.id === "wifiCancelledClose"){ closeCancelled(); }   // outside tap / ×
    });

    async function tick(){
        try{
            let st = await getJSON("/pairing/state");
            renderNetmode(st.netmode);
            renderWifi(st.wifi_change);
            const firstRun = st.paired === 0 && st.media === 0 && !dismissed;
            const wasPairing = pairOverlay.classList.contains("visible") && Date.now() >= successUntil;
            if(onDemand && onDemandRound && !st.round){
                onDemand = false;                    // MF-32: on-demand code expired -> close, no renewal
            }
            const pairing = firstRun || onDemand;
            if(pairing && !st.round && (firstRun || !onDemandRound)){
                st = (await startRound()) || st;      // first-run renews; on demand: once
                if(onDemand && st.round){ onDemandRound = true; }
            }
            if(st.paired > paired && wasPairing){
                successUntil = Date.now() + SUCCESS_MS;   // MF-33: "Phone paired" for 3 s
                onDemand = false;
            }
            render(st);
            const success = Date.now() < successUntil;
            $("pairSuccess").hidden = !success;
            $("pairBox").hidden = success;
            if(success){                              // nothing of the used code behind the card
                $("pairCode").textContent = "";
                $("pairQr").innerHTML = "";
            }
            if(success){ setTimeout(tick, Math.max(50, successUntil - Date.now() + 20)); }
            const showPair = success || (st.paired === 0 && st.media === 0 && !dismissed) || onDemand;
            const showEmpty = !showPair && !dismissed && st.paired > 0 && st.media === 0;
            pairOverlay.classList.toggle("visible", showPair);
            pairOverlay.classList.toggle("toast", success);   // #70: top-centre toast over the photo
            if(showEmpty && !emptyOverlay.classList.contains("visible")){ openedAt.set(emptyOverlay, Date.now()); }
            emptyOverlay.classList.toggle("visible", showEmpty);
            // Small corner hint whenever pairing is possible but nothing covers the screen.
            $("pairHint").classList.toggle("visible", !showPair && !showEmpty && st.paired === 0);
        }catch(e){
            console.warn("pairing state unavailable", e);
        }
    }

    function gb(n){ return (n / 1073741824).toFixed(1) + " GB"; }

    async function openMenu(){
        if(wifiRejoinActive()){ return; }            // MF-18: the rejoin screen ignores the menu
        showOverlay(menuOverlay);
        try{
            const s = await getJSON("/frame/status");
            const free = s.storage.free, total = s.storage.total;   // menu v2: display-only meter
            const f = $("menuFree"), of = document.createElement("span");
            f.textContent = gb(free) + " free ";
            of.textContent = "of " + gb(total);
            f.appendChild(of);
            const used = total > 0 ? Math.min(1, Math.max(0, 1 - free / total)) : 0;
            $("menuUsed").style.transform = "scaleX(" + used.toFixed(3) + ")";
            $("menuMeter").setAttribute("aria-valuenow", String(Math.round(used * 100)));
            $("menuFull").hidden = s.storage.almost_full !== true;      // #96: below the upload reserve
        }catch(e){
            console.warn("frame status unavailable", e);
        }
    }

    function closeMenu(){ menuOverlay.classList.remove("visible"); }

    function button(id, fn){
        $(id).addEventListener("click", (e)=>{ e.stopPropagation(); fn(); });
        $(id).addEventListener("pointerdown", (e)=> e.stopPropagation());
    }
    button("menuClose", closeMenu);

    // ---------- MF-51: Dev mode until reboot ----------
    const devOverlay = $("devModeOverlay");
    let devBusy = false;
    function openDevConfirm(){
        $("devModeAsk").hidden = false; $("devModeDone").hidden = true; $("devModeError").hidden = true;
        devOverlay.classList.add("visible");
        $("devModeCancel").focus();                     // Cancel is the default
    }

    // Hidden Developer Wi-Fi: a 3 s hold ANYWHERE (Tester #52). Nothing for 1 s, then a faint ring
    // fills around the finger over 2 s; at 3 s (moved < 20 px) the confirm opens (Cancel default).
    // Lifting early or moving >= 20 px (swipe, brightness drag) cancels silently. Events are not
    // swallowed, so swipes, brightness and double tap work as before. Only double tap opens the menu.
    const CORNER_MS = 3000, RING_DELAY = 1000, CORNER_MOVE = 20;
    const ring = $("devRing");
    let corner = null;
    function cornerStop(){
        if(!corner){ return; }
        clearTimeout(corner.ringT); clearTimeout(corner.doneT);
        ring.hidden = true; ring.classList.remove("filling");
        corner = null;
    }
    window.addEventListener("pointerdown", (e)=>{
        if(window.mfShuttingDown || overlayOpen() || devOverlay.classList.contains("visible")){ return; }
        cornerStop();
        const c = corner = {id: e.pointerId, x: e.clientX, y: e.clientY};
        c.ringT = setTimeout(()=>{
            ring.style.left = c.x + "px"; ring.style.top = c.y + "px";
            ring.classList.remove("filling"); ring.hidden = false;
            void ring.offsetWidth; ring.classList.add("filling");
        }, RING_DELAY);
        c.doneT = setTimeout(()=>{ cornerStop(); if(!window.mfBrightnessDragging){ openDevConfirm(); } }, CORNER_MS);
    }, true);
    window.addEventListener("pointermove", (e)=>{
        if(!corner || e.pointerId !== corner.id){ return; }
        if(window.mfBrightnessDragging || Math.hypot(e.clientX - corner.x, e.clientY - corner.y) >= CORNER_MOVE){ cornerStop(); }
    }, true);
    for(const t of ["pointerup", "pointercancel"]){
        window.addEventListener(t, (e)=>{
            if(!corner || e.pointerId !== corner.id){ return; }
            cornerStop();
        }, true);
    }
    button("devModeCancel", ()=>{ if(!devBusy){ devOverlay.classList.remove("visible"); } });
    button("devModeConfirm", async ()=>{
        if(devBusy){ return; }
        devBusy = true;
        $("devModeAsk").hidden = true; $("devModeError").hidden = true; $("devModeDone").hidden = false;
        $("devModeStatus").textContent = "Switching to dev mode\u2026";
        $("devModeDetail").textContent = "This can take up to 30 seconds.";
        $("devModeAfter").hidden = true;
        let res = {ok: false, reason: "no answer from the frame"};
        try{
            const meta = document.querySelector('meta[name="mf-frame-token"]');
            const r = await fetch("/frame/dev-mode", {method: "POST",
                headers: {"X-Frame-Token": meta ? meta.content : ""}});
            try{ res = await r.json(); }catch(e){ res = {ok: false, reason: "HTTP " + r.status}; }
        }catch(e){ console.warn(e); }
        if(res && res.ok === true){
            devBusy = false;
            devOverlay.classList.remove("visible");       // the dev label shows the new state
            tick();
            return;
        }
        $("devModeStatus").textContent = "Couldn't join home Wi-Fi \u2014 hotspot kept on";
        $("devModeDetail").textContent = (res && typeof res.reason === "string" && res.reason) ? res.reason : "unknown error";
        $("devModeAfter").hidden = false;
        devBusy = false;
    });
    button("devModeOk", ()=>{ devOverlay.classList.remove("visible"); });


    // ---------- MF-49: shut down ----------
    const shutdownOverlay = $("shutdownOverlay");
    let shuttingDown = false;
    button("menuShutdown", ()=>{
        closeMenu();
        $("shutdownAsk").hidden = false; $("shutdownError").hidden = true;
        shutdownOverlay.classList.add("visible");
    });
    button("shutdownCancel", ()=>{ if(!shuttingDown){ shutdownOverlay.classList.remove("visible"); } });
    button("shutdownConfirm", async ()=>{
        if(shuttingDown){ return; }
        shuttingDown = true;
        const cover = $("shuttingDown");
        shutdownOverlay.classList.remove("visible");
        cover.hidden = false;                              // immediately, covers everything
        window.mfShuttingDown = true;
        const meta = document.querySelector('meta[name="mf-frame-token"]');
        let ok = false;
        try{
            const r = await fetch("/frame/shutdown", {method: "POST",
                headers: {"X-Frame-Token": meta ? meta.content : ""}});
            ok = r.ok && (await r.json()).synced === true;     // only after the server's sync
        }catch(e){ console.warn(e); }
        if(ok){
            $("safeScreen").hidden = false;                  // final frame; poweroff follows ~1.2 s later
            document.body.classList.add("mf-safe-final");    // mock 14: nothing animates under it
            cover.hidden = true;
        }else{
            shuttingDown = false; window.mfShuttingDown = false; cover.hidden = true;
            $("shutdownAsk").hidden = false; $("shutdownError").hidden = false;
            shutdownOverlay.classList.add("visible");
        }
    });
    // taps on the cover are ignored (no menu, no brightness, no swipe)
    for(const t of ["pointerdown", "pointermove", "pointerup", "click"]){
        window.addEventListener(t, (e)=>{
            if(window.mfShuttingDown){ e.stopImmediatePropagation(); e.preventDefault(); }
        }, true);
    }
    // #62: if the poweroff call itself fails (e.g. polkit), don't leave the cover up: "Couldn't shut down".
    setInterval(async ()=>{
        if(!window.mfShuttingDown){ return; }
        let failed = false;
        try{ failed = (await (await fetch("/frame/shutdown-status")).json()).failed === true; }catch(e){ return; }
        if(!failed){ return; }
        shuttingDown = false; window.mfShuttingDown = false;
        for(const id of ["shuttingDown", "safeScreen"]){ const el = $(id); if(el){ el.hidden = true; } }
        document.body.classList.remove("mf-safe-final");
        $("shutdownAsk").hidden = false; $("shutdownError").hidden = false;
        shutdownOverlay.classList.add("visible");
    }, 1000);

    button("menuPair", ()=>{ closeMenu(); onDemand = true; onDemandRound = false; tick(); });
    // ---------- MF-31: confirm "Sign out all phones" ----------
    const confirmOverlay = $("confirmOverlay");
    let confirmBusy = false;
    function openConfirm(){
        closeMenu();
        $("confirmAsk").hidden = false;
        $("confirmDone").hidden = true;
        $("confirmError").hidden = true;
        showOverlay(confirmOverlay);
    }
    function closeConfirm(){ if(!confirmBusy){ confirmOverlay.classList.remove("visible"); } }
    button("menuSignOut", openConfirm);
    button("confirmCancel", closeConfirm);
    button("confirmSignOut", async ()=>{
        if(confirmBusy){ return; }
        confirmBusy = true;
        let ok = false;
        try{ ok = (await fetch("/pairing/sign-out-all", {method: "POST"})).ok; }catch(e){ console.warn(e); }
        if(!ok){                                     // phones are still paired: say so, keep the dialog
            confirmBusy = false;
            $("confirmError").hidden = false;
            return;
        }
        $("confirmError").hidden = true;
        $("confirmAsk").hidden = true;
        $("confirmDone").hidden = false;
        setTimeout(()=>{ confirmBusy = false; confirmOverlay.classList.remove("visible"); tick(); }, 2000);
    });

    // ---------- MF-32: outside tap and 60 s idle close (menu, confirm, empty library) ----------
    let IDLE_CLOSE = 60 * 1000;
    let lastTouch = Date.now();
    const openedAt = new Map();
    function showOverlay(o){ o.classList.add("visible"); openedAt.set(o, Date.now()); }
    const closers = new Map([[menuOverlay, closeMenu], [confirmOverlay, closeConfirm], [emptyOverlay, dismiss]]);
    document.addEventListener("pointerdown", ()=>{ lastTouch = Date.now(); }, true);
    for(const [o, close] of closers){
        let downOutside = false;
        o.addEventListener("pointerdown", (e)=>{ downOutside = e.target === o; });
        o.addEventListener("pointerup", (e)=>{
            if(downOutside && e.target === o){ e.stopPropagation(); close(); }
            downOutside = false;
        });
    }
    setInterval(()=>{
        for(const [o, close] of closers){
            if(!o.classList.contains("visible")){ continue; }
            const since = Math.max(lastTouch, openedAt.get(o) || 0);
            if(Date.now() - since >= IDLE_CLOSE){ close(); }
        }
    }, 1000);
    button("pairClose", dismiss);
    button("emptyClose", dismiss);

    // ---------- MF-34: double tap ----------
    const TAP_MAX_MS = 250, TAP_MOVE = 20, DOUBLE_GAP_MS = 300, DOUBLE_DIST = 40;
    let tapDown = null;        // {x, y, t} of the current press, if it can still be a tap
    let lastTap = null;        // {x, y, t (pointerup time)} of the previous completed tap
    let pointers = 0;
    function overlayOpen(){
        return [menuOverlay, pairOverlay, emptyOverlay, $("confirmOverlay")].some((o)=> o && o.classList.contains("visible"))
            || !!document.querySelector(".mf-modal:not([hidden])");
    }
    document.addEventListener("pointerdown", (e)=>{
        console.debug("pointerdown pointerType=" + e.pointerType);   // on-device gesture debugging
        pointers += 1;
        if(!e.isPrimary || pointers > 1){ tapDown = null; lastTap = null; return; }   // multi-touch resets
        const now = performance.now();
        if(lastTap && (now - lastTap.t > DOUBLE_GAP_MS
                       || Math.hypot(e.clientX - lastTap.x, e.clientY - lastTap.y) > DOUBLE_DIST)){
            lastTap = null;
        }
        tapDown = {x: e.clientX, y: e.clientY, t: now};
    }, true);
    document.addEventListener("pointermove", (e)=>{
        if(tapDown && e.isPrimary && Math.hypot(e.clientX - tapDown.x, e.clientY - tapDown.y) >= TAP_MOVE){
            tapDown = null; lastTap = null;                  // a swipe or drag, not a tap
        }
    }, true);
    function pointerEnd(e, cancelled){
        pointers = Math.max(0, pointers - 1);
        const d = tapDown;
        tapDown = null;
        // touch, mouse and pen alike (the labwc/Wayland panel reports taps as "mouse"; on-device #46 fail)
        if(cancelled || !d || !e.isPrimary){ lastTap = null; return; }
        const now = performance.now();
        const isTap = now - d.t < TAP_MAX_MS && Math.hypot(e.clientX - d.x, e.clientY - d.y) < TAP_MOVE;
        if(!isTap){ lastTap = null; return; }
        if(lastTap){
            lastTap = null;
            if(!overlayOpen()){ openMenu(); }
            return;
        }
        lastTap = {x: e.clientX, y: e.clientY, t: now};
    }
    document.addEventListener("pointerup", (e)=> pointerEnd(e, false), true);
    document.addEventListener("pointercancel", (e)=> pointerEnd(e, true), true);


    window.memoryFramePairing = {tick: tick, openMenu: openMenu, snoozeMs: ERROR_SNOOZE, toastMs: TOAST_MS,
        idleCloseMs: ()=> IDLE_CLOSE, setIdleCloseForTests: (ms)=>{ IDLE_CLOSE = ms; }};
    tick();
    setInterval(tick, POLL);
})();
